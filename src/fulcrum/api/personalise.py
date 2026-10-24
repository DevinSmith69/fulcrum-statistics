"""Server-side personalisation (API_CONTRACT §3): persona / language / club / player filtering.

The client never receives other personas' text: insights are cut down to one variant, cues are filtered,
and missing languages are translated on demand (see `translation.py`).
"""

from __future__ import annotations

from fulcrum.contracts import (
    EventType,
    Insight,
    InsightVariant,
    MatchEvent,
    OverlayAnchor,
    OverlayCue,
    OverlayStat,
    OverlayTemplate,
    Persona,
    Recap,
    ViewerPrefs,
)

from .readmodel import MatchReadModel
from .translation import Translator

E = EventType
PLAYER_TAG_ACTIONS: dict[str, str] = {
    E.PASS: "on the ball",
    E.CARRY: "drives forward",
    E.DRIBBLE: "takes on a defender",
    E.SHOT: "shoots",
    E.GOAL: "scores",
    E.TACKLE: "wins it back",
    E.INTERCEPTION: "reads it and intercepts",
    E.BALL_RECOVERY: "recovers the ball",
    E.SAVE: "makes the save",
}
PLAYER_TAG_MIN_GAP_MS = 20_000  # sim time between player_tag cues for one viewer
PLAYER_TAG_DURATION_MS = 4_000


def pick_variants(variants: list[InsightVariant], prefs: ViewerPrefs) -> list[InsightVariant]:
    """Variants for the viewer's persona (any language), best perspective match first.

    Falls back to the casual persona, then to anything, so a viewer always gets *something*.
    """
    persona = str(prefs.persona)
    chosen = [v for v in variants if v.persona == persona]
    if not chosen:
        chosen = [v for v in variants if v.persona == Persona.CASUAL] or list(variants)
    want_team, want_player = prefs.favourite_team_id, prefs.followed_player_id

    def rank(v: InsightVariant) -> int:
        if v.perspective_team_id is not None and v.perspective_team_id != want_team:
            return 2
        if v.perspective_player_id is not None and v.perspective_player_id != want_player:
            return 2
        exact = (v.perspective_team_id is not None and v.perspective_team_id == want_team) or (
            v.perspective_player_id is not None and v.perspective_player_id == want_player
        )
        return 0 if exact else 1

    return sorted(chosen, key=rank)


async def personalise_insight(
    insight: Insight, prefs: ViewerPrefs, translator: Translator, *, translate: bool = True
) -> Insight:
    """A copy of `insight` carrying exactly one variant for this viewer (translated if needed).

    `translate=False`: use cached translations only, else English (no LLM call).
    A player_lens viewer gets the moment retold through the followed player when they were involved in it
    (Player Lens agent); otherwise the usual pick (casual).
    """
    if prefs.persona == Persona.PLAYER_LENS and prefs.followed_player_id:
        lensed = await translator.player_lens(insight, prefs.followed_player_id, prefs.language, fetch=translate)
        if lensed is not None:
            return insight.model_copy(update={"variants": [lensed]})
    chosen = pick_variants(insight.variants, prefs)
    if not chosen:
        return insight.model_copy(update={"variants": []})
    best = chosen[0]
    same_slot = [
        v
        for v in chosen
        if (v.perspective_team_id, v.perspective_player_id) == (best.perspective_team_id, best.perspective_player_id)
    ]
    in_lang = [v for v in same_slot if v.language == prefs.language]
    if in_lang:
        return insight.model_copy(update={"variants": in_lang[:1]})
    source = [v for v in same_slot if v.language == "en"] or same_slot
    translated = await translator.variants(insight, source[:1], prefs.language, fetch=translate)
    return insight.model_copy(update={"variants": translated[:1]})


def personalise_query(
    persona: Persona | None, language: str | None, favourite_team_id: str | None, followed_player_id: str | None
) -> ViewerPrefs | None:
    """Prefs from REST query params, or None when the caller asked for the unfiltered feed."""
    if persona is None and language is None:
        return None
    return ViewerPrefs(
        persona=persona or Persona.CASUAL,
        language=language or "en",
        favourite_team_id=favourite_team_id,
        followed_player_id=followed_player_id,
    )


def cue_matches(cue: OverlayCue, prefs: ViewerPrefs) -> bool:
    """Persona + perspective + language filter (English cues pass for any language: they get translated)."""
    if cue.persona != prefs.persona:
        return False
    if cue.perspective_team_id is not None and cue.perspective_team_id != prefs.favourite_team_id:
        return False
    if cue.perspective_player_id is not None and cue.perspective_player_id != prefs.followed_player_id:
        return False
    return cue.language == prefs.language or cue.language == "en"


async def personalise_cue(cue: OverlayCue, prefs: ViewerPrefs, translator: Translator) -> OverlayCue | None:
    """The cue as this viewer should see it, or None if it is not for them."""
    if not cue_matches(cue, prefs):
        return None
    return await translator.cue(cue, prefs.language)


def select_cues(cues: list[OverlayCue], prefs: ViewerPrefs) -> list[OverlayCue]:
    """Filter a cue list, preferring a native-language cue over an English one for the same slot."""
    matching = [c for c in cues if cue_matches(c, prefs)]
    native = {(c.insight_id, c.moment_id, c.template) for c in matching if c.language == prefs.language}
    return [
        c for c in matching if c.language == prefs.language or (c.insight_id, c.moment_id, c.template) not in native
    ]


def pick_recap(recaps: dict[tuple[str, str], Recap], prefs: ViewerPrefs) -> Recap | None:
    """Best available recap: exact persona+language, then the persona in English, then casual."""
    persona = str(prefs.persona)
    for key in ((persona, prefs.language), (persona, "en"), ("casual", prefs.language), ("casual", "en")):
        if key in recaps:
            return recaps[key]
    return None


def involves(ev: MatchEvent, player_id: str) -> bool:
    """True if the followed player is the actor or the pass recipient."""
    if ev.player_id == player_id:
        return True
    return ev.pass_ is not None and ev.pass_.recipient_id == player_id and ev.outcome == "success"


def player_tag_cue(rm: MatchReadModel, ev: MatchEvent, prefs: ViewerPrefs) -> OverlayCue | None:
    """Template `player_tag` cue for the followed player (no LLM; numbers straight from MatchState)."""
    pid = prefs.followed_player_id
    if not pid or not involves(ev, pid):
        return None
    actor = ev.player_id == pid
    action = PLAYER_TAG_ACTIONS.get(ev.type) if actor else "receives"
    if action is None:
        return None
    stats: list[OverlayStat] = []
    ps = rm.state.players.get(pid) if rm.state else None
    if ps is not None:
        stats = [
            OverlayStat(label="Passes", value=f"{ps.passes_completed}/{ps.passes}"),
            OverlayStat(label="Pass accuracy", value=f"{ps.pass_accuracy:.0f}", unit="%"),
            OverlayStat(label="Rating", value=f"{ps.rating:.1f}"),
        ]
        if ps.distance_km:
            stats.append(OverlayStat(label="Distance", value=f"{ps.distance_km:.1f}", unit="km"))
    return OverlayCue(
        match_id=ev.match_id,
        template=OverlayTemplate.PLAYER_TAG,
        anchor=OverlayAnchor.TOP_RIGHT,
        priority=2,
        sync_clock_ms=ev.clock_ms,
        sync_seq=ev.seq,
        show_at_clock_ms=ev.clock_ms,
        duration_ms=PLAYER_TAG_DURATION_MS,
        persona=prefs.persona,
        language="en",
        perspective_player_id=pid,
        team_id=ev.team_id,
        player_id=pid,
        headline=f"{rm.player_name(pid)} {action}"[:90],
        stats=stats[:4],
    )
