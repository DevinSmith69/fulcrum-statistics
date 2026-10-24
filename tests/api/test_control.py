"""Demo director endpoints (API_CONTRACT §2 "Match controls"): GET/POST /api/matches/{id}/control."""

import time

import pytest
from fastapi.testclient import TestClient

from fulcrum.api.app import create_app
from fulcrum.bus.base import MemoryBus

MID = "demo-hlc-mdu"
CONFIG = {"match_id": MID, "home_team_id": "HLC", "away_team_id": "MDU", "seed": 11, "speed": 1}


def wait_for(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


@pytest.fixture
def live():
    """In-process API with the real simulator and engine (no agents), seed 11 started at 1x."""
    app = create_app(MemoryBus(), in_process=True, run_sim=True, run_agents=False)
    with TestClient(app) as c:
        assert c.post("/api/matches", json=CONFIG).status_code == 201
        yield c


def control(c: TestClient, **body):
    return c.post(f"/api/matches/{MID}/control", json=body)


def test_health_advertises_controls(live):
    assert live.get("/api/health").json()["match_controls"] is True


def test_speed_pause_resume(live):
    state = live.get(f"/api/matches/{MID}/control").json()
    assert state["speed"] == 1 and not state["paused"] and not state["skipping"] and state["available"]

    assert control(live, action="set_speed", speed=30).json()["speed"] == 30
    assert control(live, action="pause").json()["paused"] is True
    resumed = control(live, action="resume").json()
    assert resumed["paused"] is False and resumed["speed"] == 30


def test_next_moment_stops_on_the_first_big_moment(live):
    ctl = live.app.state.pipeline.control(MID)
    ctl.skip_speed = 1e5  # the 120x default would take ~7 s to reach 14'
    started = control(live, action="next_moment").json()
    assert started["skipping"] is True and started["skip_min_significance"] == 0.65

    wait_for(lambda: not live.get(f"/api/matches/{MID}/control").json()["skipping"])
    state = live.get(f"/api/matches/{MID}/control").json()
    # Seed 11: the first moment with significance >= 0.65 is the 0.76 xG big chance at 14'.
    assert state["last_skip"]["type"] == "big_chance"
    assert state["speed"] == 1 and state["effective_speed"] == 1
    moments = live.get(f"/api/matches/{MID}/moments").json()
    assert any(m["moment_id"] == state["last_skip"]["moment_id"] for m in moments)
    control(live, action="pause")


def test_bad_requests(live):
    assert control(live, action="set_speed").status_code == 422
    assert control(live, action="set_speed", speed=0).status_code == 422
    assert control(live, action="next_moment", min_significance=2).status_code == 422
    assert control(live, action="rewind").status_code == 422
    assert live.get("/api/matches/nope/control").status_code == 404


def test_not_available_when_the_sim_runs_elsewhere():
    app = create_app(MemoryBus(), in_process=False)
    with TestClient(app) as c:
        assert c.get("/api/health").json()["match_controls"] is False
        assert c.post("/api/matches", json=CONFIG).status_code == 201
        r = c.get(f"/api/matches/{MID}/control")
        assert r.status_code == 501 and r.headers["content-type"].startswith("application/problem+json")
        assert c.post(f"/api/matches/{MID}/control", json={"action": "pause"}).status_code == 501
