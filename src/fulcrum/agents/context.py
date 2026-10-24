"""Match context for the agents: rosters, recent sequence, score, and the user-prompt builders.

The user prompts follow AI_PROMPT_DESIGN §4: `MOMENT` · `FACTS` (key | label | value | unit) · `ROSTER` ·
`RECENT SEQUENCE` · `SCORE`. Numbers in FACTS are rendered with `fmt_value`, the same function the
fact-checker and templates use, so "exactly as formatted" means one thing everywhere.
"""

from __future__ import annotations

import json
import os
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from fulcrum.contracts import (
    EventType,
    Explanation,
    Fact,
    Insight,
    InsightVariant,
    MatchConfig,
    MatchEvent,
    Moment,
    Persona,
    Team,
)

from .factcheck import required_variants
from .schemas import MomentContext

DEFAULT_TEAMS_PATH = Path(__file__).resolve().parents[3] / "data" / "teams.json"
RECENT_EVENTS = 8


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


def load_teams(path: str | Path | None = None) -> list[Team]:
    """Load the fictional league from `data/teams.json` (override with `FULCRUM_TEAMS_PATH`)."""
    p = Path(path or os.getenv("FULCRUM_TEAMS_PATH") or DEFAULT_TEAMS_PATH)
    return [Team.model_validate(t) for t in json.loads(p.read_text(encoding="utf-8"))]


def resolve_team(value: object, league: Iterable[Team]) -> Team | None:
    """Find a team by team_id, name or short_name (case-insensitive). Returns None if unknown."""
    if value is None:
        return None
    needle = str(value).strip().casefold()
    for team in league:
        if needle in (team.team_id.casefold(), team.name.casefold(), team.short_name.casefold()):
            return team
    return None


def foreign_names(home: Team, away: Team, league: Iterable[Team]) -> list[str]:
    """Names of league teams and players that are NOT in this match (they must not appear in copy)."""
    in_match = {home.team_id, away.team_id}
    own_words = {w.casefold() for t in (home, away) for w in t.name.split()}
    names: list[str] = []
    for team in league:
        if team.team_id in in_match:
            continue
        names += [team.name]
        first = team.name.split()[0]  # distinctive first word: "Harlow", "Marsh"
        if first.casefold() not in own_words:
            names.append(first)
        names += [p.short_name for p in team.players]
    return names


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #


def fmt_value(value: float | int | str) -> str:
    """Render a fact value exactly as agents must quote it (engine formatting is preserved, never 1e-05)."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        text = str(value)
        return format(Decimal(text), "f") if "e" in text or "E" in text else text
    return str(value)


def minute_label(clock_ms: int) -> str:
    """Football minute for a match clock, e.g. 66_500 ms → "67'" (the minute being played)."""
    return f"{clock_ms // 60_000 + 1}'"


def fact_map(facts: Iterable[Fact]) -> dict[str, Fact]:
    """Index facts by key."""
    return {f.key: f for f in facts}


def moment_minute(moment: Moment) -> str:
    """The moment's minute as the engine formatted it, or derived from its clock."""
    fact = fact_map(moment.facts).get("minute")
    return fmt_value(fact.value) if fact is not None else minute_label(moment.clock_ms)


def format_facts_table(facts: Sequence[Fact]) -> str:
    """`key | label | value | unit` table, one fact per line."""
    lines = ["key | label | value | unit"]
    lines += [f"{f.key} | {f.label} | {fmt_value(f.value)} | {f.unit or ''}".rstrip() for f in facts]
    return "\n".join(lines)


def format_roster(home: Team, away: Team) -> str:
    """`team: players` lines. Players are listed as `player_id short_name (full name)`."""
    lines = []
    for side, team in (("home", home), ("away", away)):
        players = ", ".join(f"{p.player_id} {p.short_name} ({p.name})" for p in team.players)
        lines.append(f"{team.team_id} {team.name} [{side}]: {players}")
    return "\n".join(lines)


def format_event(event: MatchEvent, names: dict[str, str]) -> str:
    """One compact line per event, e.g. `67' Brennick (NBA) pass → Aldane (diff 7.8)`."""
    who = names.get(event.player_id or "", event.player_id or "")
    team = f" ({event.team_id})" if event.team_id else ""
    line = f"{minute_label(event.clock_ms)} {who}{team} {event.type}".replace("  ", " ")
    if event.pass_ is not None:
        to = names.get(event.pass_.recipient_id or "", "")
        line += f" → {to}" if to else ""
        line += f" (diff {event.pass_.difficulty:.1f}, {event.outcome})"
    elif event.shot is not None:
        line += f" (xG {event.shot.xg:.2f}, {event.outcome})"
    elif event.outcome != "na":
        line += f" ({event.outcome})"
    return line


def _moment_header(moment: Moment) -> str:
    return (
        f"MOMENT\ntype: {moment.type}\nminute: {moment_minute(moment)}\n"
        f"significance: {moment.significance:.2f}\nteam_id: {moment.team_id or '-'}\ntrigger: {moment.trigger}"
    )


def _common_sections(ctx: MomentContext) -> list[str]:
    seq = "\n".join(ctx.recent_sequence) if ctx.recent_sequence else "(none)"
    return [
        _moment_header(ctx.moment),
        "FACTS\n" + format_facts_table(ctx.moment.facts),
        "ROSTER\n" + format_roster(ctx.home, ctx.away),
        "RECENT SEQUENCE\n" + seq,
        f"SCORE\n{ctx.home.name} {ctx.score} {ctx.away.name}",
    ]


def _with_issues(sections: list[str], issues: Sequence[str]) -> str:
    from .prompts import REPAIR_INSTRUCTION

    if issues:
        sections = [*sections, REPAIR_INSTRUCTION + "\n" + "\n".join(f"- {i}" for i in issues)]
    return "\n\n".join(sections)


# --------------------------------------------------------------------------- #
# User prompts
# --------------------------------------------------------------------------- #


def analyst_prompt(ctx: MomentContext, issues: Sequence[str] = ()) -> str:
    """User prompt for the Moment Analyst."""
    return _with_issues(_common_sections(ctx), issues)


def editor_prompt(
    ctx: MomentContext,
    explanation: Explanation,
    issues: Sequence[str] = (),
    *,
    only: tuple[str, str | None] | None = None,
) -> str:
    """User prompt for the Persona Editor: the explanation plus the same facts/roster/score.

    `only` asks for a single (persona, perspective_team_id) variant (the fan-out mode); default is all four.
    """
    names = {ctx.home.team_id: ctx.home.name, ctx.away.team_id: ctx.away.name}
    pairs = [only] if only is not None else required_variants(ctx.home.team_id, ctx.away.team_id)
    lines = [
        f"- persona {persona}" + (f", perspective_team_id {team} ({names.get(team, team)})" if team else "")
        for persona, team in pairs
    ]
    targets = "VARIANTS REQUIRED (language en)\n" + "\n".join(lines)
    sections = [
        "EXPLANATION\n" + explanation.model_dump_json(indent=2),
        *_common_sections(ctx),
        targets,
    ]
    return _with_issues(sections, issues)


def review_prompt(ctx: MomentContext) -> str:
    """User prompt for the LLM reviewer: everything written for this moment, plus the evidence."""
    copy = {
        "explanation": ctx.explanation.model_dump(mode="json") if ctx.explanation else None,
        "variants": [v.model_dump(mode="json") for v in ctx.variants],
    }
    return "\n\n".join([*_common_sections(ctx), "COPY TO REVIEW\n" + json.dumps(copy, indent=2, ensure_ascii=False)])


def translator_prompt(
    variants: Sequence[InsightVariant], language: str, facts: Sequence[Fact], issues: Sequence[str] = ()
) -> str:
    """User prompt for the Translator."""
    src = json.dumps([v.model_dump(mode="json") for v in variants], indent=2, ensure_ascii=False)
    sections = [
        f'TARGET LANGUAGE\n{language} (set every variant\'s language to "{language}")',
        "FACTS\n" + format_facts_table(facts),
        "SOURCE VARIANTS (keep order, persona and perspective ids)\n" + src,
    ]
    return _with_issues(sections, issues)


def player_lens_prompt(
    moment: Moment,
    explanation: Explanation,
    home: Team,
    away: Team,
    score: str | None,
    player_id: str,
    issues: Sequence[str] = (),
) -> str:
    """User prompt for the Player Lens: the moment, its explanation and the facts, plus who to follow."""
    player = next((p for p in [*home.players, *away.players] if p.player_id == player_id), None)
    who = f"{player.player_id} {player.short_name} ({player.name})" if player else player_id
    sections = [
        _moment_header(moment),
        "EXPLANATION\n" + explanation.model_dump_json(indent=2),
        "FACTS\n" + format_facts_table(moment.facts),
        "ROSTER\n" + format_roster(home, away),
        f"SCORE\n{home.name} {score or '-'} {away.name}",
        f"FOLLOWED PLAYER\n{who}",
    ]
    return _with_issues(sections, issues)


def recap_prompt(
    *,
    persona: Persona | str,
    language: str,
    full_time: Moment,
    moments: Sequence[tuple[Moment, Insight | None]],
    home: Team,
    away: Team,
    score: str,
    issues: Sequence[str] = (),
) -> str:
    """User prompt for the Match Reporter: full-time facts, the top moments in order, roster, score."""
    lines = []
    for moment, insight in moments:
        headline = insight.explanation.headline if insight else ""
        why = insight.explanation.why_it_matters if insight else ""
        lines.append(f"- id {moment.moment_id} · {moment_minute(moment)} · {moment.type} · {headline} · {why}")
        lines += [f"    {f.key} | {f.label} | {fmt_value(f.value)} | {f.unit or ''}".rstrip() for f in moment.facts]
    sections = [
        f"PERSONA\n{persona}\nLANGUAGE\n{language}",
        "FACTS (full time)\n" + format_facts_table(full_time.facts),
        "MOMENTS (chronological, with their facts)\n" + ("\n".join(lines) or "(none)"),
        "ROSTER\n" + format_roster(home, away),
        f"SCORE\n{home.name} {score} {away.name}",
        f"HASHTAG\n{match_hashtag(home, away)}",
    ]
    return _with_issues(sections, issues)


def match_hashtag(home: Team, away: Team) -> str:
    """The one hashtag share posts may use, e.g. `#NBARVR` (fictional team ids, no real-world tags)."""
    return f"#{home.team_id}{away.team_id}"


# --------------------------------------------------------------------------- #
# Per-match running context (fed by the service from match-events and moments)
# --------------------------------------------------------------------------- #


@dataclass
class MatchContext:
    """What the agents know about one match beyond the moment itself."""

    match_id: str
    home: Team | None = None
    away: Team | None = None
    score_home: int = 0
    score_away: int = 0
    recent: deque[MatchEvent] = field(default_factory=lambda: deque(maxlen=RECENT_EVENTS))
    history: list[tuple[Moment, Insight | None]] = field(default_factory=list)

    @property
    def score(self) -> str:
        return f"{self.score_home}-{self.score_away}"


class MatchContextStore:
    """In-memory per-match context: teams, last events, score, moment/insight history."""

    def __init__(self, league: Sequence[Team]) -> None:
        self.league = list(league)
        self._matches: dict[str, MatchContext] = {}

    def get(self, match_id: str) -> MatchContext:
        """Return (creating if needed) the context for `match_id`."""
        return self._matches.setdefault(match_id, MatchContext(match_id))

    def register_match(self, config: MatchConfig) -> MatchContext:
        """Pin the teams for a match (the API calls this when it starts a match)."""
        mc = self.get(config.match_id)
        mc.home = resolve_team(config.home_team_id, self.league)
        mc.away = resolve_team(config.away_team_id, self.league)
        return mc

    def apply_event(self, event: MatchEvent) -> None:
        """Track score and the recent sequence; learn teams from `match_start.extra` if present."""
        mc = self.get(event.match_id)
        mc.score_home, mc.score_away = event.score_home, event.score_away
        if event.type == EventType.MATCH_START:
            mc.home = mc.home or resolve_team(event.extra.get("home_team_id"), self.league)
            mc.away = mc.away or resolve_team(event.extra.get("away_team_id"), self.league)
        if event.player_id is not None:
            mc.recent.append(event)

    def resolve_teams(self, moment: Moment) -> tuple[Team, Team] | None:
        """Teams for the moment's match: registered/learned first, then `home_team`/`away_team` facts."""
        mc = self.get(moment.match_id)
        facts = fact_map(moment.facts)
        for side in ("home", "away"):
            if getattr(mc, side) is None:
                for key in (f"{side}_team_id", f"{side}_team"):
                    if key in facts and (team := resolve_team(facts[key].value, self.league)):
                        setattr(mc, side, team)
                        break
        if mc.home is None or mc.away is None:
            return None
        return mc.home, mc.away

    def score_for(self, moment: Moment) -> str:
        """Score at the moment: the `score` fact (engine truth) else the latest event score."""
        fact = fact_map(moment.facts).get("score")
        return fmt_value(fact.value) if fact is not None else self.get(moment.match_id).score

    def recent_lines(self, match_id: str) -> list[str]:
        """The recent sequence as compact text, oldest first."""
        mc = self.get(match_id)
        names = {p.player_id: p.short_name for t in (mc.home, mc.away) if t for p in t.players}
        return [format_event(e, names) for e in mc.recent]

    def build_context(self, moment: Moment) -> MomentContext | None:
        """Assemble the workflow's shared state for `moment`, or None if its teams are unknown."""
        teams = self.resolve_teams(moment)
        if teams is None:
            return None
        home, away = teams
        return MomentContext(
            moment=moment,
            home=home,
            away=away,
            score=self.score_for(moment),
            recent_sequence=self.recent_lines(moment.match_id),
            foreign_names=foreign_names(home, away, self.league),
        )

    def record(self, moment: Moment, insight: Insight | None) -> None:
        """Remember a processed moment (used by the full-time recap)."""
        self.get(moment.match_id).history.append((moment, insight))
