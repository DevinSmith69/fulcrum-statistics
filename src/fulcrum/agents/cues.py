"""Cue Producer: Insight → OverlayCue[] (API_CONTRACT §4). Deterministic.

Each insight yields ≥ 1 cue per persona variant (all four English variants at publish time; other
languages are produced on demand by `translate.py`, which calls `build_cues` with translated variants).
Moment-type mapping per the contract, plus decisions for the types it leaves open (see `CUE_MAP`).
Cues for players in `moment.player_ids` are also emitted as `player_tag` cues with persona `player_lens`
so a viewer following that player gets a tag without an LLM call.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from fulcrum.contracts import (
    Insight,
    InsightVariant,
    Moment,
    MomentType,
    OverlayAnchor,
    OverlayCue,
    OverlayStat,
    OverlayTemplate,
    Persona,
    Player,
    Team,
)

from .context import fact_map, fmt_value
from .templates import build_slots

MAX_STATS = 4


@dataclass(frozen=True)
class CueSpec:
    """How one moment type renders as one overlay cue."""

    template: OverlayTemplate
    priority: int
    duration_ms: int
    anchor: OverlayAnchor
    stat_keys: tuple[str, ...] = ()  # fact keys or team-relative aliases (`wp_team_after`)
    text: str = "variant"  # "variant": headline/body from the variant; "wp": WP label + commentary line
    wp_side: str = "team"  # whose win probability a "wp" cue shows: the moment's team or its opponent


LT, WP, SC, PB, RC, PT = (
    OverlayTemplate.LOWER_THIRD,
    OverlayTemplate.WP_SWING,
    OverlayTemplate.STAT_CARD,
    OverlayTemplate.PRESSURE_BAR,
    OverlayTemplate.RECAP_CARD,
    OverlayTemplate.PLAYER_TAG,
)
BL, TR, BR, TL, BC = (
    OverlayAnchor.BOTTOM_LEFT,
    OverlayAnchor.TOP_RIGHT,
    OverlayAnchor.BOTTOM_RIGHT,
    OverlayAnchor.TOP_LEFT,
    OverlayAnchor.BOTTOM_CENTER,
)
WP_STATS = ("wp_team_before", "wp_team_after", "wp_team_delta")

CUE_MAP: dict[MomentType, tuple[CueSpec, ...]] = {
    # contract-specified
    MomentType.GOAL: (
        CueSpec(LT, 5, 8000, BL, ("xg", "shot_speed_kmh", "distance_m", "pass_difficulty_of_assist")),
        CueSpec(WP, 4, 6000, TR, ("wp_scorer_before", "wp_scorer_after", "wp_scorer_delta"), text="wp"),
    ),
    MomentType.WP_SWING: (CueSpec(WP, 4, 6000, TR, WP_STATS, text="wp"),),
    MomentType.PRESSURE_SHIFT: (
        CueSpec(PB, 3, 6000, TL, ("pressure_index_team_after", "pressure_index_opp_after", "shots_last_5")),
    ),
    MomentType.MOMENTUM_RUN: (CueSpec(PB, 3, 6000, TL, ("shots_last_5", "opp_shots_last_5", "xg_last_5")),),
    MomentType.PHASE_CHANGE: (
        CueSpec(PB, 2, 6000, TL, ("control_index_home", "control_index_away", "chaos_index", "turnovers_last_5")),
    ),
    MomentType.ELITE_PASS: (
        CueSpec(SC, 3, 7000, BR, ("difficulty", "expected_completion_pct", "length_m", "progressive_m")),
    ),
    MomentType.SPEED_RECORD: (CueSpec(SC, 3, 7000, BR, ("speed_kmh", "previous_match_high")),),
    MomentType.PLAYER_MILESTONE: (CueSpec(SC, 2, 7000, BR, ("value",)),),
    MomentType.FULL_TIME: (CueSpec(RC, 5, 12000, BC, ("xg_home", "xg_away", "possession_home", "possession_away")),),
    # decisions (contract is silent) — recorded in docs/HANDOFFS.md
    MomentType.BIG_CHANCE: (CueSpec(SC, 3, 7000, BR, ("xg", "distance_m", "team_xg_total")),),
    MomentType.RED_CARD: (
        CueSpec(LT, 4, 8000, BL),
        CueSpec(WP, 3, 6000, TR, ("wp_opp_before", "wp_opp_after", "wp_opp_delta"), text="wp", wp_side="opp"),
    ),
    MomentType.TEMPO_CHANGE: (CueSpec(PB, 2, 6000, TL, ("tempo_ppm", "tempo_match_avg")),),
    MomentType.HALF_TIME: (CueSpec(RC, 4, 10000, BC, ("xg_home", "xg_away", "possession_home", "possession_away")),),
}
PLAYER_TAG = CueSpec(PT, 2, 5000, TL)

WP_LABEL = {
    "en": "Win probability",
    "es": "Probabilidad de victoria",
    "fr": "Probabilité de victoire",
    "pt": "Probabilidade de vitória",
    "de": "Siegwahrscheinlichkeit",
}


def show_at(moment: Moment, latency_ms: int | None) -> int:
    """Moment clock + processing latency rounded up to the next whole second."""
    return moment.clock_ms + math.ceil(max(0, latency_ms or 0) / 1000) * 1000


def select_stats(
    moment: Moment, home: Team, away: Team, keys: Sequence[str], fallback: Sequence[str]
) -> list[OverlayStat]:
    """Up to four stats: the spec's preferred keys (aliases resolved), then the explanation's key facts."""
    facts = fact_map(moment.facts)
    sources = build_slots(moment, home, away, score="").sources
    stats: list[OverlayStat] = []
    seen: set[str] = set()
    for key in [*keys, *fallback]:
        real = key if key in facts else sources.get(key)
        if real is None or real in seen or real not in facts:
            continue
        fact = facts[real]
        if isinstance(fact.value, str) and key not in keys:
            continue  # only numeric facts from the explanation's key facts
        seen.add(real)
        stats.append(OverlayStat(label=fact.label, value=fmt_value(fact.value), unit=fact.unit))
        if len(stats) == MAX_STATS:
            break
    return stats


def _cue(
    spec: CueSpec,
    *,
    insight: Insight,
    moment: Moment,
    variant: InsightVariant,
    headline: str,
    body: str | None,
    stats: list[OverlayStat],
    persona: str | None = None,
    player_id: str | None = None,
) -> OverlayCue:
    return OverlayCue(
        match_id=insight.match_id,
        moment_id=moment.moment_id,
        insight_id=insight.insight_id,
        template=spec.template,
        anchor=spec.anchor,
        priority=spec.priority,
        sync_clock_ms=moment.clock_ms,
        sync_seq=moment.seq_end,
        show_at_clock_ms=show_at(moment, insight.latency_ms),
        duration_ms=spec.duration_ms,
        persona=persona or variant.persona,
        language=variant.language,
        perspective_team_id=variant.perspective_team_id,
        perspective_player_id=player_id or variant.perspective_player_id,
        team_id=moment.team_id,
        player_id=player_id or (moment.player_ids[0] if moment.player_ids else None),
        headline=headline,
        body=body,
        stats=stats,
    )


def build_cues(
    insight: Insight,
    moment: Moment,
    home: Team,
    away: Team,
    variants: Sequence[InsightVariant] | None = None,
) -> list[OverlayCue]:
    """All overlay cues for `insight` — per variant (default: `insight.variants`) and per player tag."""
    variants = list(insight.variants if variants is None else variants)
    specs = CUE_MAP.get(MomentType(moment.type), (CueSpec(LT, 3, 6000, BL),))
    team_names = {home.team_id: home.name, away.team_id: away.name}
    opponent = {home.team_id: away.team_id, away.team_id: home.team_id}
    cues: list[OverlayCue] = []
    for spec in specs:
        stats = select_stats(moment, home, away, spec.stat_keys, insight.explanation.key_fact_keys)
        for v in variants:
            if v.persona == Persona.PLAYER_LENS:
                continue
            if spec.text == "wp":
                label = WP_LABEL.get(v.language.split("-")[0], WP_LABEL["en"])
                side_id = opponent.get(moment.team_id or "") if spec.wp_side == "opp" else moment.team_id
                team = team_names.get(side_id or "")
                headline, body = (f"{label} · {team}" if team else label), v.commentary_line
            else:
                headline, body = v.headline, v.body
            cues.append(
                _cue(spec, insight=insight, moment=moment, variant=v, headline=headline, body=body, stats=stats)
            )
    cues += player_tag_cues(insight, moment, home, away, variants)
    return cues


def player_tag_cues(
    insight: Insight, moment: Moment, home: Team, away: Team, variants: Sequence[InsightVariant]
) -> list[OverlayCue]:
    """`player_tag` cues (persona player_lens) for each player involved in the moment."""
    players: dict[str, Player] = {p.player_id: p for t in (home, away) for p in t.players}
    casual = next((v for v in variants if v.persona == Persona.CASUAL), variants[0] if variants else None)
    if casual is None:
        return []
    cues = []
    for pid in moment.player_ids:
        player = players.get(pid)
        if player is None:
            continue
        cues.append(
            _cue(
                PLAYER_TAG,
                insight=insight,
                moment=moment,
                variant=casual,
                headline=player.name,
                body=casual.headline,
                stats=[],
                persona=Persona.PLAYER_LENS,
                player_id=pid,
            )
        )
    return cues
