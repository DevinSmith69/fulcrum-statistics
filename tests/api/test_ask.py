"""POST /api/matches/{id}/ask (Ask the studio) against the real AgentService on a scripted LLM, offline."""

import pytest
from fastapi.testclient import TestClient

from fulcrum.agents.context import load_teams
from fulcrum.agents.service import AgentService
from fulcrum.api.app import create_app
from fulcrum.api.studio import RateLimiter
from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import MatchConfig
from tests.agents.fakes import ScriptedLLM

MID = "ask-nba-rvr"
CONFIG = {"match_id": MID, "home_team_id": "NBA", "away_team_id": "RVR", "seed": 7}
ANSWER = {"answer": "It is still 0-0, so neither side has pulled ahead yet.", "answerable": True}


@pytest.fixture
def client():
    app = create_app(MemoryBus(), in_process=True, run_sim=False, run_agents=False)
    with TestClient(app) as c:
        assert c.post("/api/matches", json=CONFIG).status_code == 201
        yield c


def with_agents(c: TestClient, canned=None) -> ScriptedLLM:
    llm = ScriptedLLM({"studio_analyst": canned or ANSWER})
    svc = AgentService(c.app.state.bus, llm, league=load_teams(), consume_events=False)
    svc.register_match(MatchConfig(**CONFIG))
    c.app.state.translator.agents = svc
    return llm


def test_ask_answers_from_the_match(client):
    llm = with_agents(client)
    r = client.post(f"/api/matches/{MID}/ask", json={"question": "Who's on top?", "language": "en"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == ANSWER["answer"] and body["answerable"] is True
    assert body["check_status"] == "passed" and body["persona"] == "casual" and body["language"] == "en"
    assert body["cited_moment_ids"] == [] and body["fact_keys"] == [] and isinstance(body["latency_ms"], int)
    assert llm.agents_called() == ["studio_analyst"]


def test_ask_refuses_betting_without_the_llm(client):
    llm = with_agents(client)
    r = client.post(f"/api/matches/{MID}/ask", json={"question": "¿Qué cuotas hay?", "language": "es"})
    assert r.status_code == 200
    assert r.json()["answerable"] is False and r.json()["check_status"] == "fallback"
    assert llm.calls == []


def test_ask_unknown_match_is_404(client):
    with_agents(client)
    r = client.post("/api/matches/nope/ask", json={"question": "Who's on top?"})
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/problem+json")


def test_ask_without_agents_is_503(client):
    r = client.post(f"/api/matches/{MID}/ask", json={"question": "Who's on top?"})
    assert r.status_code == 503
    assert "agents service is not running" in r.json()["detail"]


def test_ask_validates_the_question(client):
    with_agents(client)
    assert client.post(f"/api/matches/{MID}/ask", json={"question": ""}).status_code == 422
    assert client.post(f"/api/matches/{MID}/ask", json={"question": "x" * 301}).status_code == 422


def test_ask_is_rate_limited_per_client(client):
    with_agents(client)
    client.app.state.ask_limiter = RateLimiter(2, 60)
    for _ in range(2):
        assert client.post(f"/api/matches/{MID}/ask", json={"question": "Who's on top?"}).status_code == 200
    r = client.post(f"/api/matches/{MID}/ask", json={"question": "Who's on top?"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_rate_limiter_window_and_off_switch():
    now = [0.0]
    rl = RateLimiter(2, 10, clock=lambda: now[0])
    assert rl.allow("a") is None and rl.allow("a") is None
    assert rl.allow("a") == pytest.approx(10) and rl.allow("b") is None
    now[0] = 10.0
    assert rl.allow("a") is None
    assert all(RateLimiter(0, 10).allow("a") is None for _ in range(50))


def test_rate_limiter_from_env(monkeypatch):
    monkeypatch.setenv("FULCRUM_ASK_RATE", "3/15")
    rl = RateLimiter.from_env()
    assert (rl.max_calls, rl.window_s) == (3, 15.0)
    monkeypatch.setenv("FULCRUM_ASK_RATE", "junk")
    assert RateLimiter.from_env().max_calls == 6
