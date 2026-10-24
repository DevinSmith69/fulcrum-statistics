"""Golden moments (AI_PROMPT_DESIGN §7): every moment type through the full flow with canned FakeLLM output."""

import pytest

from fulcrum.agents.orchestrator import ExplainMomentWorkflow, OrchestratorConfig
from fulcrum.contracts import CheckStatus, InsightsEnvelope, MomentType
from tests.agents.factories import AWAY, HOME, make_context, make_moment
from tests.agents.fakes import ScriptedLLM, template_canned

GOLDEN = list(MomentType)  # 13 golden moments, one per type


@pytest.mark.parametrize("mtype", GOLDEN)
async def test_golden_moment_passes_and_publishes_cleanly(mtype):
    ctx = make_context(make_moment(mtype, significance=0.7))
    llm = ScriptedLLM(template_canned(ctx))
    out = await ExplainMomentWorkflow(llm, OrchestratorConfig(review_enabled=False)).run(ctx)

    assert out.status == CheckStatus.PASSED, out.notes
    assert out.trace == ["analyst", "persona_editor", "fact_checker", "cue_producer"]
    insight = out.insight
    assert insight.moment_id == ctx.moment.moment_id and insight.clock_ms == ctx.moment.clock_ms
    assert {(v.persona, v.perspective_team_id) for v in insight.variants} == {
        ("analyst", None),
        ("casual", None),
        ("club_fan", HOME),
        ("club_fan", AWAY),
    }
    assert set(insight.explanation.key_fact_keys) <= {f.key for f in ctx.moment.facts}
    # everything the service will publish round-trips through the topic envelope
    for item in [insight, *out.cues]:
        env = InsightsEnvelope.wrap(item)
        assert InsightsEnvelope.model_validate_json(env.model_dump_json()).data == item
    assert {c.persona for c in out.cues} >= {"analyst", "casual", "club_fan"}
