"""Producer console support: the editorial re-check of producer edits, and the Graphics Producer agent.

**Edits.** A producer may rewrite a held insight's persona variants before it goes out. `apply_edit` swaps the
edited variants in (matched on persona, perspective and language) and runs the merged set through the same
deterministic checker as agent copy (`factcheck.check_insight_copy`, against `insight_sheet`: the moment's
facts, its score and minute, and the names that must not appear). A failing edit is rejected with the issues.

**Overlay requests in plain language.** The Graphics Producer (`graphics_producer`, a fixed-prompt role published
as a Foundry prompt agent) turns a request like "stat card on Brennick's passing" into one `OverlayCue`, using
only a deterministic context pack: the Studio Analyst's pack (`studio.build_pack`: score, minute, team and top
player live stats, win probability, the most significant + latest moments) extended by `graphics_pack` with the
live stats of every player the request names and each team's win probability earlier in the match.
Stat values are never written by the model: it picks pack fact keys and the values are filled in from the pack.
Its text and stat labels are fact-checked like every answer (numbers, scorelines, goal counts, names, betting
terms, leaked ids, length, cut-off copy), plus template rules (a wp_swing needs two % stats, a player_tag a
player). One repair; otherwise a clear refusal. Betting / real-club requests and requests naming a team or
player who is not in this match are refused before any LLM call.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from dataclasses import replace

from fulcrum.contracts import (
    CheckStatus,
    Fact,
    GraphicProposal,
    Insight,
    InsightVariant,
    MatchState,
    Moment,
    OverlayAnchor,
    OverlayCue,
    OverlayStat,
    OverlayTemplate,
    Persona,
    Player,
    Team,
    WinProbPoint,
)
from fulcrum.llm import LLMClient

from .context import fact_map, fmt_value, format_facts_table, format_roster, minute_label
from .factcheck import (
    MAX_BODY,
    MAX_HEADLINE,
    CheckResult,
    FactSheet,
    Issue,
    check_insight_copy,
    cut_off,
    variant_label,
)
from .orchestrator import LLMTimeout, OrchestratorConfig, call_llm
from .prompts import GRAPHICS_PRODUCER, REPAIR_INSTRUCTION
from .schemas import GraphicOutput
from .studio import _NUMBER_WORDS, GOAL_WORDS_RE, SCORELINE_RE, StudioPack, _score, screen_question

log = logging.getLogger("fulcrum.agents")

EDIT_NOTE = "edited by producer"
MAX_STAT_LABEL = 40
EARLIER_MS = 15 * 60_000  # "earlier" win probability: 15 minutes ago (or kick-off)

# template -> (anchor, priority, duration_ms): layout is ours, not the model's
GRAPHIC_LAYOUT: dict[str, tuple[OverlayAnchor, int, int]] = {
    "lower_third": (OverlayAnchor.BOTTOM_LEFT, 4, 9000),
    "stat_card": (OverlayAnchor.BOTTOM_RIGHT, 3, 9000),
    "wp_swing": (OverlayAnchor.TOP_RIGHT, 3, 8000),
    "pressure_bar": (OverlayAnchor.TOP_LEFT, 3, 8000),
    "player_tag": (OverlayAnchor.TOP_LEFT, 2, 7000),
}

# Deterministic refusals (no numbers, no banned terms: tests fact-check them).
REFUSE_OFF_TOPIC = (
    "That can't go on screen: the graphics desk only makes graphics about this match, from its own data. "
    "Predictions, other matches and real-world clubs are off limits."
)
REFUSE_UNDOABLE = (
    "The match data can't support that graphic. Try a stat card on a player or team, a win probability graphic "
    "or a pressure bar."
)
REFUSE_UNVERIFIED = "Couldn't build a graphic whose every number checks out against the match data. Try rephrasing."
REFUSE_ERROR = "The graphics desk didn't answer in time. Try again."


# --------------------------------------------------------------------------- #
# Producer edits
# --------------------------------------------------------------------------- #


def score_at(moment: Moment, fallback: str) -> str:
    """Score at the moment: its `score` fact (engine truth), else `fallback` (as the agents do)."""
    fact = fact_map(moment.facts).get("score")
    return fmt_value(fact.value) if fact is not None else fallback


def insight_sheet(moment: Moment, *, score: str, foreign: Sequence[str] = ()) -> FactSheet:
    """The fact sheet agent copy for `moment` was checked against: its facts, score and minute."""
    return FactSheet.build(moment.facts, score=score, clock_ms=moment.clock_ms, foreign_names=foreign)


def _slot(v: InsightVariant) -> tuple[str, str | None, str | None, str]:
    return (str(v.persona), v.perspective_team_id, v.perspective_player_id, v.language)


def apply_edit(
    insight: Insight,
    edits: Sequence[InsightVariant],
    sheet: FactSheet,
    *,
    home_id: str,
    away_id: str,
) -> tuple[Insight | None, CheckResult]:
    """Swap edited variants into `insight` and fact-check the result.

    Returns the edited insight (marked with an `edited by producer` note) when every variant passes, else
    None and the issues. An edit for a slot the insight doesn't have is an issue too.
    """
    by_slot = {_slot(v): v for v in insight.variants}
    result = CheckResult()
    for v in edits:
        if _slot(v) not in by_slot:
            result.issues.append(Issue("variants", variant_label(v), f"no such variant ({v.language}) to edit"))
        else:
            by_slot[_slot(v)] = v
    if result.issues:
        return None, result
    variants = list(by_slot.values())
    languages = {v.language for v in variants}
    language = languages.pop() if len(languages) == 1 else "en"
    result = check_insight_copy(None, variants, sheet, home_id=home_id, away_id=away_id, language=language)
    if not result.passed:
        return None, result
    notes = [*insight.check_notes, EDIT_NOTE]
    return insight.model_copy(update={"variants": variants, "check_notes": notes}), result


# --------------------------------------------------------------------------- #
# The graphics pack
# --------------------------------------------------------------------------- #


def _f(key: str, label: str, value: float | int | str, unit: str | None = None) -> Fact:
    return Fact(key=key, label=label, value=value, unit=unit)


def _num(value: float, digits: int) -> float | int:
    return round(value) if digits == 0 else round(value, digits)


# (field, label, digits, unit): what a player graphic may show
_PLAYER_STATS: tuple[tuple[str, str, int, str | None], ...] = (
    ("touches", "touches", 0, None),
    ("passes", "passes attempted", 0, None),
    ("passes_completed", "completed passes", 0, None),
    ("pass_accuracy", "pass accuracy", 0, "%"),
    ("avg_pass_difficulty", "average pass difficulty (0-10)", 1, None),
    ("key_passes", "key passes", 0, None),
    ("shots", "shots", 0, None),
    ("xg", "xG", 2, "xG"),
    ("goals", "goals", 0, None),
    ("tackles_won", "tackles won", 0, None),
    ("interceptions", "interceptions", 0, None),
    ("distance_km", "distance covered", 1, "km"),
    ("top_speed_kmh", "top speed", 1, "km/h"),
    ("sprints", "sprints", 0, None),
    ("rating", "match rating (0-10)", 1, None),
)


# Ranks as words: a digit in a label would become a quotable number for the fact-checker.
_RANK = {1: "(first)", 2: "(second)", 3: "(third)"}


def _name_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])", re.IGNORECASE)


def named_players(request: str, home: Team, away: Team) -> list[tuple[Player, Team]]:
    """Players of this match the request names (short name or full name), in roster order."""
    found: list[tuple[Player, Team]] = []
    for team in (home, away):
        for p in team.players:
            if any(_name_re(n).search(request) for n in {p.short_name, p.name}):
                found.append((p, team))
    return found


def _earlier_point(winprob: Sequence[WinProbPoint], clock_ms: int) -> WinProbPoint | None:
    """The win probability point about 15 minutes before `clock_ms` (else the first one)."""
    if not winprob:
        return None
    target = clock_ms - EARLIER_MS
    earlier = [p for p in winprob if p.clock_ms <= target]
    return earlier[-1] if earlier else winprob[0]


def graphics_pack(
    base: StudioPack,
    request: str,
    *,
    state: MatchState | None,
    winprob: Sequence[WinProbPoint] = (),
) -> StudioPack:
    """The Studio Analyst's pack plus named players' live stats and each team's earlier win probability."""
    extra: list[Fact] = []
    home, away = base.home, base.away
    if state is not None:
        for i, (p, team) in enumerate(named_players(request, home, away), start=1):
            stats = state.players.get(p.player_id)
            who = f"{p.short_name} ({team.short_name})"
            extra.append(_f(f"focus_{i}", f"Requested player {_RANK.get(i, 'other')}", who))
            if stats is None:
                continue
            for name, label, digits, unit in _PLAYER_STATS:
                extra.append(
                    _f(f"focus_{i}_{name}", f"{p.short_name} {label}", _num(getattr(stats, name), digits), unit)
                )
        point = _earlier_point(winprob, state.clock_ms)
        if point is not None and point.clock_ms < state.clock_ms:
            at = "kick-off" if point.clock_ms < 60_000 else minute_label(point.clock_ms)
            extra += [
                _f("wp_home_earlier", f"{home.name} win probability at {at}", _num(point.home * 100, 0), "%"),
                _f("wp_away_earlier", f"{away.name} win probability at {at}", _num(point.away * 100, 0), "%"),
            ]
    if not extra:
        return base
    return replace(base, facts=[*base.facts, *extra])


def graphics_prompt(pack: StudioPack, request: str, issues: Sequence[str] = ()) -> str:
    """User prompt: REQUEST · MATCH · FACTS · MOMENTS · ROSTER (+ repair issues)."""
    teams = {t.team_id: t.name for t in (pack.home, pack.away)}
    lines = []
    for pm in pack.moments:
        team = teams.get(pm.moment.team_id or "", "both teams")
        lines.append(f"- {pm.minute} · {pm.moment.type} · {team} · {pm.headline}".rstrip(" ·"))
        lines += [f"    {f.key} | {f.label} | {fmt_value(f.value)} | {f.unit or ''}".rstrip() for f in pm.facts]
    sections = [
        "REQUEST (from the producer)\n" + " ".join(request.split()),
        f"MATCH\n{pack.home.name} (home, {pack.home.team_id}) vs {pack.away.name} (away, {pack.away.team_id}), "
        f"score {pack.score}",
        "FACTS (the match so far)\n" + format_facts_table(pack.facts),
        "MOMENTS (chronological: minute · type · team · headline, then that moment's facts)\n"
        + ("\n".join(lines) or "(none yet)"),
        "ROSTER\n" + format_roster(pack.home, pack.away),
    ]
    if issues:
        sections.append(REPAIR_INSTRUCTION + "\n" + "\n".join(f"- {i}" for i in issues))
    return "\n\n".join(sections)


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


def _all_facts(pack: StudioPack) -> dict[str, Fact]:
    facts = {f.key: f for pm in pack.moments for f in pm.facts}
    facts.update({f.key: f for f in pack.facts})  # match-level facts win on a key clash
    return facts


def _check_text(text: str, where: str, pack: StudioPack) -> list[Issue]:
    issues = pack.sheet.check_text(text, where)
    for m in SCORELINE_RE.finditer(text):
        if _score(m) not in pack.scorelines:
            issues.append(Issue("unknown_score", where, f"scoreline '{m.group(0)}' is not a score in FACTS"))
    goal_counts = {int(n) for sl in pack.scorelines for n in sl.split("-")}
    for m in GOAL_WORDS_RE.finditer(text):
        word = m.group(1).lower()
        if (int(word) if word.isdigit() else _NUMBER_WORDS[word]) not in goal_counts:
            issues.append(Issue("unknown_number", where, f"'{m.group(0)}' is not a goal count in this match"))
    leaked = sorted(i for i in pack.player_ids | pack.moment_ids if i in text)
    if leaked:
        issues.append(Issue("id_in_text", where, f"use names, not ids: {', '.join(leaked)}"))
    return issues


def check_graphic(out: GraphicOutput, pack: StudioPack) -> list[Issue]:
    """Every rule a graphic must pass before it can be previewed (numbers, names, ids, lengths, template)."""
    facts = _all_facts(pack)
    players = {p.player_id for t in (pack.home, pack.away) for p in t.players}
    issues: list[Issue] = []
    headline, body = out.headline.strip(), out.body.strip()
    if out.template != "player_tag" and not headline:
        issues.append(Issue("empty", "headline", "the headline is empty"))
    if len(headline) > MAX_HEADLINE:
        issues.append(Issue("too_long", "headline", f"{len(headline)} chars, max {MAX_HEADLINE}"))
    if len(body) > MAX_BODY:
        issues.append(Issue("too_long", "body", f"{len(body)} chars, max {MAX_BODY}"))
    issues += _check_text(headline, "headline", pack)
    if body:
        issues += _check_text(body, "body", pack)
        issues += cut_off(body, "body")
    if len(out.stats) > 4:
        issues.append(Issue("too_many", "stats", f"{len(out.stats)} stats, max 4"))
    for n, s in enumerate(out.stats):
        where = f"stats[{n}]"
        fact = facts.get(s.fact_key)
        if fact is None:
            issues.append(Issue("unknown_fact_key", where, f"'{s.fact_key}' is not a FACT key"))
        elif isinstance(fact.value, str):
            issues.append(Issue("not_numeric", where, f"'{s.fact_key}' is not a number; pick a numeric FACT"))
        label = s.label.strip()
        if not label:
            issues.append(Issue("empty", f"{where}.label", "the label is empty"))
        if len(label) > MAX_STAT_LABEL:
            issues.append(Issue("too_long", f"{where}.label", f"{len(label)} chars, max {MAX_STAT_LABEL}"))
        issues += _check_text(label, f"{where}.label", pack)
    for key in out.fact_keys:
        if key not in facts:
            issues.append(Issue("unknown_fact_key", "fact_keys", f"'{key}' is not a FACT key"))
    if out.team_id is not None and out.team_id not in (pack.home.team_id, pack.away.team_id):
        issues.append(Issue("unknown_team", "team_id", f"'{out.team_id}' is not a team in this match"))
    if out.player_id is not None and out.player_id not in players:
        issues.append(Issue("unknown_player", "player_id", f"'{out.player_id}' is not a player in ROSTER"))
    if out.template == "player_tag" and out.player_id is None:
        issues.append(Issue("template", "player_id", "a player_tag needs the player_id of the player it shows"))
    if out.template == "stat_card" and not out.stats:
        issues.append(Issue("template", "stats", "a stat_card needs at least one stat"))
    if out.template == "wp_swing":
        units = [facts[s.fact_key].unit if s.fact_key in facts else None for s in out.stats[:2]]
        if len(units) < 2 or any(u != "%" for u in units):
            issues.append(
                Issue(
                    "template",
                    "stats",
                    "a wp_swing needs two % stats: the earlier, then current win probability",
                )
            )
        if out.team_id is None:
            issues.append(Issue("template", "team_id", "a wp_swing needs the team_id whose win probability it shows"))
    if out.template == "pressure_bar":
        issues += _pressure_direction(headline, facts, pack)
    return issues


def _pressure_direction(headline: str, facts: dict[str, Fact], pack: StudioPack) -> list[Issue]:
    """A pressure bar's headline must credit the side the bar shows ahead (live run: "MDU pressing harder" over a
    73-30 bar for HLC). Numbers pass the fact check; direction needs its own deterministic rule."""
    home, away = facts.get("home_pressure_index"), facts.get("away_pressure_index")
    if home is None or away is None or isinstance(home.value, str) or isinstance(away.value, str):
        return []
    gap = float(home.value) - float(away.value)
    if abs(gap) < 5:
        return []
    leader, trailer = (pack.home, pack.away) if gap > 0 else (pack.away, pack.home)
    text = headline.casefold()
    if trailer.name.casefold() in text and leader.name.casefold() not in text:
        return [
            Issue(
                "direction",
                "headline",
                f"the headline credits {trailer.name}, but {leader.name} lead the pressure index "
                f"({fmt_value(max(home.value, away.value))} vs {fmt_value(min(home.value, away.value))})",
            )
        ]
    return []


# --------------------------------------------------------------------------- #
# The agent
# --------------------------------------------------------------------------- #


def build_graphic_cue(out: GraphicOutput, pack: StudioPack, *, seq: int = 0) -> OverlayCue:
    """The checked output as an `OverlayCue` (origin "producer", persona-neutral, English)."""
    facts = _all_facts(pack)
    players = {p.player_id: p for t in (pack.home, pack.away) for p in t.players}
    team_of = {p.player_id: t.team_id for t in (pack.home, pack.away) for p in t.players}
    anchor, priority, duration = GRAPHIC_LAYOUT[out.template]
    stats = [
        OverlayStat(label=s.label.strip(), value=fmt_value(facts[s.fact_key].value), unit=facts[s.fact_key].unit)
        for s in out.stats[:4]
    ]
    headline = out.headline.strip()
    if out.template == "player_tag" and out.player_id in players:
        headline = players[out.player_id].name  # PlayerTag renders "<first> <rest>" like the agents' tags
    clock = pack.clock_ms or 0
    return OverlayCue(
        match_id=pack.match_id,
        template=OverlayTemplate(out.template),
        anchor=anchor,
        priority=priority,
        sync_clock_ms=clock,
        sync_seq=seq,
        show_at_clock_ms=clock,
        duration_ms=duration,
        persona=Persona.CASUAL,
        language="en",
        team_id=out.team_id or team_of.get(out.player_id or ""),
        player_id=out.player_id,
        headline=headline,
        body=out.body.strip() or None,
        stats=stats,
        origin="producer",
    )


def screen_request(request: str, pack: StudioPack) -> str | None:
    """Pre-LLM refusal for a request, or None: betting / real clubs, or a name that isn't in this match."""
    if screen_question(request):
        return REFUSE_OFF_TOPIC
    foreign = [i for i in pack.sheet.check_text(request, "request") if i.code == "foreign_name"]
    if foreign:
        name = foreign[0].detail.split("'")[1]
        return f"{name} isn't in this match: only {pack.home.name} and {pack.away.name} can go on screen."
    return None


class GraphicsProducer:
    """Turns a producer's plain-language request into one fact-checked `OverlayCue` (or a refusal)."""

    def __init__(self, llm: LLMClient, config: OrchestratorConfig | None = None) -> None:
        self.llm = llm
        self.config = config or OrchestratorConfig()

    def _refuse(
        self, pack: StudioPack, request: str, reason: str, started: float, issues: Sequence[str] = ()
    ) -> GraphicProposal:
        return GraphicProposal(
            match_id=pack.match_id,
            request=request,
            ok=False,
            check_status=CheckStatus.FALLBACK,
            refusal=reason,
            issues=list(issues),
            latency_ms=_ms(started),
        )

    async def propose(self, pack: StudioPack, request: str, *, seq: int = 0) -> GraphicProposal:
        """One graphic for `request` from `pack`: verified, or a refusal. Never raises."""
        started = time.monotonic()
        request = " ".join(request.split())
        if (reason := screen_request(request, pack)) is not None:
            log.info("graphics %s: refused without the LLM: %r", pack.match_id, request[:80])
            return self._refuse(pack, request, reason, started)
        deadline = time.monotonic() + self.config.total_budget_s
        issues: list[str] = []
        status = CheckStatus.PASSED
        errored = False
        for attempt in (1, 2):
            try:
                out = await call_llm(
                    self.llm,
                    GRAPHICS_PRODUCER,
                    user=graphics_prompt(pack, request, issues),
                    schema=GraphicOutput,
                    config=self.config,
                    deadline=deadline,
                )
            except LLMTimeout as e:
                log.warning("graphics %s timed out: %s", pack.match_id, e)
                errored = True
                break
            except Exception as e:  # noqa: BLE001
                log.warning("graphics %s failed (attempt %d): %s", pack.match_id, attempt, e)
                issues, status, errored = [], CheckStatus.REPAIRED, True
                continue
            errored = False
            if not out.doable:
                return self._refuse(pack, request, REFUSE_UNDOABLE, started)
            problems = check_graphic(out, pack)
            if not problems:
                cue = build_graphic_cue(out, pack, seq=seq)
                keys = list(dict.fromkeys([*(s.fact_key for s in out.stats), *out.fact_keys]))
                return GraphicProposal(
                    match_id=pack.match_id,
                    request=request,
                    ok=True,
                    cue=cue,
                    check_status=status,
                    fact_keys=keys,
                    latency_ms=_ms(started),
                )
            issues, status = [str(p) for p in problems], CheckStatus.REPAIRED
            log.info("graphics %s attempt %d failed checks: %s", pack.match_id, attempt, issues)
        if errored and not issues:
            return self._refuse(pack, request, REFUSE_ERROR, started)
        return self._refuse(pack, request, REFUSE_UNVERIFIED, started, issues)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
