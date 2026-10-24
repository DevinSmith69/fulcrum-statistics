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
- timeout (per call, default 8 s, and bounded by the run's total budget) → straight to fallback, because
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

from agent_framework import Case, Default, Executor, Workflow, WorkflowBuilder, WorkflowContext, handler
from pydantic import BaseModel

from fulcrum.contracts import CheckStatus, Explanation, Insight, InsightVariant
from fulcrum.llm import LLMClient
from fulcrum.telemetry import current_trace_id

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

    call_timeout_s: float = 8.0  # per LLM call (AI_PROMPT_DESIGN §5; 6 s timed out ~4% under concurrent matches)
    total_budget_s: float = 15.0  # whole run; later calls get min(call_timeout, remaining)
    # The recap runs once at full time on the stronger (reasoning) deployment, which needs ~25-40 s; it is
    # not on the live moment path, so it gets its own, longer budget.
    recap_call_timeout_s: float = 60.0
    recap_budget_s: float = 110.0
    max_attempts: int = 2  # first try + one repair
    review_enabled: bool = field(default_factory=lambda: os.getenv("FULCRUM_LLM_REVIEW", "0") == "1")
    # Write the four persona variants as four concurrent Editor calls (one variant each) instead of one call
    # that writes all four: same tokens in, a quarter of the tokens out per call, so roughly half the wall
    # time on Foundry. Off by default offline (tests script one Editor call); on with FULCRUM_LLM=foundry.
    editor_fanout: bool = field(default_factory=lambda: _editor_fanout_default())
    review_min_significance: float = 0.6


def _editor_fanout_default() -> bool:
    default = "1" if os.getenv("FULCRUM_LLM", "fake") == "foundry" else "0"
    return (os.getenv("FULCRUM_EDITOR_FANOUT") or default) == "1"  # empty = unset


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
        pairs = required_variants(ctx.home.team_id, ctx.away.team_id)
        try:
            if self.config.editor_fanout:
                outs = await _gather_or_cancel([self._call(ctx, issues, only=pair) for pair in pairs])
                variants = [_pick(out.variants, pair) for out, pair in zip(outs, pairs, strict=True)]
                variants = [v for v in variants if v is not None]
            else:
                variants = list((await self._call(ctx, issues)).variants)
        except Exception as e:  # noqa: BLE001
            _record_failure(ctx, PERSONA_EDITOR.agent_name, e)
            return
        order = {pair: i for i, pair in enumerate(pairs)}
        variants = [v.model_copy(update={"language": "en"}) for v in variants]  # we always ask for English
        ctx.variants = sorted(variants, key=lambda v: order.get((str(v.persona), v.perspective_team_id), 99))

    async def _call(
        self, ctx: MomentContext, issues: list[str], only: tuple[str, str | None] | None = None
    ) -> EditorOutput:
        assert ctx.explanation is not None
        return await call_llm(
            self.llm,
            PERSONA_EDITOR,
            user=editor_prompt(ctx, ctx.explanation, issues, only=only),
            schema=EditorOutput,
            config=self.config,
            deadline=ctx._cache.get("deadline"),
        )


def _pick(variants: list[InsightVariant], pair: tuple[str, str | None]) -> InsightVariant | None:
    """The variant a single-variant Editor call was asked for, or None (then the set check asks for a repair).

    A neutral variant with the wrong persona label is relabelled; one written for the *other* club is not, since
    its partisan copy would cheer for the wrong side.
    """
    for v in variants:
        if (str(v.persona), v.perspective_team_id) == pair:
            return v
    if not variants or variants[0].perspective_team_id not in (None, pair[1]):
        return None
    return variants[0].model_copy(update={"persona": pair[0], "perspective_team_id": pair[1]})


async def _gather_or_cancel[T](coros: list[Awaitable[T]]) -> list[T]:
    """`asyncio.gather` that cancels the remaining calls as soon as one fails (no tokens spent on a lost run)."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


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
            agent_trace_id=ctx._cache.get("trace_id"),  # the workflow run's OpenTelemetry trace (None if off)
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


class _StepExecutor(Executor):
    """Agent Framework executor wrapping one `Step`.

    It runs the step, picks the outgoing edge (first matching `Edge` in `EDGES` order), bumps the attempt on a
    retry edge, and records the choice in the context so the workflow's switch-case edge group routes to it.
    Deciding in the source keeps `EDGES` the single source of routing truth: the attempt counter must change
    *between* evaluating a retry condition and running the target, which a pure edge condition cannot do.
    """

    def __init__(self, step: Step, edges: tuple[Edge, ...]) -> None:
        super().__init__(id=step.name)
        self.step = step
        self.out_edges = tuple(e for e in edges if e.source == step.name)

    @handler
    async def run(self, ctx: MomentContext, wctx: WorkflowContext[MomentContext, MomentContext]) -> None:
        ctx.trace.append(self.id)
        ctx._cache.setdefault("trace_id", current_trace_id())
        crashed = False
        try:
            await self.step.run(ctx)
        except Exception as e:  # noqa: BLE001 — a bug in a step must not lose the moment
            if self.id in ("template_fallback", "cue_producer"):
                raise
            log.exception("step %s crashed on moment %s", self.id, ctx.moment.moment_id)
            ctx.error = f"{self.id} crashed: {type(e).__name__}: {e}"
            ctx.notes.append(f"attempt {ctx.attempt}: {ctx.error}")
            crashed = True  # never retry a crash
        edge = (
            Edge(self.id, "template_fallback", _failed)
            if crashed
            else next((e for e in self.out_edges if e.condition(ctx)), None)
        )
        if edge is None:
            await wctx.yield_output(ctx)
            return
        if edge.retry:
            ctx.attempt += 1
        ctx._cache["route"] = edge.target
        await wctx.send_message(ctx)


def _routed_to(target: str) -> Condition:
    return lambda c: c._cache.get("route") == target


def build_workflow(steps: Mapping[str, Step], edges: tuple[Edge, ...] = EDGES) -> Workflow:
    """The explain-moment graph as a Microsoft Agent Framework `Workflow` (one executor per step)."""
    executors = {name: _StepExecutor(step, edges) for name, step in steps.items()}
    builder = WorkflowBuilder(start_executor=executors[START], max_iterations=MAX_STEPS, name="explain_moment")
    for source, executor in executors.items():
        targets = list(dict.fromkeys(e.target for e in edges if e.source == source))
        if source in ("analyst", "persona_editor", "fact_checker") and "template_fallback" not in targets:
            targets.append("template_fallback")  # crash route
        if len(targets) == 1:
            builder.add_edge(executor, executors[targets[0]], condition=_routed_to(targets[0]))
        elif targets:  # Agent Framework wants exactly one Default; the last route (fallback) takes that role
            *cases, last = targets
            builder.add_switch_case_edge_group(
                executor,
                [*(Case(condition=_routed_to(t), target=executors[t]) for t in cases), Default(target=executors[last])],
            )
    return builder.build()


class ExplainMomentWorkflow:
    """Explains one moment by running the Agent Framework workflow built from `EDGES`.

    A built `Workflow` refuses concurrent runs, and moments from different matches run in parallel, so idle
    instances are pooled and a new one is built when all are busy.
    """

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
        self._idle: list[Workflow] = []

    async def run(self, ctx: MomentContext) -> MomentContext:
        """Explain one moment. Always returns `ctx` with `insight` and `cues` set."""
        now = time.monotonic()
        ctx.max_attempts = self.config.max_attempts
        ctx._cache.setdefault("started", now)
        ctx._cache.setdefault("deadline", now + self.config.total_budget_s)
        workflow = self._idle.pop() if self._idle else build_workflow(self.steps, self.edges)
        try:
            result = await workflow.run(ctx)
        finally:
            self._idle.append(workflow)
        outputs = result.get_outputs()
        if ctx.insight is None or not outputs:  # pragma: no cover — only if the edge table is broken
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
