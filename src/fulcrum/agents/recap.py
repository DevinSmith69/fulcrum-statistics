"""Match Reporter — full-time recap per persona/language (AI_PROMPT_DESIGN §1, §4).

Lazy and cached per (match_id, persona, language). Input: the full-time moment's facts plus the top-8
moments of the match by significance (in chronological order) with their insights. The copy may quote any
number from those moments' fact packs (and their minutes), the final score, and names from the two rosters.
Fact-checked, repaired once, then a deterministic fallback built from already-checked insight headlines
(English; not cached, so a later request retries the LLM).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from dataclasses import replace

from fulcrum.contracts import CheckStatus, Insight, Moment, MomentType, Persona, Recap, Team
from fulcrum.llm import LLMClient

from .context import fact_map, match_hashtag, moment_minute, recap_prompt
from .factcheck import FactSheet, Issue, cut_off
from .orchestrator import LLMTimeout, OrchestratorConfig, call_llm
from .prompts import MATCH_REPORTER
from .schemas import RecapOutput
from .translate import LANGUAGE_NAMES, normalise_language

log = logging.getLogger("fulcrum.agents")

TOP_MOMENTS = 8
TURNING_POINTS = 3
MAX_TITLE = 80
MAX_SUMMARY = 1200
MAX_POST = 280
MAX_POSTS = 3
NON_STORY_TYPES = {MomentType.HALF_TIME.value, MomentType.FULL_TIME.value}


def select_moments(
    history: Sequence[tuple[Moment, Insight | None]], n: int = TOP_MOMENTS
) -> list[tuple[Moment, Insight | None]]:
    """Top `n` story moments by significance, returned in match order."""
    story = [(m, i) for m, i in history if m.type not in NON_STORY_TYPES]
    top = sorted(story, key=lambda mi: (-mi[0].significance, mi[0].clock_ms))[:n]
    return sorted(top, key=lambda mi: (mi[0].clock_ms, mi[0].seq_end))


def recap_sheet(
    full_time: Moment, moments: Sequence[tuple[Moment, Insight | None]], score: str, foreign_names: Sequence[str]
) -> FactSheet:
    """Allowed numbers: full-time facts, every selected moment's facts and minutes, and the score."""
    facts = [*full_time.facts, *(f for m, _ in moments for f in m.facts)]
    minutes = [moment_minute(m) for m, _ in moments]
    return FactSheet.build(
        facts, score=score, clock_ms=full_time.clock_ms, foreign_names=foreign_names, extra_numbers=minutes
    )


def check_recap(
    out: RecapOutput,
    sheet: FactSheet,
    *,
    moment_ids: set[str],
    player_ids: set[str],
    language: str,
    hashtag: str | None = None,
) -> list[Issue]:
    """Rules 1, 3, 4, 5 on title, summary and share posts, plus valid moment and player ids."""
    issues = sheet.check_text(out.title, "recap.title", language=language)
    issues += sheet.check_text(out.summary, "recap.summary", language=language)
    if len(out.title) > MAX_TITLE:
        issues.append(Issue("too_long", "recap.title", f"{len(out.title)} chars, max {MAX_TITLE}"))
    if len(out.summary) > MAX_SUMMARY:
        issues.append(Issue("too_long", "recap.summary", f"{len(out.summary)} chars, max {MAX_SUMMARY}"))
    issues += cut_off(out.summary, "recap.summary")
    if len(out.social_posts) > MAX_POSTS:
        issues.append(Issue("too_long", "recap.social_posts", f"{len(out.social_posts)} posts, max {MAX_POSTS}"))
    posts = [(f"recap.social_posts[{n}]", p) for n, p in enumerate(out.social_posts)]
    for where, post in posts:
        issues += sheet.check_text(post.replace(hashtag, "") if hashtag else post, where, language=language)
        if len(post) > MAX_POST:
            issues.append(Issue("too_long", where, f"{len(post)} chars, max {MAX_POST}"))
        stray = [t for t in re.findall(r"#\w+", post) if t != hashtag]
        if stray:
            issues.append(Issue("hashtag", where, f"only {hashtag} is allowed, not {', '.join(stray)}"))
    for field_name, text in (("recap.title", out.title), ("recap.summary", out.summary), *posts):
        leaked = sorted({pid for pid in player_ids if pid in text})
        if leaked:
            issues.append(Issue("player_id_in_text", field_name, f"use names, not ids: {', '.join(leaked)}"))
    for mid in out.key_moment_ids:
        if mid not in moment_ids:
            issues.append(Issue("unknown_moment", "recap.key_moment_ids", f"'{mid}' is not one of the MOMENTS"))
    if out.player_of_the_match_id is not None and out.player_of_the_match_id not in player_ids:
        issues.append(
            Issue("unknown_player", "recap.player_of_the_match_id", f"'{out.player_of_the_match_id}' not in ROSTER")
        )
    return issues


def _player_id(value: object, home: Team, away: Team) -> str | None:
    needle = str(value).casefold()
    for p in (*home.players, *away.players):
        if needle in (p.player_id.casefold(), p.short_name.casefold(), p.name.casefold()):
            return p.player_id
    return None


def fallback_recap(
    *,
    match_id: str,
    persona: str,
    full_time: Moment,
    moments: Sequence[tuple[Moment, Insight | None]],
    home: Team,
    away: Team,
    score: str,
) -> Recap:
    """Deterministic recap from the score, the checked insight headlines and the top-rated player."""
    title = f"{home.name} {score} {away.name}"
    if len(title) > MAX_TITLE:
        title = f"Full time: {score}"
    turning = sorted((mi for mi in moments if mi[1] is not None), key=lambda mi: -mi[0].significance)[:TURNING_POINTS]
    turning.sort(key=lambda mi: mi[0].clock_ms)
    parts = [f"Full time: {home.name} {score} {away.name}."]
    if turning:
        points = "; ".join(f"{moment_minute(m)} {i.explanation.headline}" for m, i in turning if i)
        parts.append(f"The turning points: {points}.")
    facts = fact_map(full_time.facts)
    potm = _player_id(facts["top_player_1"].value, home, away) if "top_player_1" in facts else None
    if potm is not None:
        name = next(p.short_name for p in (*home.players, *away.players) if p.player_id == potm)
        parts.append(f"Standout performer: {name}.")
    summary = " ".join(parts)
    if len(summary) > MAX_SUMMARY:
        summary = parts[0]
    tag = match_hashtag(home, away)
    posts = [f"Full time: {home.name} {score} {away.name}. {tag}"]
    if potm is not None:
        posts.append(f"Standout performer: {name}. {tag}")
    return Recap(
        match_id=match_id,
        persona=persona,
        language="en",
        title=title,
        summary=summary,
        key_moment_ids=[m.moment_id for m, _ in turning],
        player_of_the_match_id=potm,
        check_status=CheckStatus.FALLBACK,
        social_posts=[p for p in posts if len(p) <= MAX_POST],
    )


class RecapService:
    """Generates and caches recaps per (match_id, persona, language)."""

    def __init__(self, llm: LLMClient, config: OrchestratorConfig | None = None) -> None:
        self.llm = llm
        self.config = config or OrchestratorConfig()
        self._cache: dict[tuple[str, str, str], Recap] = {}

    def cached(self, match_id: str, persona: str, language: str) -> Recap | None:
        """The cached recap, if generated."""
        return self._cache.get((match_id, str(persona), normalise_language(language)))

    async def recap(
        self,
        *,
        full_time: Moment,
        history: Sequence[tuple[Moment, Insight | None]],
        home: Team,
        away: Team,
        score: str,
        persona: Persona | str = Persona.CASUAL,
        language: str = "en",
        foreign_names: Sequence[str] = (),
    ) -> Recap:
        """The recap for one persona/language (cached). Never raises; falls back to a template."""
        persona, lang = str(Persona(persona)), normalise_language(language)
        key = (full_time.match_id, persona, lang)
        if key in self._cache:
            return self._cache[key]
        moments = select_moments(history)
        sheet = recap_sheet(full_time, moments, score, foreign_names)
        moment_ids = {m.moment_id for m, _ in moments}
        player_ids = {p.player_id for t in (home, away) for p in t.players}
        config = replace(
            self.config, call_timeout_s=self.config.recap_call_timeout_s, total_budget_s=self.config.recap_budget_s
        )
        deadline = time.monotonic() + config.total_budget_s
        issues: list[str] = []
        status = CheckStatus.PASSED
        for attempt in (1, 2):
            try:
                out = await call_llm(
                    self.llm,
                    MATCH_REPORTER,
                    user=recap_prompt(
                        persona=persona,
                        language=LANGUAGE_NAMES.get(lang.split("-")[0], lang),
                        full_time=full_time,
                        moments=moments,
                        home=home,
                        away=away,
                        score=score,
                        issues=issues,
                    ),
                    schema=RecapOutput,
                    config=config,
                    deadline=deadline,
                    persona=persona,
                )
            except LLMTimeout as e:
                log.warning("recap %s timed out: %s", key, e)
                break
            except Exception as e:  # noqa: BLE001
                log.warning("recap %s failed (attempt %d): %s", key, attempt, e)
                issues, status = [], CheckStatus.REPAIRED
                continue
            problems = check_recap(
                out,
                sheet,
                moment_ids=moment_ids,
                player_ids=player_ids,
                language=lang,
                hashtag=match_hashtag(home, away),
            )
            if not problems:
                recap = Recap(
                    match_id=full_time.match_id,
                    persona=persona,
                    language=lang,
                    title=out.title,
                    summary=out.summary,
                    key_moment_ids=out.key_moment_ids,
                    player_of_the_match_id=out.player_of_the_match_id,
                    check_status=status,
                    social_posts=out.social_posts,
                )
                self._cache[key] = recap
                return recap
            issues, status = [str(p) for p in problems], CheckStatus.REPAIRED
            log.info("recap %s attempt %d failed checks: %s", key, attempt, issues)
        log.warning("recap %s fell back to template (not cached, so a later request retries)", key)
        return fallback_recap(
            match_id=full_time.match_id,
            persona=persona,
            full_time=full_time,
            moments=moments,
            home=home,
            away=away,
            score=score,
        )
