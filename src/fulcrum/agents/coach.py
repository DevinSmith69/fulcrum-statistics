"""Coach ("Coach's view", on demand, cached) — how a goal or big chance was conceded, for the defending team.

The engine computes the evidence deterministically (`coach_` facts, MODEL_SPEC §4.1: how the move started, the
misplaced pass and its expected completion, passes and seconds to the shot, pressure on the shooter, chance
quality, keeper facts). This agent only explains it: 2–3 points, each tied to the coach_ facts it states, plus
one conditional takeaway, in the viewer's language.

Checks (deterministic, no LLM): every number in a point must come from the coach_ facts that point lists in
`fact_keys` (or the moment's minute / score), the takeaway's numbers from the coach facts; the usual fact-check
rules (foreign names, real clubs, betting terms) apply; plus lengths, cut-off copy, unknown keys and blame
language ("should have", "mistake", ...). One repair; if it still fails, or the LLM errors / times out, the result
is None and the caller serves `fallback_view` (the raw facts, no LLM). Successes are cached per (insight,
language); concurrent requests share one LLM call; failures are not cached, so a later request retries.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Sequence

from fulcrum.contracts import CheckStatus, Fact, Insight, Moment, MomentType, Team
from fulcrum.llm import LLMClient

from .context import fact_map, fmt_value, format_facts_table, format_roster, moment_minute
from .factcheck import FactSheet, Issue, base_language, cut_off, numbers_in, to_decimal
from .orchestrator import LLMTimeout, OrchestratorConfig, call_llm
from .prompts import COACH, REPAIR_INSTRUCTION
from .schemas import CoachOutput, CoachPoint, CoachView
from .studio import answer_language
from .translate import LANGUAGE_NAMES

log = logging.getLogger("fulcrum.agents")

COACH_PREFIX = "coach_"
COACH_MOMENT_TYPES = frozenset({MomentType.GOAL, MomentType.BIG_CHANCE})
# Moment facts the coach may also use for context (names, minute, score), besides the coach_ facts.
CONTEXT_KEYS = ("minute", "score", "score_after", "home_team", "away_team", "team", "scorer", "shooter", "assist")
MIN_POINTS, MAX_POINTS = 2, 3
MAX_POINT = 220
MAX_TAKEAWAY = 220

# Blame language the Coach must not use (whole words, case-insensitive). English always, plus the copy's language.
BLAME_TERMS: dict[str, tuple[str, ...]] = {
    "en": (
        "should have",
        "should've",
        "shouldn't have",
        "ought to have",
        "mistake",
        "mistakes",
        "error",
        "errors",
        "howler",
        "blunder",
        "at fault",
        "to blame",
        "blame",
        "blamed",
        "fault",
        "sloppy",
        "careless",
        "lazy",
        "switched off",
    ),
    "es": ("debería haber", "deberían haber", "debió", "debieron", "error", "errores", "culpa", "culpable"),
    "fr": ("aurait dû", "auraient dû", "erreur", "erreurs", "bourde", "coupable"),
    "pt": ("deveria ter", "deveriam ter", "erro", "erros", "culpa", "culpado"),
    "de": ("hätte müssen", "hätten müssen", "fehler", "schuld", "patzer"),
}
_BLAME_RE: dict[str, re.Pattern[str]] = {
    lang: re.compile(r"(?<!\w)(?:" + "|".join(re.escape(t) for t in terms) + r")(?!\w)", re.IGNORECASE)
    for lang, terms in BLAME_TERMS.items()
}


def coach_facts(moment: Moment) -> list[Fact]:
    """The moment's coach_ facts (MODEL_SPEC §4.1), in engine order."""
    return [f for f in moment.facts if f.key.startswith(COACH_PREFIX)]


def coach_eligible(moment: Moment) -> bool:
    """True for goal / big_chance moments that carry coach_ facts."""
    return moment.type in COACH_MOMENT_TYPES and bool(coach_facts(moment))


def context_facts(moment: Moment) -> list[Fact]:
    """Names, minute and score from the moment, for context."""
    return [f for f in moment.facts if f.key in CONTEXT_KEYS]


def defending_team_id(moment: Moment, home: Team | None, away: Team | None) -> str | None:
    """The team that conceded (the other side from the moment's attacking `team_id`)."""
    ids = [t.team_id for t in (home, away) if t is not None]
    if moment.team_id not in ids or len(ids) != 2:
        return None
    return ids[1] if moment.team_id == ids[0] else ids[0]


def fallback_view(moment: Moment, insight: Insight, *, defending: str | None = None) -> CoachView:
    """The deterministic Coach's view: no copy, just the coach_ facts (clients list them). Never calls an LLM."""
    return CoachView(
        match_id=moment.match_id,
        insight_id=insight.insight_id,
        moment_id=moment.moment_id,
        moment_type=str(moment.type),
        defending_team_id=defending,
        language="en",
        check_status=CheckStatus.FALLBACK,
        facts=coach_facts(moment),
    )


def coach_prompt(
    moment: Moment, home: Team, away: Team, score: str | None, language: str, issues: Sequence[str] = ()
) -> str:
    """User prompt: LANGUAGE · MOMENT · FACTS (context + coach_) · ROSTER (+ repair issues)."""
    f = fact_map(moment.facts)
    what = "goal conceded" if moment.type == MomentType.GOAL else "big chance allowed (not scored)"
    attacker = fmt_value(f["team"].value) if "team" in f else "the attacking team"
    defending = fmt_value(f["coach_defending_team"].value) if "coach_defending_team" in f else "the defending team"
    lang_name = LANGUAGE_NAMES.get(base_language(language), "English")
    sections = [
        f"LANGUAGE\n{lang_name} ({language})",
        f"MOMENT\n{moment_minute(moment)} · {what} · {attacker} attacking, {defending} defending"
        f" · score {score or '-'}",
        "FACTS\n" + format_facts_table([*context_facts(moment), *coach_facts(moment)]),
        "ROSTER\n" + format_roster(home, away),
    ]
    if issues:
        sections.append(REPAIR_INSTRUCTION + "\n" + "\n".join(f"- {i}" for i in issues))
    return "\n\n".join(sections)


def _blame(text: str, where: str, language: str) -> list[Issue]:
    found: dict[tuple[int, int], str] = {}  # one issue per span ("error" is blame in English and Spanish)
    for lang in dict.fromkeys(("en", base_language(language))):
        if (pattern := _BLAME_RE.get(lang)) is not None:
            for m in pattern.finditer(text):
                found.setdefault(m.span(), m.group(0))
    return [
        Issue("blame", where, f"'{word}' judges a player; describe what the facts show instead")
        for _, word in sorted(found.items())
    ]


def check_coach(
    out: CoachOutput,
    moment: Moment,
    *,
    score: str | None,
    language: str,
    foreign_names: Sequence[str] = (),
) -> list[Issue]:
    """Deterministic checks on the Coach's output (see the module docstring)."""
    facts, ctx = coach_facts(moment), context_facts(moment)
    keys = {f.key for f in facts}
    full = FactSheet.build([*ctx, *facts], score=score, clock_ms=moment.clock_ms, foreign_names=foreign_names)
    issues: list[Issue] = []
    if not MIN_POINTS <= len(out.points) <= MAX_POINTS:
        issues.append(Issue("points", "points", f"{len(out.points)} points; write {MIN_POINTS} or {MAX_POINTS}"))
    for i, point in enumerate(out.points):
        where = f"points[{i}]"
        text = point.text.strip()
        if not text:
            issues.append(Issue("empty", f"{where}.text", "the point is empty"))
            continue
        if not point.fact_keys:
            issues.append(
                Issue("unknown_fact_key", f"{where}.fact_keys", "list the coach_ fact keys this point states")
            )
        for key in point.fact_keys:
            if key not in keys:
                issues.append(Issue("unknown_fact_key", f"{where}.fact_keys", f"'{key}' is not a coach_ FACT key"))
        cited = [f for f in facts if f.key in point.fact_keys]
        own = FactSheet.build([*ctx, *cited], score=score, clock_ms=moment.clock_ms)
        for token in numbers_in(text, language):
            value = to_decimal(token)
            if value is None or value not in own.numbers:
                why = (
                    "is not in the facts this point lists in fact_keys" if value in full.numbers else "is not in FACTS"
                )
                issues.append(Issue("unknown_number", f"{where}.text", f"number '{token}' {why}"))
        issues += [i for i in full.check_text(text, f"{where}.text", language=language) if i.code != "unknown_number"]
        if len(text) > MAX_POINT:
            issues.append(Issue("too_long", f"{where}.text", f"{len(text)} chars, max {MAX_POINT}"))
        issues += cut_off(text, f"{where}.text")
        issues += _blame(text, f"{where}.text", language)
    takeaway = out.takeaway.strip()
    if not takeaway:
        issues.append(Issue("empty", "takeaway", "the takeaway is empty"))
    else:
        issues += full.check_text(takeaway, "takeaway", language=language)
        if len(takeaway) > MAX_TAKEAWAY:
            issues.append(Issue("too_long", "takeaway", f"{len(takeaway)} chars, max {MAX_TAKEAWAY}"))
        issues += cut_off(takeaway, "takeaway")
        issues += _blame(takeaway, "takeaway", language)
    return issues


class CoachAgent:
    """On-demand Coach's views with an in-memory cache per (insight_id, language)."""

    def __init__(self, llm: LLMClient, config: OrchestratorConfig | None = None) -> None:
        self.llm = llm
        self.config = config or OrchestratorConfig()
        self._cache: dict[tuple[str, str], CoachView] = {}
        self._inflight: dict[tuple[str, str], asyncio.Task[CoachView | None]] = {}

    async def view(
        self,
        insight: Insight,
        moment: Moment,
        home: Team,
        away: Team,
        language: str = "en",
        *,
        foreign_names: Sequence[str] = (),
        score: str | None = None,
    ) -> CoachView | None:
        """The checked Coach's view in `language`, or None (not a goal/big chance, or no checked copy this time)."""
        if not coach_eligible(moment):
            return None
        lang = answer_language(language)
        key = (insight.insight_id, lang)
        if key in self._cache:
            return self._cache[key]
        task = self._inflight.get(key)
        if task is None:  # concurrent requests for the same key share one LLM call
            task = asyncio.create_task(self._write(insight, moment, home, away, lang, foreign_names, score))
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        return await asyncio.shield(task)

    async def _write(
        self,
        insight: Insight,
        moment: Moment,
        home: Team,
        away: Team,
        lang: str,
        foreign_names: Sequence[str],
        score: str | None,
    ) -> CoachView | None:
        if score is None and (score_fact := fact_map(moment.facts).get("score")) is not None:
            score = fmt_value(score_fact.value)
        started = time.monotonic()
        deadline = started + self.config.total_budget_s
        issues: list[str] = []
        status = CheckStatus.PASSED
        for attempt in (1, 2):
            try:
                out = await call_llm(
                    self.llm,
                    COACH,
                    user=coach_prompt(moment, home, away, score, lang, issues),
                    schema=CoachOutput,
                    config=self.config,
                    deadline=deadline,
                )
            except LLMTimeout as e:
                log.warning("coach %s timed out: %s", insight.insight_id, e)
                return None
            except Exception as e:  # noqa: BLE001
                log.warning("coach %s failed (attempt %d): %s", insight.insight_id, attempt, e)
                issues, status = [], CheckStatus.REPAIRED
                continue
            problems = check_coach(out, moment, score=score, language=lang, foreign_names=foreign_names)
            if not problems:
                view = CoachView(
                    match_id=moment.match_id,
                    insight_id=insight.insight_id,
                    moment_id=moment.moment_id,
                    moment_type=str(moment.type),
                    defending_team_id=defending_team_id(moment, home, away),
                    language=lang,
                    check_status=status,
                    points=[
                        CoachPoint(text=p.text.strip(), fact_keys=list(dict.fromkeys(p.fact_keys))) for p in out.points
                    ],
                    takeaway=out.takeaway.strip(),
                    facts=coach_facts(moment),
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
                self._cache[(insight.insight_id, lang)] = view
                return view
            issues, status = [str(p) for p in problems], CheckStatus.REPAIRED
            log.info("coach %s attempt %d failed checks: %s", insight.insight_id, attempt, issues)
        log.warning("coach %s: no checked copy, the facts are served instead", insight.insight_id)
        return None
