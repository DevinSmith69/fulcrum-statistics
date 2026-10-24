"""FoundryLLM routing and options — no network (the live path is exercised by scripts/eval_live.py)."""

from types import SimpleNamespace

from fulcrum.agents.orchestrator import EDGES, ExplainMomentWorkflow, build_workflow
from fulcrum.agents.schemas import EditorOutput, ReviewOutput
from fulcrum.llm import FakeLLM
from fulcrum.llm.foundry import FoundryLLM, ModelProfile, definition_fingerprint, foundry_agent_name
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


# --------------------------------------------------------------------------- prompt agents


class _Version:
    def __init__(self, version, metadata):
        self.version, self.metadata = version, metadata


class _FakeAgents:
    """`AIProjectClient.agents` stand-in: remembers the latest version per agent name."""

    def __init__(self, fail=()):
        self.latest: dict[str, _Version] = {}
        self.created: list[tuple[str, dict]] = []
        self.fail = set(fail)

    async def get(self, name):
        from azure.core.exceptions import ResourceNotFoundError

        if name not in self.latest:
            raise ResourceNotFoundError("nope")
        return SimpleNamespace(versions=SimpleNamespace(latest=self.latest[name]))

    async def create_version(self, name, *, definition, description, metadata):
        if name in self.fail:
            raise RuntimeError("forbidden")
        assert definition["model"] == "fast" and definition["instructions"]
        version = str(int(self.latest[name].version) + 1) if name in self.latest else "1"
        self.latest[name] = _Version(version, metadata)
        self.created.append((name, metadata))
        return self.latest[name]


class _FakeFoundryAgent:
    def __init__(self, *, project_client, agent_name, agent_version):
        self.agent_name, self.agent_version = agent_name, agent_version
        self.inputs: list[str] = []

    async def run(self, user):
        self.inputs.append(user)
        return SimpleNamespace(
            text='{"supported": true, "issues": []}',
            usage_details={"input_token_count": 10, "output_token_count": 5},
        )


def _llm_with_fake_project(monkeypatch, agents):
    import agent_framework.foundry as af_foundry

    monkeypatch.setattr(af_foundry, "FoundryAgent", _FakeFoundryAgent)
    llm = FoundryLLM(
        endpoint="https://x.services.ai.azure.com/api/projects/p",
        fast=ModelProfile("fast"),
        recap=ModelProfile("strong", False),
        credential=_NoCredential(),
        prompt_agents=True,
    )
    llm._project = SimpleNamespace(agents=agents)
    return llm


class _NoCredential:
    async def get_token(self, *scopes, **kw):  # pragma: no cover - never called offline
        raise AssertionError("no network in tests")


ROLE = ("fact_reviewer", "You review football copy.", "reviewer/1.0.0", ReviewOutput)


def test_agent_names_and_fingerprint():
    assert foundry_agent_name("moment_analyst") == "fulcrum-moment-analyst"
    a = definition_fingerprint("fast", "x", {"response_format": ReviewOutput, "temperature": 0.2})
    assert a == definition_fingerprint("fast", "x", {"response_format": ReviewOutput, "temperature": 0.2})
    assert a != definition_fingerprint("fast", "y", {"response_format": ReviewOutput, "temperature": 0.2})
    assert a != definition_fingerprint("strong", "x", {"response_format": ReviewOutput, "temperature": 0.2})
    assert a != definition_fingerprint("fast", "x", {"response_format": EditorOutput, "temperature": 0.2})


def test_prompt_agents_are_on_by_default(monkeypatch):
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://x.services.ai.azure.com/api/projects/p")
    monkeypatch.delenv("FOUNDRY_PROMPT_AGENTS", raising=False)
    assert FoundryLLM.from_env().prompt_agents is True
    monkeypatch.setenv("FOUNDRY_PROMPT_AGENTS", "0")
    assert FoundryLLM.from_env().prompt_agents is False


async def test_publish_creates_once_and_reuses_unchanged_versions(monkeypatch):
    agents = _FakeAgents()
    llm = _llm_with_fake_project(monkeypatch, agents)
    assert await llm.publish_agents([ROLE]) == {"fulcrum-fact-reviewer": "1"}
    assert agents.created[0][1]["prompt_version"] == "reviewer/1.0.0"
    # same definition (e.g. a restart): no new version
    assert await llm.publish_agents([ROLE]) == {"fulcrum-fact-reviewer": "1"}
    assert len(agents.created) == 1
    # changed prompt: a new version
    changed = (ROLE[0], ROLE[1] + " Be strict.", "reviewer/1.1.0", ROLE[3])
    assert await llm.publish_agents([changed]) == {"fulcrum-fact-reviewer": "2"}
    assert llm.published["fact_reviewer"].instructions == changed[1]


async def test_publish_failure_leaves_the_role_local(monkeypatch):
    llm = _llm_with_fake_project(monkeypatch, _FakeAgents(fail={"fulcrum-fact-reviewer"}))
    assert await llm.publish_agents([ROLE]) == {}
    assert llm.published == {}


async def test_published_role_runs_on_its_prompt_agent(monkeypatch):
    llm = _llm_with_fake_project(monkeypatch, _FakeAgents())
    await llm.publish_agents([ROLE])
    out = await llm.structured(system=ROLE[1], user="COPY", schema=ReviewOutput, agent_name="fact_reviewer")
    assert out == ReviewOutput(supported=True, issues=[])
    assert llm.published["fact_reviewer"].agent.inputs == ["COPY"]
    assert llm.calls[-1].input_tokens == 10 and llm.calls[-1].ok and llm.calls[-1].via == "prompt_agent"


def test_service_publishes_the_fixed_prompt_roles():
    from fulcrum.agents.service import PROMPT_AGENT_ROLES

    names = [r[0] for r in PROMPT_AGENT_ROLES]
    assert names == ["moment_analyst", "persona_editor", "fact_reviewer", "studio_analyst"]
    for _, system, _, _ in PROMPT_AGENT_ROLES:  # no per-call placeholders in a published prompt
        assert not any(f"{{{p}}}" in system for p in ("language", "persona", "player"))
