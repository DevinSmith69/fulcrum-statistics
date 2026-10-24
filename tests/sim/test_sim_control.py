"""Match controls (demo director): pacing only — speed, pause, skip — never the event stream itself."""

import asyncio

import pytest

from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import (
    TOPIC_EVENTS,
    TOPIC_TRACKING,
    MatchConfig,
    MatchEvent,
    MomentType,
    TrackingFrame,
    WinProbability,
)
from fulcrum.contracts import Moment as MomentModel
from fulcrum.sim import load_teams, run_match, simulate
from fulcrum.sim.control import MatchControl

TEAMS = load_teams()
WP = WinProbability(home=0.4, draw=0.3, away=0.3)


def _moment(kind: MomentType, significance: float, match_id: str = "m") -> MomentModel:
    return MomentModel(
        match_id=match_id,
        type=kind,
        significance=significance,
        period=1,
        clock_ms=60_000,
        seq_start=1,
        seq_end=2,
        wp_before=WP,
        wp_after=WP,
        facts=[],
        trigger="test",
    )


async def _blocks(coro, wait_s: float = 0.15) -> bool:
    """True if `coro` is still waiting after `wait_s` seconds (it is then cancelled)."""
    task = asyncio.ensure_future(coro)
    done, _ = await asyncio.wait({task}, timeout=wait_s)
    if done:
        return False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return True


async def test_same_seed_same_stream_whatever_the_controls_do():
    """Determinism: speed changes, a pause and a skip change only wall-clock pacing, not the items."""
    cfg = MatchConfig(match_id="ctl", home_team_id="HLC", away_team_id="MDU", seed=11, emit_tracking=True)
    expected = list(simulate(cfg, TEAMS["HLC"], TEAMS["MDU"]))

    bus = MemoryBus()
    bus.register(TOPIC_EVENTS, "t")
    bus.register(TOPIC_TRACKING, "t")
    ctl = MatchControl(cfg.match_id, 600, skip_speed=1e7)
    run = asyncio.create_task(run_match(cfg, bus=bus, control=ctl))

    await asyncio.sleep(0.05)
    ctl.set_speed(5)
    await asyncio.sleep(0.05)
    ctl.pause()
    paused_at = ctl.position_ms
    await asyncio.sleep(0.1)
    assert ctl.position_ms == paused_at  # nothing released while paused
    ctl.resume()
    ctl.set_speed(600)
    await asyncio.sleep(0.05)
    ctl.skip_to_next(1.0)  # nothing calls on_moment here, so the skip runs to the final whistle
    await asyncio.wait_for(run, 60)
    assert ctl.finished and not ctl.skipping

    events_q, frames_q = bus._queue(TOPIC_EVENTS, "t"), bus._queue(TOPIC_TRACKING, "t")
    events = [MatchEvent.model_validate_json(events_q.get_nowait()) for _ in range(events_q.qsize())]
    frames = [TrackingFrame.model_validate_json(frames_q.get_nowait()) for _ in range(frames_q.qsize())]
    exp_events = [e for e in expected if isinstance(e, MatchEvent)]
    exp_frames = [f for f in expected if isinstance(f, TrackingFrame)]
    # `ts` is restamped with the wall clock when paced (as without controls); everything else is identical.
    assert [e.model_dump(exclude={"ts"}) for e in events] == [e.model_dump(exclude={"ts"}) for e in exp_events]
    assert [f.model_dump() for f in frames] == [f.model_dump() for f in exp_frames]


async def test_pause_holds_until_resume():
    ctl = MatchControl("m", 600)
    await ctl.pace(0)
    ctl.pause()
    assert await _blocks(ctl.pace(1))
    waiter = asyncio.create_task(ctl.pace(1))
    await asyncio.sleep(0.05)
    assert not waiter.done()
    ctl.resume()
    await asyncio.wait_for(waiter, 1)
    assert ctl.position_ms == 1


async def test_speed_change_applies_to_an_item_already_waiting():
    ctl = MatchControl("m", 1)
    await ctl.pace(0)
    waiter = asyncio.create_task(ctl.pace(60_000))  # a minute away at 1x
    await asyncio.sleep(0.05)
    assert not waiter.done()
    ctl.set_speed(600)  # now 0.1 s away
    await asyncio.wait_for(waiter, 1)


async def test_skip_runs_fast_until_a_significant_moment_then_drops_back():
    ctl = MatchControl("m", 1, skip_speed=1e6)
    await ctl.pace(0)
    ctl.skip_to_next(0.65)
    assert ctl.skipping and ctl.effective_speed == 1e6
    await asyncio.wait_for(ctl.pace(600_000), 1)  # ten match minutes in a blink

    assert not ctl.on_moment(_moment(MomentType.PHASE_CHANGE, 0.35))
    assert not ctl.on_moment(_moment(MomentType.GOAL, 0.9, match_id="other"))
    assert ctl.skipping
    assert ctl.on_moment(_moment(MomentType.BIG_CHANCE, 0.68))
    assert not ctl.skipping and ctl.effective_speed == 1
    assert ctl.last_skip and ctl.last_skip["type"] == "big_chance"
    assert await _blocks(ctl.pace(660_000))  # back to real time: a minute away


async def test_skip_by_type_and_skip_resumes_a_paused_match():
    ctl = MatchControl("m", 10, skip_speed=1e6)
    ctl.pause()
    ctl.skip_to_next(types=["red_card"])
    assert not ctl.paused
    assert not ctl.on_moment(_moment(MomentType.GOAL, 1.0))
    assert ctl.on_moment(_moment(MomentType.RED_CARD, 0.8))


def test_speed_and_threshold_are_validated():
    with pytest.raises(ValueError):
        MatchControl("m", 0)
    ctl = MatchControl("m", 10)
    with pytest.raises(ValueError):
        ctl.set_speed(10_000)
    with pytest.raises(ValueError):
        ctl.skip_to_next(1.5)
    assert ctl.snapshot()["speed"] == 10
