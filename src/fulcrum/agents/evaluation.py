"""Records and summaries for Foundry evaluations of the agents' output (AI_PROMPT_DESIGN §7).

`scripts/eval_live.py` writes one `eval_record()` per explained moment to a JSONL file;
`scripts/eval_foundry.py` submits those records to Foundry's built-in quality evaluators (groundedness,
relevance, coherence, fluency) and stores `summarise_eval()`'s output in `data/eval/`. Both helpers are pure
and offline so they are unit-tested without Azure.

Record shape (one JSON object per line):
    query     what the agents were asked: the task plus the moment (MOMENT · FACTS · ROSTER · RECENT · SCORE)
    context   the grounding evidence: the same sections — the only things the copy may rely on
    response  everything published for the moment: the explanation and the four persona variants
    plus metadata (moment_id, type, minute, clock_ms, significance, check_status, latency_ms).
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable, Sequence
from typing import Any

from fulcrum.contracts import Insight

from .context import _common_sections, moment_minute
from .schemas import MomentContext

QUALITY_EVALUATORS: tuple[str, ...] = ("groundedness", "relevance", "coherence", "fluency")
"""Foundry built-in evaluators we run (short names as `agent_framework.foundry.FoundryEvals` accepts them)."""

TASK = (
    "Explain why this football moment matters, using only the numbers in FACTS, then rewrite it for an "
    "analyst, a casual fan and each club's fans."
)


def _context_sections(ctx: MomentContext) -> str:
    # The exact MOMENT · FACTS · ROSTER · RECENT SEQUENCE · SCORE text the Analyst and Editor see.
    return "\n\n".join(_common_sections(ctx))


def format_response(insight: Insight, team_names: dict[str, str] | None = None) -> str:
    """The published copy for one moment as plain text: explanation first, then one line per variant."""
    names = team_names or {}
    lines = [f"{insight.explanation.headline}. {insight.explanation.why_it_matters}"]
    for v in insight.variants:
        label = str(v.persona)
        if v.perspective_team_id:
            label += f" ({names.get(v.perspective_team_id, v.perspective_team_id)} fans)"
        lines.append(f"[{label}] {v.headline}. {v.body} {v.commentary_line}".strip())
    return "\n".join(lines)


def eval_record(ctx: MomentContext) -> dict[str, Any]:
    """One evaluation record for an explained moment (`ctx.insight` must be set)."""
    if ctx.insight is None:
        raise ValueError(f"moment {ctx.moment.moment_id} has no insight yet")
    ins = ctx.insight
    context = _context_sections(ctx)
    return {
        "moment_id": ctx.moment.moment_id,
        "match_id": ctx.moment.match_id,
        "type": str(ctx.moment.type),
        "minute": moment_minute(ctx.moment),
        "clock_ms": ctx.moment.clock_ms,
        "significance": round(ctx.moment.significance, 3),
        "check_status": str(ins.check_status),
        "latency_ms": ins.latency_ms,
        "query": f"{TASK}\n\n{context}",
        "context": context,
        "response": format_response(ins, {ctx.home.team_id: ctx.home.name, ctx.away.team_id: ctx.away.name}),
    }


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Attribute or key access, so summaries work on SDK objects and plain dicts alike."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _json_field(messages: Any, key: str) -> str | None:
    """`key` from the first message whose content is a JSON object holding it as a string."""
    for message in messages or []:
        content = _field(message, "content")
        if not isinstance(content, str):
            continue
        try:
            parsed = json.loads(content)
        except ValueError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get(key), str):
            return parsed[key]
    return None


def judge_reason(sample: Any) -> str | None:
    """The judge's explanation from an evaluator result's `sample`.

    Foundry puts it at `sample.reason` for some evaluators; the built-in quality evaluators return it inside
    the judge's JSON reply, `sample.output[*].content` = `{"reason": ..., "score": ...}`.
    """
    if not isinstance(sample, dict):
        return None
    if isinstance(sample.get("reason"), str):
        return sample["reason"]
    return _json_field(sample.get("output"), "reason")


def judged_response(sample: Any) -> str | None:
    """The `response` the judge saw, from `sample.input[*].content` (a JSON object of the mapped fields)."""
    return _json_field(sample.get("input"), "response") if isinstance(sample, dict) else None


def summarise_eval(
    results: Any,
    records: Sequence[dict[str, Any]],
    *,
    evaluators: Iterable[str] = QUALITY_EVALUATORS,
    judge: str = "",
) -> dict[str, Any]:
    """Summarise an `EvalResults` (or an equivalent dict) into the JSON stored in `data/eval/`.

    Built-in quality evaluators score 1–5 (higher is better) and pass at ≥ 3. Each result item is matched to
    its record by the response the judge saw (submission order if that is missing). Returns per-evaluator
    mean / min / pass rate, the same split by check_status, the lowest-scoring moments and judge tokens.
    """
    evaluators = list(evaluators)
    items = list(_field(results, "items", None) or [])
    by_response = {r.get("response"): r for r in records}
    per_item: list[dict[str, Any]] = []
    judge_tokens = 0
    for i, item in enumerate(items):
        rec: dict[str, Any] | None = None
        scores: dict[str, float] = {}
        passed: dict[str, bool | None] = {}
        reasons: dict[str, str] = {}
        for s in _field(item, "scores", None) or []:
            sample = _field(s, "sample")
            rec = rec or by_response.get(judged_response(sample))
            if isinstance(sample, dict):
                judge_tokens += int(_field(sample.get("usage") or {}, "total_tokens", 0) or 0)
            name = str(_field(s, "name", ""))
            value = _num(_field(s, "score"))
            if value is None:
                continue
            scores[name] = value
            passed[name] = _field(s, "passed")
            reason = judge_reason(sample)
            if reason:
                reasons[name] = str(reason)[:1000]
        if rec is None:  # the judge's input didn't identify the record: fall back to submission order
            rec = records[i] if i < len(records) else {}
        per_item.append(
            {
                "moment_id": rec.get("moment_id"),
                "type": rec.get("type"),
                "minute": rec.get("minute"),
                "check_status": rec.get("check_status"),
                "status": str(_field(item, "status", "")),
                "scores": scores,
                "passed": passed,
                "reasons": reasons,
            }
        )

    def stats(rows: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
        vals = [r["scores"][name] for r in rows if name in r["scores"]]
        if not vals:
            return None
        oks = [r["passed"].get(name) for r in rows if name in r["scores"]]
        n_pass = sum(1 for ok in oks if ok)
        return {
            "mean": round(statistics.mean(vals), 2),
            "min": min(vals),
            "n": len(vals),
            "pass_rate_pct": round(100 * n_pass / len(vals), 1),
        }

    by_status: dict[str, dict[str, Any]] = {}
    for status in sorted({r["check_status"] for r in per_item if r["check_status"]}):
        rows = [r for r in per_item if r["check_status"] == status]
        by_status[status] = {"n": len(rows), **{e: (stats(rows, e) or {}).get("mean") for e in evaluators}}

    def low_key(r: dict[str, Any]) -> float:
        return min(r["scores"].values()) if r["scores"] else 99.0

    lowest = sorted((r for r in per_item if r["scores"]), key=low_key)[:5]
    return {
        "eval_id": _field(results, "eval_id", ""),
        "run_id": _field(results, "run_id", ""),
        "status": _field(results, "status", ""),
        "error": _field(results, "error"),
        "report_url": _field(results, "report_url"),
        "judge_deployment": judge,
        "result_counts": _field(results, "result_counts"),
        "items": len(per_item),
        "judge_tokens": judge_tokens,
        "evaluators": {e: stats(per_item, e) for e in evaluators},
        "by_check_status": by_status,
        "lowest": [
            {k: r[k] for k in ("moment_id", "type", "minute", "check_status", "scores", "reasons")} for r in lowest
        ],
        "per_item": per_item,
    }
