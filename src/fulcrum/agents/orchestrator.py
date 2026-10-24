"""The explain-moment workflow (AI_PROMPT_DESIGN §2) and the per-match ordered worker.

```
  [analyst] ──▶ [persona_editor] ──▶ [fact_checker] ──pass──▶ [cue_producer] ──▶ result
      ▲  ▲               ▲                 │fail, attempt 1
      │  └───────────────┴──── issues ─────┘   (analyst if the explanation failed, else editor only)
      │                                    │fail, attempt 2 / LLM error twice / timeout
      └─ LLM error, attempt 1              └──▶ [template_fallback] ──▶ [cue_producer]
```

Mapping to Microsoft Agent Framework (WS3b): each `Step` is one workflow executor (its `name` is the executor
id), `MomentContext` is the shared state, and `EDGES` are `WorkflowBuilder.add_edge(source, target,
condition=...)` calls, evaluated in order (first match wins). Nothing else carries routing logic.

Failure policy:
- fact-check failure → one repair attempt with the issues appended to the prompt;
- LLM error → counts as a failed attempt (retried once, then fallback);
- timeout (per call, default 6 s, and bounded by the run's total budget) → straight to fallback, because
  the latency budget is already spent;
- an unexpected exception inside a step → fallback (the demo must never stall on a bug).
Every fallback is logged with its reasons and recorded in `Insight.check_notes`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

from fulcrum.contracts import CheckStatus, Explanation, Insight
from fulcrum.llm import LLMClient

from .context import analyst_prompt, editor_prompt, review_prompt
from .cues import build_cues
from .factcheck import check_insight_copy, context_sheet, required_variants
from .prompts import FACT_REVIEWER, MOMENT_ANALYST, PERSONA_EDITOR, PromptSpec
from .schemas import EditorOutput, MomentContext, ReviewOutput
from .templates import fallback_explanation, fallback_variants

log = logging.getLogger("fulcrum.agents")

MAX_NOTE_ISSUES = 8
MAX_STEPS = 16


@dataclass
class OrchestratorConfig:
    """Tunables for one workflow run."""

    call_timeout_s: float = 6.0  # per LLM call (AI_PROMPT_DESIGN §5)
    total_budget_s: float = 15.0  # whole run; later calls get min(call_timeout, remaining)
    max_attempts: int = 2  # first try + one repair
    review_enabled: bool = field(default_factory=lambda: os.getenv("FULCRUM_LLM_REVIEW", "0") == "1")
    review_min_significance: float = 0.6


class LLMTimeout(TimeoutError):
    """An LLM call exceeded its time budget."""


async def call_llm[T: BaseModel](
    llm: LLMClient,
    spec: PromptSpec,
    *,
    user: str,
    schema: type[T],
    config: OrchestratorConfig,
    deadline: float | None = None,
    **render: str,
) -> T:
    """One structured LLM call with the per-call timeout, capped by the run's `deadline` (monotonic)."""
    timeout = config.call_timeout_s
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
    if timeout <= 0:
        raise LLMTimeout(f"{spec.agent_name}: run budget of {config.total_budget_s}s exhausted")
    try:
        return await asyncio.wait_for(
            llm.structured(system=spec.render(**render), user=user, schema=schema, agent_name=spec.agent_name),
            timeout=timeout,
        )
    except TimeoutError as e:
        raise LLMTimeout(f"{spec.agent_name}: timed out after {timeout:.1f}s") from e


def _record_failure(ctx: MomentContext, agent: str, exc: BaseException) -> None:
    if isinstance(exc, LLMTimeout):
        ctx.timed_out = True
        ctx.error = str(exc)
    else:
        ctx.error = f"{agent}: {type(exc).__name__}: {exc}"
    ctx.notes.append(f"attempt {ctx.attempt}: {ctx.error}")


# --------------------------------------------------------------------------- #
# Steps (one per Agent Framework executor)
# --------------------------------------------------------------------------- #


class Step(Protocol):
    name: str

    async def run(self, ctx: MomentContext) -> None:
        """Read inputs from `ctx`, write outputs to `ctx`. Must not raise for expected failures."""


@dataclass
class AnalystStep:
    """Moment Analyst (LLM): Moment + context → Explanation."""

    llm: LLMClient
    config: OrchestratorConfig
    name: str = "analyst"

    async def run(self, ctx: MomentContext) -> None:
        ctx.error = None
        issues = ctx.issues if ctx.attempt > 1 else []
        try:
            ctx.explanation = await call_llm(
                self.llm,
                MOMENT_ANALYST,
                user=analyst_prompt(ctx, issues),
                schema=Explanation,
                config=self.config,
                deadline=ctx._cache.get("deadline"),
            )
        except Exception as e:  # noqa: BLE001 — any LLM failure is routed, not raised
            _record_failure(ctx, MOMENT_ANALYST.agent_name, e)


@dataclass
class PersonaEditorStep:
    """Persona Editor (LLM): Explanation → four InsightVariants (analyst, casual, club_fan × 2)."""

    llm: LLMClient
    config: OrchestratorConfig
    name: str = "persona_editor"

    async def run(self, ctx: MomentContext) -> None:
        ctx.error = None
        assert ctx.explanation is not None, "persona_editor runs after a successful analyst"
        issues = ctx.issues if ctx.attempt > 1 else []
        try:
            out = await call_llm(
                self.llm,
                PERSONA_EDITOR,
                user=editor_prompt(ctx, ctx.explanation, issues),
                schema=EditorOutput,
                config=self.config,
                deadline=ctx._cache.get("deadline"),
            )
        except Exception as e:  # noqa: BLE001
            _record_failure(ctx, PERSONA_EDITOR.agent_name, e)
            return
        order = {pair: i for i, pair in enumerate(required_variants(ctx.home.team_id, ctx.away.team_id))}
        variants = [v.model_copy(update={"language": "en"}) for v in out.variants]  # we always ask for English
        ctx.variants = sorted(variants, key=lambda v: order.get((str(v.persona), v.perspective_team_id), 99))


@dataclass
class FactCheckStep:
    """Fact-Checker: deterministic rules, then (optionally) the LLM reviewer for big moments."""

    llm: LLMClient
    config: OrchestratorConfig
    name: str = "fact_checker"

    async def run(self, ctx: MomentContext) -> None:
        result = check_insight_copy(
            ctx.explanation, ctx.variants, context_sheet(ctx), home_id=ctx.home.team_id, away_id=ctx.away.team_id
        )
        issues, explanation_failed = result.messages, result.explanation_failed
        if not issues and self._review_wanted(ctx):
            issues = await self._review(ctx)
            explanation_failed = bool(issues)
        ctx.issues, ctx.explanation_failed = issues, explanation_failed
        if issues:
            shown = issues[:MAX_NOTE_ISSUES] + (
                [f"… +{len(issues) - MAX_NOTE_ISSUES} more"] if len(issues) > MAX_NOTE_ISSUES else []
            )
            ctx.notes += [f"attempt {ctx.attempt}: fact-check: {i}" for i in shown]
        else:
            ctx.status = CheckStatus.PASSED if ctx.attempt == 1 else CheckStatus.REPAIRED
            if ctx.status == CheckStatus.REPAIRED:
                ctx.notes.append(f"repaired on attempt {ctx.attempt}")

    def _review_wanted(self, ctx: MomentContext) -> bool:
        return self.config.review_enabled and ctx.moment.significance >= self.config.review_min_significance

    async def _review(self, ctx: MomentContext) -> list[str]:
        try:
            verdict = await call_llm(
                self.llm,
                FACT_REVIEWER,
                user=review_prompt(ctx),
                schema=ReviewOutput,
                config=self.config,
                deadline=ctx._cache.get("deadline"),
            )
        except Exception as e:  # noqa: BLE001 — the reviewer is advisory; never block on it
            ctx.notes.append(f"attempt {ctx.attempt}: reviewer skipped ({type(e).__name__}: {e})")
            return []
        if verdict.supported:
            return []
        return [f"reviewer: {i}" for i in verdict.issues] or ["reviewer: copy not supported by FACTS"]


@dataclass
class TemplateFallbackStep:
    """Deterministic templates — always valid copy."""

    name: str = "template_fallback"

    async def run(self, ctx: MomentContext) -> None:
        reason = ctx.error or ("; ".join(ctx.issues[:3]) if ctx.issues else "unknown")
        ctx.explanation = fallback_explanation(ctx)
        ctx.variants = fallback_variants(ctx)
        ctx.status = CheckStatus.FALLBACK
        ctx.notes.append(f"fallback: templates used after attempt {ctx.attempt} ({reason})")
        log.warning(
            "fallback for moment %s (%s, match %s): %s",
            ctx.moment.moment_id,
            ctx.moment.type,
            ctx.moment.match_id,
            " | ".join(ctx.notes),
        )


@dataclass
class CueProducerStep:
    """Builds the Insight and its OverlayCues from the checked copy."""

    name: str = "cue_producer"

    async def run(self, ctx: MomentContext) -> None:
        assert ctx.explanation is not None and ctx.status is not None
        started = ctx._cache.get("started", time.monotonic())
        ctx.latency_ms = int((time.monotonic() - started) * 1000)
        ctx.insight = Insight(
            match_id=ctx.moment.match_id,
            moment_id=ctx.moment.moment_id,
            clock_ms=ctx.moment.clock_ms,
            explanation=ctx.explanation,
            variants=ctx.variants,
            check_status=ctx.status,
            check_notes=ctx.notes,
            agent_trace_id=None,  # TODO(azure): OpenTelemetry trace id once tracing lands (WS3b)
            latency_ms=ctx.latency_ms,
        )
        ctx.cues = build_cues(ctx.insight, ctx.moment, ctx.home, ctx.away)


# --------------------------------------------------------------------------- #
# Edges (WorkflowBuilder.add_edge equivalents) and the runner
# --------------------------------------------------------------------------- #

Condition = Callable[[MomentContext], bool]


@dataclass(frozen=True)
class Edge:
    """A conditional transition. `retry=True` edges start the next attempt."""

    source: str
    target: str
    condition: Condition
    retry: bool = False


def _ok(c: MomentContext) -> bool:
    return c.error is None


def _failed_can_retry(c: MomentContext) -> bool:
    return c.error is not None and c.can_retry


def _failed(c: MomentContext) -> bool:
    return c.error is not None


START = "analyst"
EDGES: tuple[Edge, ...] = (
    Edge("analyst", "persona_editor", _ok),
    Edge("analyst", "analyst", _failed_can_retry, retry=True),
    Edge("analyst", "template_fallback", _failed),
    Edge("persona_editor", "fact_checker", _ok),
    Edge("persona_editor", "persona_editor", _failed_can_retry, retry=True),
    Edge("persona_editor", "template_fallback", _failed),
    Edge("fact_checker", "cue_producer", lambda c: not c.issues),
    Edge("fact_checker", "analyst", lambda c: c.can_retry and c.explanation_failed, retry=True),
    Edge("fact_checker", "persona_editor", lambda c: c.can_retry, retry=True),
    Edge("fact_checker", "template_fallback", lambda c: True),
    Edge("template_fallback", "cue_producer", lambda c: True),
)


class ExplainMomentWorkflow:
    """Runs the steps along `EDGES` until a step has no outgoing edge (cue_producer)."""

    def __init__(
        self,
        llm: LLMClient,
        config: OrchestratorConfig | None = None,
        steps: Mapping[str, Step] | None = None,
        edges: tuple[Edge, ...] = EDGES,
    ) -> None:
        self.config = config or OrchestratorConfig()
        self.steps: dict[str, Step] = dict(
            steps
            or {
                s.name: s
                for s in (
                    AnalystStep(llm, self.config),
                    PersonaEditorStep(llm, self.config),
                    FactCheckStep(llm, self.config),
                    TemplateFallbackStep(),
                    CueProducerStep(),
                )
            }
        )
        self.edges = edges

    def _next(self, source: str, ctx: MomentContext) -> Edge | None:
        return next((e for e in self.edges if e.source == source and e.condition(ctx)), None)

    async def run(self, ctx: MomentContext) -> MomentContext:
        """Explain one moment. Always returns `ctx` with `insight` and `cues` set."""
        now = time.monotonic()
        ctx.max_attempts = self.config.max_attempts
        ctx._cache.setdefault("started", now)
        ctx._cache.setdefault("deadline", now + self.config.total_budget_s)
        current: str | None = START
        for _ in range(MAX_STEPS):
            if current is None:
                break
            ctx.trace.append(current)
            try:
                await self.steps[current].run(ctx)
            except Exception as e:  # noqa: BLE001 — a bug in a step must not lose the moment
                if current in ("template_fallback", "cue_producer"):
                    raise
                log.exception("step %s crashed on moment %s", current, ctx.moment.moment_id)
                ctx.error = f"{current} crashed: {type(e).__name__}: {e}"
                ctx.notes.append(f"attempt {ctx.attempt}: {ctx.error}")
                current = "template_fallback"  # never retry a crash
                continue
            edge = self._next(current, ctx)
            if edge is not None and edge.retry:
                ctx.attempt += 1
            current = edge.target if edge else None
        if ctx.insight is None:  # pragma: no cover — only if the edge table is broken
            raise RuntimeError(f"workflow ended without an insight (trace {ctx.trace})")
        return ctx


# --------------------------------------------------------------------------- #
# Ordered worker per match
# --------------------------------------------------------------------------- #

ResultHandler = Callable[[MomentContext], Awaitable[None]]


class MomentProcessor:
    """Processes moments in arrival order per match, different matches concurrently."""

    def __init__(self, workflow: ExplainMomentWorkflow, on_result: ResultHandler) -> None:
        self.workflow = workflow
        self.on_result = on_result
        self._queues: dict[str, asyncio.Queue[MomentContext]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}

    def submit(self, ctx: MomentContext) -> None:
        """Queue a moment's context (built at arrival time, so score/sequence are as of the moment)."""
        match_id = ctx.moment.match_id
        queue = self._queues.setdefault(match_id, asyncio.Queue())
        queue.put_nowait(ctx)
        if match_id not in self._workers or self._workers[match_id].done():
            self._workers[match_id] = asyncio.create_task(self._work(queue), name=f"agents:{match_id}")

    async def _work(self, queue: asyncio.Queue[MomentContext]) -> None:
        while True:
            ctx = await queue.get()
            try:
                await self.workflow.run(ctx)
                await self.on_result(ctx)
            except Exception:
                log.exception("failed to process moment %s", ctx.moment.moment_id)
            finally:
                queue.task_done()

    async def drain(self) -> None:
        """Wait until every submitted moment has been processed."""
        for queue in list(self._queues.values()):
            await queue.join()

    async def close(self) -> None:
        """Stop all workers (pending moments are dropped)."""
        for task in self._workers.values():
            task.cancel()
        for task in self._workers.values():
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._workers.clear()
