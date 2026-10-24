"""Evaluation records and Foundry evaluation summaries (offline: fakes stand in for EvalResults)."""

import json
from types import SimpleNamespace

import pytest

from fulcrum.agents.evaluation import (
    QUALITY_EVALUATORS,
    eval_record,
    judge_reason,
    judged_response,
    summarise_eval,
)
from fulcrum.agents.service import AgentService
from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import MomentType
from tests.agents.factories import league, make_context, make_moment
from tests.agents.fakes import GOAL_ANALYST, ScriptedLLM, goal_variants


async def explained_context():
    svc = AgentService(
        MemoryBus(),
        ScriptedLLM({"moment_analyst": GOAL_ANALYST, "persona_editor": goal_variants()}),
        league=league(),
        consume_events=False,
    )
    ctx = svc.submit(make_moment(MomentType.GOAL))
    await svc.drain()
    await svc.stop()
    return ctx


async def test_eval_record_carries_query_context_and_all_published_copy():
    ctx = await explained_context()
    rec = eval_record(ctx)
    assert rec["moment_id"] == ctx.moment.moment_id and rec["type"] == "goal"
    assert rec["check_status"] == "passed" and rec["latency_ms"] == ctx.insight.latency_ms
    assert "FACTS" in rec["context"] and "SCORE" in rec["context"]
    assert rec["query"].endswith(rec["context"])  # the task, then exactly the evidence
    assert ctx.insight.explanation.headline in rec["response"]
    for v in ctx.insight.variants:
        assert v.body in rec["response"]
    assert f"({ctx.home.name} fans)" in rec["response"]
    json.dumps(rec)  # JSONL-serialisable


def test_eval_record_needs_an_insight():
    with pytest.raises(ValueError):
        eval_record(make_context())


def judge_sample(response: str, reason: str, score: int) -> dict:
    return {
        "usage": {"total_tokens": 100},
        "input": [{"role": "user", "content": json.dumps({"query": "q", "response": response})}],
        "output": [{"role": "assistant", "content": json.dumps({"reason": reason, "score": score})}],
    }


def score(name: str, value: float, response: str, reason: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(name=name, score=value, passed=value >= 3, sample=judge_sample(response, reason, value))


def test_judge_sample_parsing():
    s = judge_sample("resp", "because", 4)
    assert judge_reason(s) == "because" and judged_response(s) == "resp"
    assert judge_reason({"reason": "direct"}) == "direct"
    assert judge_reason(None) is None and judged_response({"input": [{"content": "not json"}]}) is None


def test_summarise_matches_items_by_response_and_aggregates():
    records = [
        {"moment_id": "a", "type": "goal", "minute": "12'", "check_status": "passed", "response": "A"},
        {"moment_id": "b", "type": "wp_swing", "minute": "50'", "check_status": "repaired", "response": "B"},
    ]
    # Returned out of submission order: matching must use the judged response, not the position.
    items = [
        SimpleNamespace(
            status="completed", scores=[score("groundedness", 2, "B", "adds a claim"), score("fluency", 4, "B")]
        ),
        SimpleNamespace(status="completed", scores=[score("groundedness", 5, "A"), score("fluency", 3, "A")]),
    ]
    results = SimpleNamespace(
        eval_id="e1",
        run_id="r1",
        status="completed",
        error=None,
        report_url="https://portal/x",
        result_counts={"passed": 1, "failed": 1, "errored": 0, "total": 2},
        items=items,
    )
    out = summarise_eval(results, records, evaluators=["groundedness", "fluency"], judge="fast")
    assert out["eval_id"] == "e1" and out["report_url"] == "https://portal/x" and out["judge_deployment"] == "fast"
    assert out["items"] == 2 and out["judge_tokens"] == 400
    g = out["evaluators"]["groundedness"]
    assert g == {"mean": 3.5, "min": 2, "n": 2, "pass_rate_pct": 50.0}
    assert out["by_check_status"]["repaired"] == {"n": 1, "groundedness": 2, "fluency": 4}
    assert out["lowest"][0]["moment_id"] == "b" and out["lowest"][0]["reasons"]["groundedness"] == "adds a claim"
    json.dumps(out)


def test_summarise_handles_a_failed_run():
    out = summarise_eval({"status": "failed", "error": "quota", "items": []}, [])
    assert out["status"] == "failed" and out["error"] == "quota" and out["items"] == 0
    assert out["evaluators"] == {e: None for e in QUALITY_EVALUATORS}
