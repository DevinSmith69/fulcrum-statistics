import asyncio

import pytest

from fulcrum.agents.factcheck import FactSheet, check_insight_copy
from fulcrum.agents.orchestrator import (
    EDGES,
    START,
    ExplainMomentWorkflow,
    MomentProcessor,
    OrchestratorConfig,
)
from fulcrum.contracts import CheckStatus, MomentType
from fulcrum.llm import FakeLLM
from tests.agents.factories import AWAY, HOME, make_context, make_moment, make_store
from tests.agents.fakes import GOAL_ANALYST, ScriptedLLM, goal_variants

GOOD = {"moment_analyst": GOAL_ANALYST, "persona_editor": goal_variants()}


def wf(llm, **cfg) -> ExplainMomentWorkflow:
    return ExplainMomentWorkflow(llm, OrchestratorConfig(**{"review_enabled": False, **cfg}))


def assert_valid_output(ctx):
    sheet = FactSheet.build(
        ctx.moment.facts, score=ctx.score, clock_ms=ctx.moment.clock_ms, foreign_names=ctx.foreign_names
    )
    result = check_insight_copy(ctx.explanation, ctx.variants, sheet, home_id=HOME, away_id=AWAY)
    assert result.passed, result.messages
    assert ctx.insight is not None and ctx.insight.check_status == ctx.status
    assert ctx.insight.variants == ctx.variants and ctx.insight.check_notes == ctx.notes
    assert ctx.cues and all(c.insight_id == ctx.insight.insight_id for c in ctx.cues)


# --------------------------------------------------------------------------- PASSED


async def test_golden_goal_passes_first_time():
    llm = ScriptedLLM(GOOD)
    ctx = await wf(llm).run(make_context(make_moment(MomentType.GOAL)))
    assert ctx.status == CheckStatus.PASSED
    assert ctx.trace == ["analyst", "persona_editor", "fact_checker", "cue_producer"]
    assert llm.agents_called() == ["moment_analyst", "persona_editor"]
    assert ctx.explanation.headline == GOAL_ANALYST["headline"]
    assert [(v.persona, v.perspective_team_id) for v in ctx.variants] == [
        ("analyst", None),
        ("casual", None),
        ("club_fan", HOME),
        ("club_fan", AWAY),
    ]
    assert ctx.notes == [] and ctx.insight.latency_ms is not None
    assert_valid_output(ctx)


async def test_prompts_carry_facts_roster_sequence_and_score():
    llm = ScriptedLLM(GOOD)
    ctx = make_context(make_moment(MomentType.GOAL))
    ctx.recent_sequence = ["66' Aldane (NBA) pass → Brennick (diff 7.8, success)"]
    await wf(llm).run(ctx)
    analyst_user = llm.calls[0]["user"]
    for section in ("MOMENT", "FACTS", "ROSTER", "RECENT SEQUENCE", "SCORE"):
        assert section in analyst_user
    assert "xg | Shot xG | 0.34 | xG" in analyst_user
    assert "NBA-10 Brennick" in analyst_user and "Aldane (NBA) pass" in analyst_user
    assert "Northbridge Athletic 1-0 Riverside Rovers" in analyst_user
    assert "Fix these issues" not in analyst_user
    editor_user = llm.calls[1]["user"]
    assert "EXPLANATION" in editor_user and GOAL_ANALYST["headline"] in editor_user
    assert "perspective_team_id RVR" in editor_user
    assert "Use ONLY numbers that appear in FACTS" in llm.calls[0]["system"]


async def test_editor_output_is_put_in_canonical_order():
    shuffled = goal_variants()
    shuffled["variants"].reverse()
    ctx = await wf(ScriptedLLM({"moment_analyst": GOAL_ANALYST, "persona_editor": shuffled})).run(make_context())
    assert ctx.status == CheckStatus.PASSED
    assert [v.persona for v in ctx.variants] == ["analyst", "casual", "club_fan", "club_fan"]


# --------------------------------------------------------------------------- REPAIRED


async def test_repair_when_editor_invents_a_number():
    bad = goal_variants(casual_body="Brennick scores a 0.52 xG beauty and Northbridge lead 1-0!")
    llm = ScriptedLLM({"moment_analyst": GOAL_ANALYST, "persona_editor": [bad, goal_variants()]})
    ctx = await wf(llm).run(make_context())
    assert ctx.status == CheckStatus.REPAIRED
    # only the editor's copy failed, so only the editor is re-run
    assert llm.agents_called() == ["moment_analyst", "persona_editor", "persona_editor"]
    assert ctx.trace == ["analyst", "persona_editor", "fact_checker", "persona_editor", "fact_checker", "cue_producer"]
    repair_prompt = llm.calls[2]["user"]
    assert (
        "Fix these issues" in repair_prompt and "variants[casual].body: number '0.52' is not in FACTS" in repair_prompt
    )
    assert ctx.notes == [
        "attempt 1: fact-check: variants[casual].body: number '0.52' is not in FACTS",
        "repaired on attempt 2",
    ]
    assert_valid_output(ctx)


async def test_repair_when_analyst_invents_a_number_reruns_analyst_and_editor():
    bad = {**GOAL_ANALYST, "why_it_matters": "Brennick's goal is his 12th of the season."}
    llm = ScriptedLLM({"moment_analyst": [bad, GOAL_ANALYST], "persona_editor": goal_variants()})
    ctx = await wf(llm).run(make_context())
    assert ctx.status == CheckStatus.REPAIRED
    assert llm.agents_called() == ["moment_analyst", "persona_editor", "moment_analyst", "persona_editor"]
    assert "number '12' is not in FACTS" in llm.calls[2]["user"]
    assert_valid_output(ctx)


async def test_repair_after_one_llm_error():
    llm = ScriptedLLM(GOOD, fail_times=1)
    ctx = await wf(llm).run(make_context())
    assert ctx.status == CheckStatus.REPAIRED
    assert ctx.trace[:2] == ["analyst", "analyst"]
    assert ctx.notes[0].startswith("attempt 1: moment_analyst: RuntimeError: FakeLLM simulated failure")


async def test_unknown_fact_key_and_missing_variant_are_repaired():
    bad_expl = {**GOAL_ANALYST, "key_fact_keys": ["xg", "wp_home_after_goal"]}
    three = goal_variants()
    three["variants"] = three["variants"][:3]
    llm = ScriptedLLM({"moment_analyst": [bad_expl, GOAL_ANALYST], "persona_editor": [three, goal_variants()]})
    ctx = await wf(llm).run(make_context())
    assert ctx.status == CheckStatus.REPAIRED
    assert any("'wp_home_after_goal' is not a FACT key" in n for n in ctx.notes)
    assert any("missing the club_fan for RVR variant" in n for n in ctx.notes)


# --------------------------------------------------------------------------- FALLBACK


async def test_fallback_after_two_llm_failures():
    llm = FakeLLM(GOOD, fail_times=2)
    ctx = await wf(llm).run(make_context())
    assert ctx.status == CheckStatus.FALLBACK
    assert ctx.trace == ["analyst", "analyst", "template_fallback", "cue_producer"]
    assert len(llm.calls) == 2
    assert ctx.notes[-1].startswith("fallback: templates used after attempt 2 (moment_analyst: RuntimeError")
    assert_valid_output(ctx)


async def test_fallback_when_repair_still_invents_numbers(caplog):
    bad = goal_variants(casual_body="Brennick scores a 0.52 xG beauty!")
    llm = ScriptedLLM({"moment_analyst": GOAL_ANALYST, "persona_editor": bad})
    with caplog.at_level("WARNING", logger="fulcrum.agents"):
        ctx = await wf(llm).run(make_context())
    assert ctx.status == CheckStatus.FALLBACK
    assert llm.agents_called() == ["moment_analyst", "persona_editor", "persona_editor"]
    assert ctx.notes[:2] == [
        "attempt 1: fact-check: variants[casual].body: number '0.52' is not in FACTS",
        "attempt 2: fact-check: variants[casual].body: number '0.52' is not in FACTS",
    ]
    assert "fallback for moment" in caplog.text and "0.52" in caplog.text
    assert_valid_output(ctx)


async def test_fallback_with_default_fake_llm_has_no_canned_output():
    ctx = await wf(FakeLLM()).run(make_context())
    assert ctx.status == CheckStatus.FALLBACK and "KeyError" in ctx.notes[0]
    assert_valid_output(ctx)


async def test_crashing_step_falls_back():
    class Boom:
        name = "persona_editor"

        async def run(self, ctx):
            raise ValueError("bug")

    workflow = wf(ScriptedLLM(GOOD))
    workflow.steps["persona_editor"] = Boom()
    ctx = await workflow.run(make_context())
    assert ctx.status == CheckStatus.FALLBACK
    assert ctx.trace == ["analyst", "persona_editor", "template_fallback", "cue_producer"]
    assert "persona_editor crashed: ValueError: bug" in ctx.notes[0]


# --------------------------------------------------------------------------- timeouts


async def test_analyst_timeout_goes_straight_to_fallback():
    llm = ScriptedLLM(GOOD, delays={"moment_analyst": 1.0})
    ctx = await wf(llm, call_timeout_s=0.05).run(make_context())
    assert ctx.status == CheckStatus.FALLBACK
    assert ctx.trace == ["analyst", "template_fallback", "cue_producer"]
    assert ctx.timed_out and "moment_analyst: timed out after 0.1s" in ctx.notes[0]
    assert ctx.insight.latency_ms < 1000
    assert_valid_output(ctx)


async def test_editor_timeout_goes_straight_to_fallback():
    llm = ScriptedLLM(GOOD, delays={"persona_editor": 1.0})
    ctx = await wf(llm, call_timeout_s=0.05).run(make_context())
    assert ctx.status == CheckStatus.FALLBACK
    assert ctx.trace == ["analyst", "persona_editor", "template_fallback", "cue_producer"]


async def test_total_budget_caps_later_calls():
    llm = ScriptedLLM(GOOD, delays={"moment_analyst": 0.06, "persona_editor": 0.06})
    ctx = await wf(llm, call_timeout_s=1.0, total_budget_s=0.1).run(make_context())
    assert ctx.status == CheckStatus.FALLBACK and ctx.timed_out
    assert "persona_editor: timed out" in ctx.notes[0]


# --------------------------------------------------------------------------- LLM reviewer


async def test_reviewer_runs_for_big_moments_and_feeds_repair():
    reviews = [{"supported": False, "issues": ["'big favourites' is not supported"]}, {"supported": True}]
    llm = ScriptedLLM({**GOOD, "fact_reviewer": reviews})
    ctx = await wf(llm, review_enabled=True).run(make_context(make_moment(significance=0.8)))
    assert ctx.status == CheckStatus.REPAIRED
    assert llm.agents_called() == [
        "moment_analyst",
        "persona_editor",
        "fact_reviewer",
        "moment_analyst",
        "persona_editor",
        "fact_reviewer",
    ]
    assert "reviewer: 'big favourites' is not supported" in llm.calls[3]["user"]


async def test_reviewer_skipped_for_small_moments_and_when_disabled():
    llm = ScriptedLLM({**GOOD, "fact_reviewer": {"supported": False, "issues": ["x"]}})
    ctx = await wf(llm, review_enabled=True).run(make_context(make_moment(significance=0.4)))
    assert ctx.status == CheckStatus.PASSED and "fact_reviewer" not in llm.agents_called()
    ctx = await wf(llm, review_enabled=False).run(make_context(make_moment(significance=0.9)))
    assert ctx.status == CheckStatus.PASSED


async def test_reviewer_error_does_not_block():
    llm = ScriptedLLM(GOOD)  # no canned reviewer → KeyError
    ctx = await wf(llm, review_enabled=True).run(make_context(make_moment(significance=0.9)))
    assert ctx.status == CheckStatus.PASSED and "reviewer skipped" in ctx.notes[0]


def test_review_flag_from_env(monkeypatch):
    monkeypatch.setenv("FULCRUM_LLM_REVIEW", "1")
    assert OrchestratorConfig().review_enabled
    monkeypatch.setenv("FULCRUM_LLM_REVIEW", "0")
    assert not OrchestratorConfig().review_enabled


# --------------------------------------------------------------------------- graph shape


def test_edges_reference_known_steps_and_every_step_is_reachable():
    workflow = wf(FakeLLM())
    names = set(workflow.steps)
    assert {e.source for e in EDGES} | {e.target for e in EDGES} <= names
    reachable, frontier = {START}, [START]
    while frontier:
        src = frontier.pop()
        for e in EDGES:
            if e.source == src and e.target not in reachable:
                reachable.add(e.target)
                frontier.append(e.target)
    assert reachable == names
    assert not [e for e in EDGES if e.source == "cue_producer"], "cue_producer is the terminal step"


# --------------------------------------------------------------------------- ordered worker


async def test_processor_keeps_order_within_a_match():
    first = make_moment(MomentType.GOAL)
    second = make_moment(MomentType.GOAL, clock_ms=first.clock_ms + 60_000)

    async def slow_first(user: str) -> None:
        if "minute: 67'" in user and "EXPLANATION" not in user:
            await asyncio.sleep(0.05)

    llm = ScriptedLLM(GOOD, hooks={"moment_analyst": slow_first})
    done: list[str] = []

    async def on_result(ctx):
        done.append(ctx.moment.moment_id)

    proc = MomentProcessor(wf(llm), on_result)
    store = make_store()
    proc.submit(store.build_context(first))
    proc.submit(store.build_context(second))
    await asyncio.wait_for(proc.drain(), 5)
    await proc.close()
    assert done == [first.moment_id, second.moment_id]


async def test_processor_runs_matches_concurrently():
    other_started = asyncio.Event()

    async def hook(user: str) -> None:
        if "type: elite_pass" in user:
            other_started.set()
        else:
            # match A's analyst can only finish if match B is being processed at the same time
            await asyncio.wait_for(other_started.wait(), 1.0)

    llm = ScriptedLLM(GOOD, hooks={"moment_analyst": hook})
    results = {}

    async def on_result(ctx):
        results[ctx.moment.match_id] = ctx.status

    proc = MomentProcessor(wf(llm, call_timeout_s=2.0), on_result)
    store = make_store()
    a = make_moment(MomentType.GOAL)
    b = make_moment(MomentType.ELITE_PASS, match_id="m-other")  # teams resolved from home_team/away_team facts
    proc.submit(store.build_context(a))
    proc.submit(store.build_context(b))
    await asyncio.wait_for(proc.drain(), 5)
    await proc.close()
    assert results["m-test"] == CheckStatus.PASSED


async def test_processor_survives_a_failing_result_handler(caplog):
    calls = []

    async def on_result(ctx):
        calls.append(ctx.moment.moment_id)
        raise RuntimeError("publish failed")

    proc = MomentProcessor(wf(FakeLLM()), on_result)
    store = make_store()
    m1, m2 = make_moment(), make_moment()
    proc.submit(store.build_context(m1))
    proc.submit(store.build_context(m2))
    await asyncio.wait_for(proc.drain(), 5)
    await proc.close()
    assert calls == [m1.moment_id, m2.moment_id]


@pytest.mark.parametrize("mtype", list(MomentType))
async def test_every_moment_type_completes_with_default_fake(mtype):
    ctx = await wf(FakeLLM()).run(make_context(make_moment(mtype)))
    assert_valid_output(ctx)


# --------------------------------------------------------------------------- Editor fan-out


async def test_editor_fanout_writes_one_variant_per_call():
    llm = ScriptedLLM(GOOD)
    ctx = await wf(llm, editor_fanout=True).run(make_context(make_moment(MomentType.GOAL)))
    assert ctx.status == CheckStatus.PASSED
    assert llm.agents_called() == ["moment_analyst"] + ["persona_editor"] * 4
    editor_users = [c["user"] for c in llm.calls[1:]]
    required = [u.split("VARIANTS REQUIRED (language en)\n")[1] for u in editor_users]
    assert required == [
        "- persona analyst",
        "- persona casual",
        f"- persona club_fan, perspective_team_id {HOME} (Northbridge Athletic)",
        f"- persona club_fan, perspective_team_id {AWAY} (Riverside Rovers)",
    ]
    # each call returned all four canned variants; only the one asked for is kept
    assert [(v.persona, v.perspective_team_id) for v in ctx.variants] == [
        ("analyst", None),
        ("casual", None),
        ("club_fan", HOME),
        ("club_fan", AWAY),
    ]
    assert_valid_output(ctx)


async def test_editor_fanout_calls_run_concurrently():
    llm = ScriptedLLM(GOOD, delays={"persona_editor": 0.1})
    ctx = await wf(llm, editor_fanout=True, call_timeout_s=0.25, total_budget_s=0.25).run(make_context())
    assert ctx.status == CheckStatus.PASSED  # 4 × 0.1 s in sequence would blow the 0.25 s budget


async def test_editor_fanout_relabels_a_mislabelled_variant():
    only_casual = goal_variants()
    only_casual["variants"] = [only_casual["variants"][1]]  # every call answers with the casual variant
    llm = ScriptedLLM({"moment_analyst": GOAL_ANALYST, "persona_editor": only_casual})
    ctx = await wf(llm, editor_fanout=True).run(make_context())
    assert [(v.persona, v.perspective_team_id) for v in ctx.variants] == [
        ("analyst", None),
        ("casual", None),
        ("club_fan", HOME),
        ("club_fan", AWAY),
    ]


async def test_editor_fanout_failure_retries_then_passes():
    llm = ScriptedLLM(GOOD)
    original = llm.structured
    failures = {"left": 1}

    async def flaky(**kw):
        if kw["agent_name"] == "persona_editor" and failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("boom")
        return await original(**kw)

    llm.structured = flaky
    ctx = await wf(llm, editor_fanout=True).run(make_context())
    assert ctx.status == CheckStatus.REPAIRED  # an LLM error is a failed attempt, retried once
    assert ctx.trace == ["analyst", "persona_editor", "persona_editor", "fact_checker", "cue_producer"]
    assert "persona_editor: RuntimeError: boom" in ctx.notes[0]


def test_editor_fanout_default_follows_the_llm(monkeypatch):
    monkeypatch.delenv("FULCRUM_EDITOR_FANOUT", raising=False)
    monkeypatch.setenv("FULCRUM_LLM", "fake")
    assert OrchestratorConfig().editor_fanout is False
    monkeypatch.setenv("FULCRUM_LLM", "foundry")
    assert OrchestratorConfig().editor_fanout is True
    monkeypatch.setenv("FULCRUM_EDITOR_FANOUT", "")  # as copied from .env.example
    assert OrchestratorConfig().editor_fanout is True
    monkeypatch.setenv("FULCRUM_EDITOR_FANOUT", "0")
    assert OrchestratorConfig().editor_fanout is False
