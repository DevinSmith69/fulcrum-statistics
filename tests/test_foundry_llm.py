"""FoundryLLM routing and options — no network (the live path is exercised by scripts/eval_live.py)."""

from fulcrum.agents.orchestrator import EDGES, ExplainMomentWorkflow, build_workflow
from fulcrum.agents.schemas import EditorOutput
from fulcrum.llm import FakeLLM
from fulcrum.llm.foundry import FoundryLLM
from fulcrum.telemetry import configure_telemetry, current_trace_id


def test_from_env_and_routing(monkeypatch):
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://x.services.ai.azure.com/api/projects/p")
    monkeypatch.delenv("FOUNDRY_MODEL_DEPLOYMENT", raising=False)
    monkeypatch.delenv("FOUNDRY_MODEL_DEPLOYMENT_RECAP", raising=False)
    llm = FoundryLLM.from_env()
    assert llm.profile_for("moment_analyst").deployment == "fast"
    assert llm.profile_for("match_reporter").deployment == "strong"
    # gpt-5-mini rejects any temperature but the default, so the recap call must not send one
    assert "temperature" not in llm.options_for("match_reporter", EditorOutput)
    assert llm.options_for("persona_editor", EditorOutput) == {"response_format": EditorOutput, "temperature": 0.4}
    assert llm.options_for("moment_analyst", EditorOutput)["temperature"] == 0.2


def test_from_env_requires_endpoint(monkeypatch):
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    try:
        FoundryLLM.from_env()
    except RuntimeError as e:
        assert "FOUNDRY_PROJECT_ENDPOINT" in str(e)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")


def test_workflow_is_an_agent_framework_graph_of_the_edge_table():
    wf = ExplainMomentWorkflow(FakeLLM())
    built = build_workflow(wf.steps)
    assert type(built).__module__.startswith("agent_framework")
    ids = {e.id for e in built.get_executors_list()}
    assert ids == {e.source for e in EDGES} | {e.target for e in EDGES}


def test_telemetry_is_off_without_configuration(monkeypatch):
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("FULCRUM_OTEL_CONSOLE", raising=False)
    assert configure_telemetry("test") is False
    assert current_trace_id() is None
