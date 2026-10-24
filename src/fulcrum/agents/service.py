"""Agents service: `moments` (+ `match-events`) in → `InsightsEnvelope`s out on `insights`.

Runs standalone (`python -m fulcrum.agents`, one Container App) or in-process in single-process dev mode, where
the API lifespan creates one `AgentService` on the shared bus and calls `start()`. The API also uses it for the
on-demand paths: `translate`, `translate_cues`, `recap` and `register_match`.

Per match, moments are explained strictly in arrival order (one worker per match); matches run in parallel.
For each moment the service publishes the Insight, then its OverlayCues; after the full-time moment it also
publishes the casual/English Recap (other personas/languages are generated lazily by `recap`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Sequence

from fulcrum.bus import Bus
from fulcrum.contracts import (
    TOPIC_EVENTS,
    TOPIC_INSIGHTS,
    TOPIC_MOMENTS,
    Insight,
    InsightsEnvelope,
    InsightVariant,
    MatchConfig,
    MatchEvent,
    Moment,
    MomentType,
    OverlayCue,
    Persona,
    Recap,
    Team,
)
from fulcrum.llm import LLMClient

from .context import MatchContextStore, foreign_names, load_teams
from .orchestrator import ExplainMomentWorkflow, MomentProcessor, OrchestratorConfig
from .recap import RecapService
from .schemas import MomentContext
from .translate import Translator

log = logging.getLogger("fulcrum.agents")

CONSUMER_GROUP = "agents"
MAX_PENDING = 50  # per match: moments waiting for their teams to be known


class AgentService:
    """The agent team behind the bus."""

    def __init__(
        self,
        bus: Bus,
        llm: LLMClient,
        *,
        league: Sequence[Team] | None = None,
        config: OrchestratorConfig | None = None,
        group: str = CONSUMER_GROUP,
        consume_events: bool | None = None,
    ) -> None:
        self.bus = bus
        self.group = group
        self.consume_events = (
            os.getenv("FULCRUM_AGENTS_EVENTS", "1") == "1" if consume_events is None else consume_events
        )
        self.config = config or OrchestratorConfig()
        self.llm = llm
        self.store = MatchContextStore(league if league is not None else load_teams())
        self.workflow = ExplainMomentWorkflow(llm, self.config)
        self.processor = MomentProcessor(self.workflow, self._publish)
        self.translator = Translator(llm, self.config)
        self.recaps = RecapService(llm, self.config)
        self._seen: set[str] = set()
        self._insights: dict[str, tuple[Moment, Insight, str]] = {}  # id -> (moment, insight, score)
        self._pending: dict[str, list[Moment]] = {}
        self._tasks: list[asyncio.Task[None]] = []

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Subscribe to the input topics (queues are registered before this returns, so nothing is missed)."""
        if (warm_up := getattr(self.llm, "warm_up", None)) is not None:
            try:
                await warm_up()  # FoundryLLM: token + clients, so the first moment isn't a cold start
            except Exception:
                log.exception("agents: LLM warm-up failed; the first calls may time out")
        topics = [(TOPIC_MOMENTS, Moment, self._on_moment)]
        if self.consume_events:
            topics.append((TOPIC_EVENTS, MatchEvent, self._on_event))
        for topic, model, handler in topics:
            if hasattr(self.bus, "register"):
                self.bus.register(topic, self.group)  # MemoryBus: create the queue up front
            self._tasks.append(asyncio.create_task(self._consume(topic, model, handler), name=f"agents:{topic}"))

    async def run(self) -> None:
        """Start and run until cancelled."""
        await self.start()
        try:
            await asyncio.gather(*self._tasks)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Cancel subscriptions and workers."""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        await self.processor.close()

    async def drain(self) -> None:
        """Wait until every moment received so far has been published (tests, graceful shutdown)."""
        await asyncio.sleep(0)
        await self.processor.drain()

    async def _consume(self, topic: str, model: type, handler) -> None:  # noqa: ANN001
        async for message in self.bus.subscribe(topic, model, self.group):
            try:
                await handler(message)
            except Exception:
                log.exception("agents: failed to handle %s message", topic)

    # ------------------------------------------------------------------ input

    def register_match(self, config: MatchConfig) -> None:
        """Tell the agents which teams play `config.match_id` (call when a match starts)."""
        self.store.register_match(config)
        self._retry_pending(config.match_id)

    async def _on_event(self, event: MatchEvent) -> None:
        self.store.apply_event(event)
        if event.match_id in self._pending:
            self._retry_pending(event.match_id)

    def _retry_pending(self, match_id: str) -> None:
        for moment in self._pending.pop(match_id, []):
            self.submit(moment)

    async def _on_moment(self, moment: Moment) -> None:
        self.submit(moment)

    def submit(self, moment: Moment) -> MomentContext | None:
        """Queue a moment for explanation (idempotent on moment_id). Returns its context, or None if skipped."""
        if moment.moment_id in self._seen:
            return None
        ctx = self.store.build_context(moment)
        if ctx is None:
            # Topics are consumed independently, so a moment can beat match_start: hold it until teams are known.
            pending = self._pending.setdefault(moment.match_id, [])
            if all(m.moment_id != moment.moment_id for m in pending):
                if len(pending) >= MAX_PENDING:
                    dropped = pending.pop(0)
                    log.warning("agents: dropping moment %s, still no teams for %s", dropped.moment_id, moment.match_id)
                pending.append(moment)
            log.warning(
                "agents: holding moment %s — unknown teams for match %s (register_match, match_start extra or "
                "home_team/away_team facts)",
                moment.moment_id,
                moment.match_id,
            )
            return None
        self._seen.add(moment.moment_id)
        self.processor.submit(ctx)
        return ctx

    # ------------------------------------------------------------------ output

    async def _publish(self, ctx: MomentContext) -> None:
        assert ctx.insight is not None
        moment, insight = ctx.moment, ctx.insight
        self.store.record(moment, insight)
        self._insights[insight.insight_id] = (moment, insight, ctx.score)
        await self.bus.publish(TOPIC_INSIGHTS, moment.match_id, InsightsEnvelope.wrap(insight))
        for cue in ctx.cues:
            await self.bus.publish(TOPIC_INSIGHTS, moment.match_id, InsightsEnvelope.wrap(cue))
        log.info(
            "agents: %s %s %s in %sms (%d cues)",
            moment.match_id,
            moment.type,
            insight.check_status,
            insight.latency_ms,
            len(ctx.cues),
        )
        if moment.type == MomentType.FULL_TIME:
            recap = await self.recap(moment.match_id, Persona.CASUAL, "en")
            if recap is not None:
                await self.bus.publish(TOPIC_INSIGHTS, moment.match_id, InsightsEnvelope.wrap(recap))

    # ------------------------------------------------------------------ on-demand (API)

    def insight(self, insight_id: str) -> Insight | None:
        """A published insight by id."""
        found = self._insights.get(insight_id)
        return found[1] if found else None

    def pending(self, match_id: str) -> list[Moment]:
        """Moments held because the match's teams are not known yet."""
        return list(self._pending.get(match_id, []))

    def _teams(self, match_id: str) -> tuple[Team, Team] | None:
        mc = self.store.get(match_id)
        return (mc.home, mc.away) if mc.home and mc.away else None

    async def translate(self, insight_id: str, language: str) -> list[InsightVariant]:
        """Variants of a published insight in `language` (cached; English on failure). Empty if unknown id."""
        found = self._insights.get(insight_id)
        teams = self._teams(found[0].match_id) if found else None
        if found is None or teams is None:
            return []
        moment, insight, score = found
        home, away = teams
        return await self.translator.translate(
            insight,
            moment,
            home,
            away,
            language,
            foreign_names=foreign_names(home, away, self.store.league),
            score=score,
        )

    async def translate_cues(self, insight_id: str, language: str) -> list[OverlayCue]:
        """Overlay cues of a published insight in `language`. Empty if unknown id."""
        found = self._insights.get(insight_id)
        teams = self._teams(found[0].match_id) if found else None
        if found is None or teams is None:
            return []
        moment, insight, score = found
        home, away = teams
        return await self.translator.cues(
            insight,
            moment,
            home,
            away,
            language,
            foreign_names=foreign_names(home, away, self.store.league),
            score=score,
        )

    async def recap(self, match_id: str, persona: Persona | str = Persona.CASUAL, language: str = "en") -> Recap | None:
        """The full-time recap for a persona/language (lazy, cached). None until the full-time moment is done."""
        history = self.store.get(match_id).history
        full_time = next((m for m, _ in reversed(history) if m.type == MomentType.FULL_TIME), None)
        teams = self._teams(match_id)
        if full_time is None or teams is None:
            return None
        home, away = teams
        return await self.recaps.recap(
            full_time=full_time,
            history=history,
            home=home,
            away=away,
            score=self.store.score_for(full_time),
            persona=persona,
            language=language,
            foreign_names=foreign_names(home, away, self.store.league),
        )
