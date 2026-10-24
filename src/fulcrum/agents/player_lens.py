"""Player Lens (on demand, cached) — AI_PROMPT_DESIGN §1: a moment retold through one followed player.

`PlayerLens.variant` returns a `player_lens` InsightVariant for (insight, player), written in English and
fact-checked exactly like the Editor's copy (one repair). It only runs when the player is in
`moment.player_ids`, i.e. directly involved; otherwise it returns None and the viewer gets the casual variant.
An LLM failure raises `PlayerLensUnavailable` (the API then serves casual and retries on a later request).
Successes are cached per (insight_id, player_id); concurrent requests for the same key share one LLM call.
Other languages go through the Translator (see `AgentService.player_lens`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence

from fulcrum.contracts import Insight, InsightVariant, Moment, Persona, Team
from fulcrum.llm import LLMClient

from .context import fact_map, fmt_value, player_lens_prompt
from .factcheck import FactSheet, check_variant
from .orchestrator import LLMTimeout, OrchestratorConfig, call_llm
from .prompts import PLAYER_LENS

log = logging.getLogger("fulcrum.agents")


class PlayerLensUnavailable(RuntimeError):
    """The player was involved but no checked lens could be written (LLM failure/timeout); retry later."""


class PlayerLens:
    """On-demand player-perspective variants with an in-memory cache."""

    def __init__(self, llm: LLMClient, config: OrchestratorConfig | None = None) -> None:
        self.llm = llm
        self.config = config or OrchestratorConfig()
        self._cache: dict[tuple[str, str], InsightVariant] = {}
        self._inflight: dict[tuple[str, str], asyncio.Task[InsightVariant | None]] = {}

    async def variant(
        self,
        insight: Insight,
        moment: Moment,
        home: Team,
        away: Team,
        player_id: str,
        *,
        foreign_names: Sequence[str] = (),
        score: str | None = None,
    ) -> InsightVariant | None:
        """The English player_lens variant for `player_id`; None if not involved; raises PlayerLensUnavailable if the
        LLM could not write a checked one (so callers retry later instead of caching the miss)."""
        if player_id not in moment.player_ids:
            return None
        key = (insight.insight_id, player_id)
        if key in self._cache:
            return self._cache[key]
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._write(insight, moment, home, away, player_id, foreign_names, score))
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        got = await asyncio.shield(task)
        if got is None:  # involved, but the LLM failed: raise so callers don't cache "no lens" for good
            raise PlayerLensUnavailable(f"player lens {insight.insight_id}/{player_id} failed")
        return got

    async def _write(
        self,
        insight: Insight,
        moment: Moment,
        home: Team,
        away: Team,
        player_id: str,
        foreign_names: Sequence[str],
        score: str | None,
    ) -> InsightVariant | None:
        if score is None and (score_fact := fact_map(moment.facts).get("score")) is not None:
            score = fmt_value(score_fact.value)
        player = next((p for p in [*home.players, *away.players] if p.player_id == player_id), None)
        name = player.short_name if player else player_id
        sheet = FactSheet.build(moment.facts, score=score, clock_ms=moment.clock_ms, foreign_names=foreign_names)
        deadline = time.monotonic() + self.config.total_budget_s
        issues: list[str] = []
        for attempt in (1, 2):
            try:
                out = await call_llm(
                    self.llm,
                    PLAYER_LENS,
                    user=player_lens_prompt(moment, insight.explanation, home, away, score, player_id, issues),
                    schema=InsightVariant,
                    config=self.config,
                    deadline=deadline,
                    player=name,
                    player_id=player_id,
                )
            except LLMTimeout as e:
                log.warning("player lens %s/%s timed out: %s", insight.insight_id, player_id, e)
                return None
            except Exception as e:  # noqa: BLE001
                log.warning("player lens %s/%s failed (attempt %d): %s", insight.insight_id, player_id, attempt, e)
                issues = []
                continue
            variant = out.model_copy(
                update={
                    "persona": Persona.PLAYER_LENS,
                    "language": "en",
                    "perspective_team_id": None,
                    "perspective_player_id": player_id,
                }
            )
            problems = check_variant(variant, sheet)
            if not problems:
                self._cache[(insight.insight_id, player_id)] = variant
                return variant
            issues = [str(p) for p in problems]
            log.info("player lens %s/%s attempt %d failed checks: %s", insight.insight_id, player_id, attempt, issues)
        log.warning("player lens %s/%s fell back to the casual variant", insight.insight_id, player_id)
        return None
