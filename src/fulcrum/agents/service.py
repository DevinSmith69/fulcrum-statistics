"""Agents service: `moments` (+ `match-events`) in → `InsightsEnvelope`s out on `insights`.

Runs standalone (`python -m fulcrum.agents`, one Container App) or in-process in single-process dev mode, where
the API lifespan creates one `AgentService` on the shared bus and calls `start()`. The API also uses it for the
on-demand paths: `translate`, `translate_cues`, `player_lens`, `recap`, `ask` and `register_match`.

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
    AskAnswer,
    CheckStatus,
    Explanation,
    GraphicProposal,
    Insight,
    InsightsEnvelope,
    InsightVariant,
    MatchConfig,
    MatchEvent,
    MatchState,
    Moment,
    MomentType,
    OverlayCue,
    Persona,
    Recap,
    Team,
    WinProbPoint,
)
from fulcrum.llm import LLMClient

from .context import MatchContextStore, foreign_names, load_teams
from .orchestrator import ExplainMomentWorkflow, MomentProcessor, OrchestratorConfig
from .player_lens import PlayerLens
from .producer import GraphicsProducer, graphics_pack
from .prompts import FACT_REVIEWER, GRAPHICS_PRODUCER, MOMENT_ANALYST, PERSONA_EDITOR, STUDIO_ANALYST, PromptSpec
from .recap import RecapService
from .schemas import EditorOutput, GraphicOutput, MomentContext, ReviewOutput, StudioAnswerOutput
from .studio import StudioAnalyst, build_pack
from .translate import Translator, normalise_language

log = logging.getLogger("fulcrum.agents")

CONSUMER_GROUP = "agents"
MAX_PENDING = 50  # per match: moments waiting for their teams to be known

# Roles with a fixed system prompt, published as Foundry prompt agents by FoundryLLM (llm/foundry.py).
_PROMPT_AGENTS: tuple[tuple[PromptSpec, type], ...] = (
    (MOMENT_ANALYST, Explanation),
    (PERSONA_EDITOR, EditorOutput),
    (FACT_REVIEWER, ReviewOutput),
    (STUDIO_ANALYST, StudioAnswerOutput),
    (GRAPHICS_PRODUCER, GraphicOutput),
)
PROMPT_AGENT_ROLES = tuple((spec.agent_name, spec.system, spec.version, schema) for spec, schema in _PROMPT_AGENTS)


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
        self.player_lenses = PlayerLens(llm, self.config)
        self.studio = StudioAnalyst(llm, self.config)
        self.graphics = GraphicsProducer(llm, self.config)
        self._seen: set[str] = set()
        self._insights: dict[str, tuple[Moment, Insight, str]] = {}  # id -> (moment, insight, score)
        self._pending: dict[str, list[Moment]] = {}
        self._tasks: list[asyncio.Task[None]] = []

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Subscribe to the input topics (queues are registered before this returns, so nothing is missed)."""
        if (warm_up := getattr(self.llm, "warm_up", None)) is not None:
            try:
                # FoundryLLM: token + clients (so the first moment isn't a cold start) + prompt agents
                await warm_up(PROMPT_AGENT_ROLES)
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

    def update_insight(self, insight: Insight) -> bool:
        """Replace a published insight's copy (a producer edit), so translations start from the edited text."""
        found = self._insights.get(insight.insight_id)
        if found is None:
            return False
        moment, _, score = found
        self._insights[insight.insight_id] = (moment, insight, score)
        history = self.store.get(moment.match_id).history
        for n, (m, _i) in enumerate(history):
            if m.moment_id == moment.moment_id:
                history[n] = (m, insight)
        return True

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

    async def player_lens(self, insight_id: str, player_id: str, language: str = "en") -> list[InsightVariant]:
        """The moment retold through `player_id`, in `language` (on demand, cached).

        Empty when the id is unknown or the player was not directly involved (the caller serves the casual
        variant); raises PlayerLensUnavailable if the LLM failed, so the miss isn't cached.
        Non-English goes through the Translator (keyed `<insight_id>~<player_id>`).
        """
        found = self._insights.get(insight_id)
        teams = self._teams(found[0].match_id) if found else None
        if found is None or teams is None:
            return []
        moment, insight, score = found
        home, away = teams
        names = foreign_names(home, away, self.store.league)
        variant = await self.player_lenses.variant(
            insight, moment, home, away, player_id, foreign_names=names, score=score
        )
        if variant is None:
            return []
        lang = normalise_language(language)
        if lang == "en" or lang.startswith("en-"):
            return [variant]
        lensed = insight.model_copy(update={"insight_id": f"{insight.insight_id}~{player_id}", "variants": [variant]})
        return await self.translator.translate(lensed, moment, home, away, lang, foreign_names=names, score=score)

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

    async def ask(
        self,
        match_id: str,
        question: str,
        *,
        persona: Persona | str = Persona.CASUAL,
        language: str = "en",
        favourite_team_id: str | None = None,
        state: MatchState | None = None,
    ) -> AskAnswer:
        """Ask the studio: answer a viewer's question from this match's data only (cached; never raises).

        `state` is the latest `MatchState` (the API's read model has it; the agents don't consume that topic).
        Moments and their insights come from this service's own history. Unknown teams → the safe reply.
        """
        teams = self._teams(match_id)
        if teams is None:
            return self.studio.reply(match_id, question, persona=str(Persona(persona)), language=language)
        home, away = teams
        mc = self.store.get(match_id)
        score = f"{state.score_home}-{state.score_away}" if state is not None else mc.score
        pack = build_pack(
            match_id,
            home,
            away,
            state=state,
            history=mc.history,
            score=score,
            foreign_names=foreign_names(home, away, self.store.league),
        )
        return await self.studio.ask(
            pack, question, persona=persona, language=language, favourite_team_id=favourite_team_id
        )

    async def graphic(
        self,
        match_id: str,
        request: str,
        *,
        state: MatchState | None = None,
        winprob: Sequence[WinProbPoint] = (),
    ) -> GraphicProposal:
        """Producer console: one overlay graphic for a plain-language request, from this match's data (never raises).

        `state` / `winprob` come from the API's read model (the agents don't consume `match-state`).
        """
        teams = self._teams(match_id)
        if teams is None:
            return GraphicProposal(
                match_id=match_id,
                request=request,
                ok=False,
                check_status=CheckStatus.FALLBACK,
                refusal="The match's teams aren't known yet. Try again in a moment.",
            )
        home, away = teams
        mc = self.store.get(match_id)
        score = f"{state.score_home}-{state.score_away}" if state is not None else mc.score
        base = build_pack(
            match_id,
            home,
            away,
            state=state,
            history=mc.history,
            score=score,
            foreign_names=foreign_names(home, away, self.store.league),
        )
        pack = graphics_pack(base, request, state=state, winprob=winprob)
        return await self.graphics.propose(pack, request, seq=state.seq if state is not None else 0)

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
