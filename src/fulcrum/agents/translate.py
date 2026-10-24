"""Translator (on demand, cached) — AI_PROMPT_DESIGN §1/§4.

`Translator.translate` returns an insight's variants in another language. Results are cached per
(insight_id, language); concurrent requests for the same key share one LLM call. The fact-checker re-runs on
every translation (decimal commas are accepted). A failing translation is repaired once; if it still fails, or
the LLM errors / times out, the English variants are returned unchanged (and not cached, so a later request
can try again). `Translator.cues` gives the overlay cues for the translated variants.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence

from fulcrum.contracts import Insight, InsightVariant, Moment, OverlayCue, Team
from fulcrum.llm import LLMClient

from .context import fact_map, fmt_value, translator_prompt
from .cues import build_cues
from .factcheck import FactSheet, Issue, check_variants, variant_label
from .orchestrator import LLMTimeout, OrchestratorConfig, call_llm
from .prompts import TRANSLATOR
from .schemas import TranslationOutput

log = logging.getLogger("fulcrum.agents")

DEMO_LANGUAGES = ("es", "fr", "pt", "de")
LANGUAGE_NAMES = {"en": "English", "es": "Spanish", "fr": "French", "pt": "Portuguese", "de": "German"}


def normalise_language(language: str) -> str:
    """'ES' → 'es', 'pt-BR' → 'pt-br' (stored lower-case; base language drives checks)."""
    return language.strip().lower().replace("_", "-")


def _structure_issues(source: Sequence[InsightVariant], translated: Sequence[InsightVariant]) -> list[Issue]:
    if len(source) != len(translated):
        return [Issue("variants", "variants", f"expected {len(source)} variants, got {len(translated)}")]
    issues = []
    for i, (s, t) in enumerate(zip(source, translated, strict=True)):
        if (s.persona, s.perspective_team_id, s.perspective_player_id) != (
            t.persona,
            t.perspective_team_id,
            t.perspective_player_id,
        ):
            issues.append(Issue("variants", f"variants[{i}]", f"must stay {variant_label(s)} (same order as source)"))
    return issues


class Translator:
    """On-demand translation of insight variants with an in-memory cache."""

    def __init__(self, llm: LLMClient, config: OrchestratorConfig | None = None) -> None:
        self.llm = llm
        self.config = config or OrchestratorConfig()
        self._cache: dict[tuple[str, str], list[InsightVariant]] = {}
        self._cue_cache: dict[tuple[str, str], list[OverlayCue]] = {}
        self._inflight: dict[tuple[str, str], asyncio.Task[list[InsightVariant]]] = {}

    def cached(self, insight_id: str, language: str) -> list[InsightVariant] | None:
        """The cached translation, if any."""
        return self._cache.get((insight_id, normalise_language(language)))

    async def translate(
        self,
        insight: Insight,
        moment: Moment,
        home: Team,
        away: Team,
        language: str,
        *,
        foreign_names: Sequence[str] = (),
        score: str | None = None,
    ) -> list[InsightVariant]:
        """`insight.variants` (English) in `language`. Never raises; falls back to English.

        Pass the `score` the English copy was checked with (default: the moment's `score` fact) so the
        translation is held to exactly the same numbers.
        """
        lang = normalise_language(language)
        source = [v for v in insight.variants if v.language == "en"] or list(insight.variants)
        if lang == "en" or lang.startswith("en-"):
            return source
        key = (insight.insight_id, lang)
        if key in self._cache:
            return self._cache[key]
        task = self._inflight.get(key)
        if task is None:  # concurrent requests for the same key share one LLM call
            task = asyncio.create_task(self._translate(insight, moment, source, lang, foreign_names, score))
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        return await asyncio.shield(task)

    async def cues(
        self,
        insight: Insight,
        moment: Moment,
        home: Team,
        away: Team,
        language: str,
        *,
        foreign_names: Sequence[str] = (),
        score: str | None = None,
    ) -> list[OverlayCue]:
        """Overlay cues for the translated variants (cached alongside the translation)."""
        lang = normalise_language(language)
        variants = await self.translate(insight, moment, home, away, lang, foreign_names=foreign_names, score=score)
        if variants and variants[0].language == "en":
            return build_cues(insight, moment, home, away, variants)
        key = (insight.insight_id, lang)
        if key not in self._cue_cache:
            self._cue_cache[key] = build_cues(insight, moment, home, away, variants)
        return self._cue_cache[key]

    async def _translate(
        self,
        insight: Insight,
        moment: Moment,
        source: list[InsightVariant],
        lang: str,
        foreign_names: Sequence[str],
        score: str | None,
    ) -> list[InsightVariant]:
        if score is None and (score_fact := fact_map(moment.facts).get("score")) is not None:
            score = fmt_value(score_fact.value)
        sheet = FactSheet.build(moment.facts, score=score, clock_ms=moment.clock_ms, foreign_names=foreign_names)
        deadline = time.monotonic() + self.config.total_budget_s
        issues: list[str] = []
        for attempt in (1, 2):
            try:
                out = await call_llm(
                    self.llm,
                    TRANSLATOR,
                    user=translator_prompt(source, lang, moment.facts, issues),
                    schema=TranslationOutput,
                    config=self.config,
                    deadline=deadline,
                    language=LANGUAGE_NAMES.get(lang.split("-")[0], lang),
                )
            except LLMTimeout as e:
                log.warning("translation %s→%s timed out: %s", insight.insight_id, lang, e)
                return source
            except Exception as e:  # noqa: BLE001
                log.warning("translation %s→%s failed (attempt %d): %s", insight.insight_id, lang, attempt, e)
                issues = []
                continue
            translated = [v.model_copy(update={"language": lang}) for v in out.variants]
            problems = _structure_issues(source, translated) + check_variants(translated, sheet).issues
            if not problems:
                self._cache[(insight.insight_id, lang)] = translated
                return translated
            issues = [str(p) for p in problems]
            log.info("translation %s→%s attempt %d failed checks: %s", insight.insight_id, lang, attempt, issues)
        log.warning("translation %s→%s fell back to English", insight.insight_id, lang)
        return source
