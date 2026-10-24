"""Hybrid topology, offline: the sim is a separate service, the API hosts engine + agents.

One MemoryBus stands in for Event Hubs. The sim "Container App" is the real `fulcrum.sim.service` app reached
through an httpx ASGI transport, publishing onto that shared bus; the API must not run a simulator itself.
"""

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from fulcrum.api.app import create_app
from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import TOPIC_EVENTS, TOPIC_MOMENTS, TOPIC_TRACKING
from fulcrum.sim import service as sim_service

MID = "hybrid-nba-rvr"
CONFIG = {"match_id": MID, "home_team_id": "NBA", "away_team_id": "RVR", "seed": 7, "speed": 100000}


def wait_for(predicate, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


@pytest.fixture
def hybrid(monkeypatch):
    monkeypatch.setenv("FULCRUM_LLM", "fake")
    monkeypatch.setenv("FULCRUM_SIM_URL", "http://ca-fulcrum-sim")
    monkeypatch.setenv("FULCRUM_SIM_AUTOSTART", "0")
    bus = MemoryBus()
    sim_app = sim_service.create_app(bus)
    app = create_app(bus, topology="hybrid", run_sim=True)  # run_sim is ignored: hybrid never simulates here
    with TestClient(app) as c:
        sim_app.state.sim = sim_service.SimService(bus)  # the ASGI transport does not run the sim's lifespan
        app.state.sim_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=sim_app))
        c.sim = sim_app.state.sim
        yield c
        c.portal.call(c.sim.stop)


def test_hybrid_wiring_before_any_match(hybrid):
    app = hybrid.app
    pipeline = app.state.pipeline
    assert pipeline is not None and pipeline.run_sim is False
    queues = app.state.bus._queues
    # the engine joined its consumer groups at start-up (the sim may start matches nobody POSTed)
    assert "engine" in queues[TOPIC_EVENTS] and "engine" in queues[TOPIC_TRACKING]
    wait_for(lambda: pipeline.agents_state == "running")
    assert "agents" in queues[TOPIC_MOMENTS] and "agents" in queues[TOPIC_EVENTS]
    assert app.state.translator.agents is pipeline.agents  # on-demand hooks are wired
    h = hybrid.get("/api/health").json()
    assert (h["topology"], h["in_process"], h["sim"], h["agents"]) == ("hybrid", False, "service", True)


def test_hybrid_match_runs_on_the_sim_service_and_explains_in_the_api(hybrid):
    r = hybrid.post("/api/matches", json=CONFIG)
    assert r.status_code == 201, r.text
    assert [m["match_id"] for m in hybrid.sim.summary()] == [MID]  # forwarded to the sim service
    assert not any(t.get_name().startswith("sim-") for t in hybrid.app.state.pipeline.tasks)  # not simulated here

    wait_for(lambda: hybrid.get("/api/matches").json()[0]["status"] == "finished")
    moments = hybrid.get(f"/api/matches/{MID}/moments").json()
    assert len(moments) >= 5
    wait_for(lambda: len(hybrid.get(f"/api/matches/{MID}/insights").json()) >= len(moments) // 2)
    insight = hybrid.get(f"/api/matches/{MID}/insights").json()[0]

    # on-demand agent features, which the split topology cannot serve. FakeLLM has no translations, so the
    # AgentService answers with its English fallback; what matters here is that the API reached it.
    agents, calls = hybrid.app.state.pipeline.agents, []
    real_translate = agents.translate

    async def spy(insight_id, language):
        calls.append((insight_id, language))
        return await real_translate(insight_id, language)

    agents.translate = spy
    tr = hybrid.post(f"/api/matches/{MID}/translate", json={"insight_id": insight["insight_id"], "language": "es"})
    assert tr.status_code == 200 and tr.json()
    assert calls == [(insight["insight_id"], "es")]
    ask = hybrid.post(f"/api/matches/{MID}/ask", json={"question": "Who is on top?"})
    assert ask.status_code == 200, ask.text
    assert ask.json()["answer"]


def test_hybrid_sim_failure_is_502_and_releases_the_match_id(hybrid):
    hybrid.app.state.sim_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(500, text="boom"))
    )
    assert hybrid.post("/api/matches", json=CONFIG).status_code == 502
    assert hybrid.get("/api/matches").json() == []


def test_split_topology_has_no_pipeline(monkeypatch):
    monkeypatch.delenv("FULCRUM_SIM_URL", raising=False)
    with TestClient(create_app(MemoryBus(), topology="split")) as c:
        h = c.get("/api/health").json()
        assert (h["topology"], h["in_process"], h["sim"], h["agents"]) == ("split", False, None, None)
        assert c.app.state.pipeline is None


def test_topology_from_env_and_legacy_flag(monkeypatch):
    monkeypatch.setenv("FULCRUM_TOPOLOGY", "hybrid")
    monkeypatch.setenv("FULCRUM_RUN_AGENTS", "0")
    with TestClient(create_app(MemoryBus())) as c:
        assert c.get("/api/health").json()["topology"] == "hybrid"
    monkeypatch.delenv("FULCRUM_TOPOLOGY")
    monkeypatch.setenv("FULCRUM_INPROCESS", "1")
    with TestClient(create_app(MemoryBus(), run_sim=False)) as c:
        h = c.get("/api/health").json()
        assert (h["topology"], h["in_process"]) == ("inprocess", True)


def test_demo_director_controls_a_match_on_the_sim_service(hybrid):
    """Speed / pause / next moment are forwarded to the sim service; moments from the API's engine end a skip."""
    assert hybrid.get("/api/health").json()["match_controls"] is True
    cfg = {**CONFIG, "match_id": "hybrid-ctl", "speed": 1}  # real-time pacing, so the match is still running
    assert hybrid.post("/api/matches", json=cfg).status_code == 201
    base = "/api/matches/hybrid-ctl/control"
    snap = hybrid.get(base).json()
    assert snap["available"] and snap["speed"] == 1.0 and not snap["paused"]
    assert hybrid.post(base, json={"action": "set_speed", "speed": 30}).json()["speed"] == 30.0
    assert hybrid.post(base, json={"action": "pause"}).json()["paused"] is True
    assert hybrid.post(base, json={"action": "set_speed"}).status_code == 422
    # skip to the first big moment: the sim runs fast until the API's engine sees one and passes it on
    snap = hybrid.post(base, json={"action": "next_moment", "min_significance": 0.65}).json()
    assert snap["skipping"] is True and "hybrid-ctl" in hybrid.app.state.remote_skips
    wait_for(lambda: hybrid.get(base).json()["last_skip"] is not None, timeout=60)
    after = hybrid.get(base).json()
    assert after["skipping"] is False and after["last_skip"]["significance"] >= 0.65
    wait_for(lambda: "hybrid-ctl" not in hybrid.app.state.remote_skips, timeout=10)
    hybrid.post(base, json={"action": "pause"})


def test_controls_for_an_unknown_match_on_the_sim_are_404(hybrid):
    hybrid.app.state.registry.get_or_create("ghost")  # the API knows it, the sim never started it
    assert hybrid.get("/api/matches/ghost/control").status_code == 404
