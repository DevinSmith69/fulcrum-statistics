"""MatchState invariants over the whole hand-made match, plus the engine service on the memory bus."""

import asyncio

import pytest

from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import (
    TOPIC_EVENTS,
    TOPIC_MOMENTS,
    TOPIC_STATE,
    TOPIC_TRACKING,
    MatchEvent,
    MatchState,
    Moment,
)
from fulcrum.engine.match import MatchEngine
from fulcrum.engine.service import EngineService
from fulcrum.engine.state import minute_label
from fulcrum.engine.teams import load_teams


def _check_invariants(s: MatchState, prev: MatchState | None) -> None:
    assert s.win_prob.home + s.win_prob.draw + s.win_prob.away == pytest.approx(1.0, abs=1e-6)
    assert s.home.team_id == "NBA" and s.away.team_id == "RVR"
    for t in (s.home, s.away):
        assert 0 <= t.passes_completed <= t.passes
        assert 0 <= t.shots_on_target <= t.shots
        assert 0 <= t.pass_accuracy <= 100
        assert 0 <= t.pressure_index <= 100 and 0 <= t.control_index <= 100
        assert t.xg >= 0
    assert s.home.possession_pct + s.away.possession_pct == pytest.approx(100, abs=0.2)
    assert (s.home.goals, s.away.goals) == (s.score_home, s.score_away)
    assert -100 <= s.momentum <= 100 and 0 <= s.chaos_index <= 100 and s.tempo_ppm >= 0
    for p in s.players.values():
        assert 0 <= p.passes_completed <= p.passes
        assert 3 <= p.rating <= 10
        assert 0 <= p.minutes <= s.clock_ms / 60000 + 0.05  # minutes are rounded to 0.1
    if prev is not None:  # monotone counters
        assert s.seq >= prev.seq and s.clock_ms >= prev.clock_ms
        assert s.score_home >= prev.score_home and s.score_away >= prev.score_away
        assert s.home.passes >= prev.home.passes and s.away.shots >= prev.away.shots


def test_state_invariants_throughout(fixture_items):
    engine = MatchEngine("fixture-nba-rvr", teams_by_id=load_teams())
    prev = None
    n_states = 0
    for item in fixture_items:
        out = engine.process_event(item) if isinstance(item, MatchEvent) else engine.process_frame(item)
        if out.state is not None:
            _check_invariants(out.state, prev)
            if prev is not None and not out.moments and out.state.period == prev.period:
                assert out.state.clock_ms - prev.clock_ms >= 1000, "MatchState is throttled to 1 Hz sim time"
            prev = out.state
            n_states += 1
    assert n_states > 100


def test_final_state(replayed):
    s = replayed.snapshot()
    assert (s.score_home, s.score_away) == (1, 1)
    assert s.win_prob.draw == pytest.approx(1.0)  # finished
    assert s.away.red_cards == 1 and s.away.yellow_cards == 2
    assert s.home.max_shot_speed_kmh == 118
    loxley, brennick, yardley = s.players["NBA-08"], s.players["NBA-10"], s.players["RVR-03"]
    assert brennick.goals == 1 and brennick.shots >= 2 and brennick.xg >= 0.9
    assert loxley.key_passes >= 1
    assert yardley.minutes < 57  # sent off at 55'
    assert s.players["NBA-11"].top_speed_kmh == pytest.approx(34.6)
    assert s.players["NBA-11"].sprints == 1
    assert len(replayed.winprob_points) == replayed.tracker.last_seq + 1  # one point per applied event


def test_idempotent_on_seq(fixture_items):
    events = [i for i in fixture_items if isinstance(i, MatchEvent)]
    once, twice = (
        MatchEngine("fixture-nba-rvr", teams_by_id=load_teams()),
        MatchEngine("fixture-nba-rvr", teams_by_id=load_teams()),
    )
    for ev in events:
        once.process_event(ev)
    for ev in events:
        twice.process_event(ev)
        twice.process_event(ev)  # duplicate delivery
    twice.process_event(events[10])  # late stale message
    a, b = once.snapshot().model_dump(), twice.snapshot().model_dump()
    assert a == b


def test_minute_labels():
    assert minute_label(1, 0.0) == "1'"
    assert minute_label(1, 66.5) == "45+22'"
    assert minute_label(1, 44.99) == "45'"
    assert minute_label(1, 46.2) == "45+2'"
    assert minute_label(2, 66.5) == "67'"
    assert minute_label(2, 92.1) == "90+3'"


async def test_engine_service_on_memory_bus(fixture_items):
    bus = MemoryBus()
    bus.register(TOPIC_STATE, "test")
    bus.register(TOPIC_MOMENTS, "test")
    service = EngineService(bus, teams=load_teams())
    service.start()
    for item in fixture_items:
        await bus.publish(TOPIC_EVENTS if isinstance(item, MatchEvent) else TOPIC_TRACKING, item.match_id, item)
    # wait for the engine to drain both topics
    for _ in range(200):
        await asyncio.sleep(0.01)
        eng = service.engines.get("fixture-nba-rvr")
        if eng and eng.tracker.finished and eng.tracker.last_frame_id == 59:
            break
    await service.stop()
    moments_q = bus._queues[TOPIC_MOMENTS]["test"]
    states_q = bus._queues[TOPIC_STATE]["test"]
    moments = [Moment.model_validate_json(moments_q.get_nowait()) for _ in range(moments_q.qsize())]
    states = [MatchState.model_validate_json(states_q.get_nowait()) for _ in range(states_q.qsize())]
    # events and frames arrive on separate topics, so frame-driven moments may interleave differently
    # from the file replay — compare published vs detected, not against the replay count.
    assert len(moments) == len(service.engines["fixture-nba-rvr"].moments) >= 15
    assert {m.type for m in moments} >= {"goal", "red_card", "big_chance", "half_time", "full_time"}
    assert states[-1].score_home == 1 and states[-1].score_away == 1
    assert len(states) < len([i for i in fixture_items if isinstance(i, MatchEvent)])  # throttled
