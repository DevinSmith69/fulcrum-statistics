"""GET /api/matches/{id}/insights/{insight_id}/coach (Coach's view), offline on a scripted LLM."""

import pytest
from fastapi.testclient import TestClient

from fulcrum.agents.context import load_teams
from fulcrum.agents.service import AgentService
from fulcrum.agents.templates import fallback_explanation, fallback_variants
from fulcrum.api.app import create_app
from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import CheckStatus, Insight, MatchConfig, MomentType
from tests.agents.factories import make_context, make_moment
from tests.agents.fakes import ScriptedLLM
from tests.agents.test_coach import COACH_FACTS, GOOD

MID = "coach-nba-rvr"
CONFIG = {"match_id": MID, "home_team_id": "NBA", "away_team_id": "RVR", "seed": 7}


def published(mtype=MomentType.GOAL, with_coach=True):
    moment = make_moment(mtype)
    facts = [*moment.facts, *COACH_FACTS] if with_coach else moment.facts
    moment = moment.model_copy(update={"match_id": MID, "facts": facts})
    ctx = make_context(moment)
    insight = Insight(
        match_id=MID,
        moment_id=moment.moment_id,
        clock_ms=moment.clock_ms,
        explanation=fallback_explanation(ctx),
        variants=fallback_variants(ctx),
        check_status=CheckStatus.PASSED,
    )
    return moment, insight


@pytest.fixture
def client():
    app = create_app(MemoryBus(), in_process=True, run_sim=False, run_agents=False)
    with TestClient(app) as c:
        assert c.post("/api/matches", json=CONFIG).status_code == 201
        yield c


def add(c: TestClient, moment, insight, svc: AgentService | None = None) -> None:
    rm = c.app.state.registry.get(MID)
    rm.add_moment(moment)
    rm.add_insight(insight)
    if svc is not None:
        svc._insights[insight.insight_id] = (moment, insight, "1-0")


def with_agents(c: TestClient, canned) -> tuple[AgentService, ScriptedLLM]:
    llm = ScriptedLLM({"coach": canned})
    svc = AgentService(c.app.state.bus, llm, league=load_teams(), consume_events=False)
    svc.register_match(MatchConfig(**CONFIG))
    c.app.state.translator.agents = svc
    return svc, llm


def url(insight_id: str, match_id: str = MID) -> str:
    return f"/api/matches/{match_id}/insights/{insight_id}/coach"


def test_coach_view_from_the_agents(client):
    svc, llm = with_agents(client, GOOD)
    moment, insight = published()
    add(client, moment, insight, svc)
    r = client.get(url(insight.insight_id), params={"language": "en"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["check_status"] == "passed" and body["language"] == "en" and body["defending_team_id"] == "RVR"
    assert [p["text"] for p in body["points"]] == [p["text"] for p in GOOD["points"]]
    assert body["points"][1]["fact_keys"] == ["coach_move_passes", "coach_move_seconds"]
    assert body["takeaway"] == GOOD["takeaway"]
    assert [f["key"] for f in body["facts"]] == [f.key for f in COACH_FACTS]
    assert client.get(url(insight.insight_id)).json() == body and len(llm.calls) == 1  # cached


def test_without_agents_the_facts_are_served(client):
    moment, insight = published()
    add(client, moment, insight)
    r = client.get(url(insight.insight_id))
    assert r.status_code == 200
    body = r.json()
    assert body["check_status"] == "fallback" and body["points"] == [] and body["takeaway"] is None
    assert body["defending_team_id"] == "RVR" and len(body["facts"]) == len(COACH_FACTS)


def test_agent_failure_falls_back_to_the_facts(client):
    svc, llm = with_agents(client, {"points": [], "takeaway": "It took 99 passes."})
    moment, insight = published()
    add(client, moment, insight, svc)
    body = client.get(url(insight.insight_id), params={"language": "de"}).json()
    assert body["check_status"] == "fallback" and body["facts"] and len(llm.calls) == 2


def test_big_chances_are_covered_too(client):
    moment, insight = published(MomentType.BIG_CHANCE)
    add(client, moment, insight)
    assert client.get(url(insight.insight_id)).json()["moment_type"] == "big_chance"


def test_404s(client):
    assert client.get(url("nope", match_id="nope")).status_code == 404
    assert client.get(url("nope")).status_code == 404
    red, red_insight = published(MomentType.RED_CARD)
    add(client, red, red_insight)
    r = client.get(url(red_insight.insight_id))
    assert r.status_code == 404 and "goals and big chances" in r.json()["detail"]
    bare, bare_insight = published(with_coach=False)
    add(client, bare, bare_insight)
    assert client.get(url(bare_insight.insight_id)).status_code == 404
