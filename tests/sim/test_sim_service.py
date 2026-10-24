"""Sim service (out-of-process `sim` Container App): HTTP trigger, autostart, team tagging on match_start."""

import asyncio

from fastapi.testclient import TestClient

from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import TOPIC_EVENTS, EventType, MatchConfig, MatchEvent
from fulcrum.sim.service import SimService, create_app


def _config(**kw) -> MatchConfig:
    return MatchConfig(match_id="svc-test", home_team_id="HLC", away_team_id="MDU", seed=11, speed=1000, **kw)


async def _first_event(bus: MemoryBus, group: str = "t") -> MatchEvent:
    raw = await asyncio.wait_for(bus._queue(TOPIC_EVENTS, group).get(), 5)
    return MatchEvent.model_validate_json(raw)


async def test_match_start_carries_team_ids():
    bus = MemoryBus()
    bus.register(TOPIC_EVENTS, "t")
    svc = SimService(bus)
    svc.start(_config(emit_tracking=False))
    first = await _first_event(bus)
    assert first.type == EventType.MATCH_START
    assert first.extra["home_team_id"] == "HLC" and first.extra["away_team_id"] == "MDU"
    assert "seed" in first.extra  # the simulator's own extra is kept
    assert svc.summary() == [{"match_id": "svc-test", "status": "running"}]
    await svc.stop()


async def test_rejects_unknown_team_and_duplicate():
    svc = SimService(MemoryBus())
    try:
        svc.start(_config().model_copy(update={"home_team_id": "XXX"}))
    except ValueError as exc:
        assert "unknown team" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    svc.start(_config(emit_tracking=False))
    try:
        svc.start(_config(emit_tracking=False))
    except ValueError as exc:
        assert "already running" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    await svc.stop()


async def test_autostart_uses_env():
    bus = MemoryBus()
    bus.register(TOPIC_EVENTS, "t")
    svc = SimService(bus)
    env = {"FULCRUM_SIM_HOME": "HLC", "FULCRUM_SIM_AWAY": "MDU", "FULCRUM_SIM_SEED": "3", "FULCRUM_SIM_SPEED": "1000"}
    await svc.autostart(env)
    first = await _first_event(bus)
    assert first.match_id == "hlc-mdu-s3"
    assert first.extra["home_team_id"] == "HLC"
    await svc.stop()


def test_http_trigger():
    bus = MemoryBus()
    with TestClient(create_app(bus)) as client:
        assert client.get("/health").json()["service"] == "sim"
        body = _config(emit_tracking=False).model_dump(mode="json")
        assert client.post("/matches", json=body).status_code == 202
        assert client.post("/matches", json=body).status_code == 409
        assert client.post("/matches", json={**body, "match_id": "x", "away_team_id": "NOPE"}).status_code == 422
        assert client.get("/matches").json()[0]["match_id"] == "svc-test"
