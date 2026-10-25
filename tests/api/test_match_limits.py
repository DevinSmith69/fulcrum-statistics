"""Starting matches from the overlay: the running-match cap and the per-client rate limit."""

import time

import pytest
from fastapi.testclient import TestClient

from fulcrum.api.app import create_app
from fulcrum.bus.base import MemoryBus


def _client(monkeypatch, cap: str, rate: str) -> TestClient:
    monkeypatch.setenv("FULCRUM_MAX_LIVE_MATCHES", cap)
    monkeypatch.setenv("FULCRUM_MATCH_RATE", rate)
    return TestClient(create_app(MemoryBus(), in_process=True, run_sim=False, run_agents=False))


def _start(c: TestClient, match_id: str, ip: str = "203.0.113.7"):
    body = {"match_id": match_id, "home_team_id": "HLC", "away_team_id": "MDU", "seed": 11}
    return c.post("/api/matches", json=body, headers={"X-Forwarded-For": ip})


def test_running_cap_refuses_a_new_match_with_429(monkeypatch):
    with _client(monkeypatch, cap="2", rate="0") as c:
        assert _start(c, "a").status_code == 201
        assert _start(c, "b").status_code == 201
        r = _start(c, "c")
        assert r.status_code == 429 and "already running" in r.json()["detail"]
        assert r.headers["Retry-After"] == "60"
        c.app.state.registry.get("a").status = "finished"  # one ends: room for another
        assert _start(c, "c").status_code == 201


def test_quiet_matches_stop_counting(monkeypatch):
    with _client(monkeypatch, cap="1", rate="0") as c:
        assert _start(c, "a").status_code == 201
        c.app.state.registry.get("a").last_activity = time.monotonic() - 600  # paused or abandoned
        assert _start(c, "b").status_code == 201


def test_one_client_can_only_start_a_few_matches(monkeypatch):
    with _client(monkeypatch, cap="0", rate="2/600") as c:
        assert _start(c, "a").status_code == 201
        assert _start(c, "b").status_code == 201
        r = _start(c, "c")
        assert r.status_code == 429 and "try again in" in r.json()["detail"]
        assert _start(c, "c", ip="198.51.100.9").status_code == 201  # another viewer is unaffected


@pytest.mark.parametrize("bad", ["", "lots"])
def test_cap_falls_back_to_the_default_on_bad_config(monkeypatch, bad):
    with _client(monkeypatch, cap=bad, rate="0") as c:
        assert c.app.state.max_running == 3
