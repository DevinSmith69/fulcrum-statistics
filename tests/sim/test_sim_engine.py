"""Simulator invariants: determinism, contract validity, ordering, bounds, score consistency."""

import time
from collections import Counter

import pytest

from fulcrum.contracts import EventType, MatchConfig, MatchEvent, Outcome, TrackingFrame
from fulcrum.sim import load_teams, simulate
from fulcrum.sim.runner import to_jsonl_line

TEAMS = load_teams()
EXTRA_SEEDS = range(20, 28)


def make_config(seed: int = 7, home: str = "NBA", away: str = "RVR", tracking: bool = True) -> MatchConfig:
    return MatchConfig(
        match_id=f"test-{home}-{away}-{seed}",
        home_team_id=home,
        away_team_id=away,
        seed=seed,
        emit_tracking=tracking,
    )


def run(config: MatchConfig) -> list[MatchEvent | TrackingFrame]:
    return list(simulate(config, TEAMS[config.home_team_id], TEAMS[config.away_team_id]))


@pytest.fixture(scope="module")
def match() -> list[MatchEvent | TrackingFrame]:
    return run(make_config())


@pytest.fixture(scope="module")
def events(match) -> list[MatchEvent]:
    return [m for m in match if isinstance(m, MatchEvent)]


@pytest.fixture(scope="module")
def frames(match) -> list[TrackingFrame]:
    return [m for m in match if isinstance(m, TrackingFrame)]


@pytest.fixture(scope="module")
def many_matches() -> list[list[MatchEvent]]:
    pairs = [("HLC", "MDU"), ("MDU", "NBA"), ("RVR", "HLC"), ("NBA", "HLC")]
    out = []
    for i, seed in enumerate(EXTRA_SEEDS):
        home, away = pairs[i % len(pairs)]
        out.append([m for m in run(make_config(seed, home, away, tracking=False)) if isinstance(m, MatchEvent)])
    return out


# ---------------------------------------------------------------- determinism
def test_same_seed_identical_stream(match):
    again = run(make_config())
    assert len(again) == len(match)
    assert [to_jsonl_line(m) for m in again] == [to_jsonl_line(m) for m in match]


def test_different_seed_differs(events):
    other = [m for m in run(make_config(seed=8, tracking=False)) if isinstance(m, MatchEvent)]
    assert [(e.type, e.clock_ms) for e in other] != [(e.type, e.clock_ms) for e in events]


def test_tracking_flag_does_not_change_events(events):
    no_tracking = run(make_config(tracking=False))
    assert all(isinstance(m, MatchEvent) for m in no_tracking)
    assert [to_jsonl_line(m) for m in no_tracking] == [to_jsonl_line(e) for e in events]


# ---------------------------------------------------------------- contracts
def test_every_model_round_trips_through_its_contract(match):
    for m in match:
        wire = m.model_dump_json(by_alias=True)
        assert type(m).model_validate_json(wire) == m


def test_payloads_present_and_consistent(events):
    for e in events:
        if e.type == EventType.PASS:
            assert e.pass_ is not None and e.end_x is not None
            assert (e.pass_.recipient_id is not None) == (e.outcome == Outcome.SUCCESS)
        if e.type == EventType.SHOT:
            assert e.shot is not None
            assert e.shot.on_target == (e.outcome in (Outcome.GOAL, Outcome.SAVED))
            assert e.shot.big_chance == (e.shot.xg >= 0.30)
        if e.type == EventType.CARD:
            assert e.card is not None
        if e.type == EventType.SUBSTITUTION:
            assert e.substitution is not None and e.player_id == e.substitution.player_on_id


# ---------------------------------------------------------------- ordering
def test_seq_strictly_increasing_from_zero(events):
    assert [e.seq for e in events] == list(range(len(events)))
    assert len({e.event_id for e in events}) == len(events)


def test_clock_non_decreasing_across_events_and_frames(match):
    clocks = [m.clock_ms for m in match]
    assert clocks == sorted(clocks)


def test_frame_ids_contiguous_at_one_hz(frames):
    assert [f.frame_id for f in frames] == list(range(len(frames)))
    gaps = Counter(b.clock_ms - a.clock_ms for a, b in zip(frames, frames[1:], strict=False) if a.period == b.period)
    assert set(gaps) <= {999, 1000, 1001}


def test_possession_id_never_decreases(events):
    ids = [e.possession_id for e in events if e.possession_id is not None]
    assert ids == sorted(ids)
    assert sum(e.type == EventType.POSSESSION_CHANGE for e in events) > 100


def test_match_structure(events):
    types = [e.type for e in events]
    assert types[0] == EventType.MATCH_START and types[-1] == EventType.MATCH_END
    assert types.count(EventType.PERIOD_START) == 2 and types.count(EventType.PERIOD_END) == 2
    p1_end = next(e for e in events if e.type == EventType.PERIOD_END and e.period == 1)
    p2_start = next(e for e in events if e.type == EventType.PERIOD_START and e.period == 2)
    assert p1_end.clock_ms >= 46 * 60_000
    assert p2_start.clock_ms == p1_end.clock_ms  # contract: P2 starts at 45:00 + first-half stoppage
    assert events[-1].clock_ms >= p2_start.clock_ms + 48 * 60_000
    assert all(e.period == 1 for e in events if e.clock_ms < p1_end.clock_ms)
    kickoffs = [e for e in events if e.type == EventType.KICK_OFF]
    assert len(kickoffs) == 2 + sum(e.type == EventType.GOAL for e in events)


def test_squad_rules(events, many_matches):
    for evs in [events, *many_matches]:
        subs = Counter(e.team_id for e in evs if e.type == EventType.SUBSTITUTION)
        assert all(3 <= n <= 5 for n in subs.values())
        assert all(e.clock_ms >= 55 * 60_000 for e in evs if e.type == EventType.SUBSTITUTION)
        subbed_off = {e.substitution.player_off_id: e.seq for e in evs if e.substitution}
        sent_off = {e.player_id: e.seq for e in evs if e.type == EventType.CARD and e.outcome == Outcome.RED}
        for gone in (subbed_off, sent_off):
            for pid, seq in gone.items():  # nobody acts after leaving the pitch
                assert not any(e.player_id == pid for e in evs if e.seq > seq)


# ---------------------------------------------------------------- bounds
def _in_bounds(x: float | None, y: float | None) -> bool:
    return (x is None or 0 <= x <= 105) and (y is None or 0 <= y <= 68)


def test_event_coordinates_in_bounds(events, many_matches):
    for evs in [events, *many_matches]:
        for e in evs:
            assert _in_bounds(e.x, e.y) and _in_bounds(e.end_x, e.end_y), e


def test_tracking_in_bounds_and_plausible(frames):
    for f in frames:
        assert _in_bounds(f.ball_x, f.ball_y)
        assert 19 <= len(f.players) <= 22
        assert len({p.player_id for p in f.players}) == len(f.players)
        for p in f.players:
            assert _in_bounds(p.x, p.y)
            assert 0 <= p.speed_kmh <= 35.1


# ---------------------------------------------------------------- score
def test_score_consistent_with_goal_events(events, many_matches):
    for evs in [events, *many_matches]:
        home_id = next(e.team_id for e in evs if e.type == EventType.KICK_OFF)  # home kicks off the first half
        goals = [0, 0]
        for e in evs:
            if e.type == EventType.GOAL:
                goals[0 if e.team_id == home_id else 1] += 1
            assert (e.score_home, e.score_away) == tuple(goals), e
        goal_shots = [e for e in evs if e.type == EventType.SHOT and e.outcome == Outcome.GOAL]
        goal_events = [e for e in evs if e.type == EventType.GOAL]
        assert len(goal_shots) == len(goal_events)
        for g in goal_events:
            assert g.extra["shot_seq"] == g.seq - 1
            assert evs[g.extra["shot_seq"]].player_id == g.player_id


# ---------------------------------------------------------------- performance
def test_full_match_with_tracking_under_five_seconds():
    t0 = time.perf_counter()
    out = run(make_config(seed=99))
    elapsed = time.perf_counter() - t0
    assert sum(isinstance(m, TrackingFrame) for m in out) > 5000
    assert elapsed < 5.0, f"90-minute match took {elapsed:.2f}s"
