"""Concession analysis (MODEL_SPEC §4.1): coach_ facts on goal and big_chance moments."""

from pathlib import Path

import pytest

from fulcrum.contracts import (
    EventType,
    MatchEvent,
    Moment,
    Outcome,
    PassAttributes,
    PlayerFrame,
    ShotAttributes,
    TrackingFrame,
)
from fulcrum.engine.concession import (
    attacks_positive_x,
    chance_quality,
    defenders_goal_side,
    keeper_shot_profile,
    zone_label,
)
from fulcrum.engine.match import MatchEngine
from fulcrum.engine.params import CoachParams
from fulcrum.engine.replay import run_file
from fulcrum.engine.teams import load_teams

CP = CoachParams()
SAMPLES = Path(__file__).resolve().parents[2] / "data" / "matches"
SEED11 = SAMPLES / "sample_seed11.jsonl"
SEED7 = SAMPLES / "sample_seed7.jsonl"


def _facts(m: Moment) -> dict:
    return {f.key: f for f in m.facts}


def _coach(m: Moment) -> dict:
    return {f.key: f.value for f in m.facts if f.key.startswith("coach_")}


# --------------------------------------------------------------------------- pure helpers


def test_zone_label_uses_the_thirds_of_the_described_team():
    assert zone_label(10.0, CP) == "own third"
    assert zone_label(34.9, CP) == "own third"
    assert zone_label(35.0, CP) == "middle third"
    assert zone_label(69.9, CP) == "middle third"
    assert zone_label(70.0, CP) == "final third"


@pytest.mark.parametrize(
    ("xg", "band"),
    [(0.01, "low-quality chance"), (0.099, "low-quality chance"), (0.10, "half-chance"), (0.29, "half-chance")]
    + [(0.30, "clear chance"), (0.76, "clear chance")],
)
def test_chance_quality_bands(xg, band):
    assert chance_quality(xg, CP) == band


def test_keeper_label_only_where_defensible():
    assert keeper_shot_profile(True, 0.06, 24.0, CP) == "low-probability finish"
    assert keeper_shot_profile(True, 0.06, 18.0, CP) is None  # not beyond 18 m
    assert keeper_shot_profile(True, 0.10, 25.0, CP) is None  # xG not below 0.10
    assert keeper_shot_profile(False, 0.06, 24.0, CP) is None  # off target: nothing for the keeper


def test_attack_direction_switches_at_half_time():
    assert attacks_positive_x(True, 1) and not attacks_positive_x(True, 2)
    assert not attacks_positive_x(False, 1) and attacks_positive_x(False, 2)


def _frame(ball: tuple[float, float], players: list[tuple[str, str, float, float]], clock_ms=60_000, period=1):
    return TrackingFrame(
        match_id="syn",
        frame_id=clock_ms // 1000,
        period=period,
        clock_ms=clock_ms,
        ball_x=ball[0],
        ball_y=ball[1],
        ball_speed_kmh=0.0,
        players=[PlayerFrame(player_id=p, team_id=t, x=x, y=y, speed_kmh=0.0) for p, t, x, y in players],
    )


def test_defenders_goal_side_from_a_hand_made_frame():
    # Shooter (NBA, attacking +x) at (88, 30); RVR defenders at 95 and 92 are goal-side, 88 (level) and 80 are not;
    # the keeper at 104 is never counted.
    frame = _frame(
        (88.0, 30.0),
        [
            ("RVR-01", "RVR", 104.0, 34.0),
            ("RVR-02", "RVR", 95.0, 20.0),
            ("RVR-03", "RVR", 92.0, 40.0),
            ("RVR-04", "RVR", 88.0, 30.0),
            ("RVR-05", "RVR", 80.0, 30.0),
            ("NBA-09", "NBA", 97.0, 34.0),
        ],
    )
    kw = dict(defending_team_id="RVR", goalkeeper_ids={"RVR-01"}, shot_x=88.0, shot_y=30.0, max_ball_gap_m=3.0)
    assert defenders_goal_side(frame, shooter_attacks_positive_x=True, **kw) == 2


def test_defenders_goal_side_flips_into_the_shooting_team_frame():
    # Same picture with the shooter attacking towards x = 0 in absolute coordinates.
    frame = _frame(
        (105 - 88.0, 68 - 30.0),
        [("RVR-01", "RVR", 1.0, 34.0), ("RVR-02", "RVR", 10.0, 48.0), ("RVR-05", "RVR", 25.0, 38.0)],
    )
    kw = dict(defending_team_id="RVR", goalkeeper_ids={"RVR-01"}, shot_x=88.0, shot_y=30.0, max_ball_gap_m=3.0)
    assert defenders_goal_side(frame, shooter_attacks_positive_x=False, **kw) == 1
    # Wrong orientation (or a frame from another instant): the ball is not at the shot, so no fact at all.
    assert defenders_goal_side(frame, shooter_attacks_positive_x=True, **kw) is None


# --------------------------------------------------------------------------- synthetic event chains


class Chain:
    """Writes a short event chain (NBA home v RVR away, period 1) through a real MatchEngine."""

    def __init__(self) -> None:
        self.engine = MatchEngine("syn", teams_by_id=load_teams())
        self.seq = 0
        self.moments: list[Moment] = []
        self.ev(EventType.MATCH_START, 0, extra={"home_team_id": "NBA", "away_team_id": "RVR"})
        self.ev(EventType.KICK_OFF, 0, "NBA", "NBA-10", x=52.5, y=34.0)

    def ev(self, type_, clock_ms, team=None, player=None, **kw) -> list[Moment]:
        e = MatchEvent(
            match_id="syn", seq=self.seq, period=1, clock_ms=clock_ms, type=type_, team_id=team, player_id=player, **kw
        )
        self.seq += 1
        got = self.engine.process_event(e).moments
        self.moments += got
        return got

    def frame(self, frame: TrackingFrame) -> None:
        self.engine.process_frame(frame)

    def pass_(self, clock_ms, team, player, ok=True, difficulty=1.0, ec=0.9, x=40.0, y=34.0):
        pa = PassAttributes(
            length_m=15, progressive_m=5, ball_speed_kmh=50, expected_completion=ec, difficulty=difficulty
        )
        out = Outcome.SUCCESS if ok else Outcome.FAIL
        self.ev(EventType.PASS, clock_ms, team, player, x=x, y=y, end_x=x + 10, end_y=y, outcome=out, **{"pass": pa})

    def shot(
        self,
        clock_ms,
        team,
        player,
        xg=0.35,
        goal=True,
        near=4.2,
        x=94.0,
        y=34.0,
        on_target=True,
        situation="open_play",
    ):
        sa = ShotAttributes(
            distance_m=11.0,
            angle_rad=0.6,
            body_part="right_foot",
            xg=xg,
            shot_speed_kmh=101.4,
            on_target=on_target,
            situation=situation,
        )
        out = Outcome.GOAL if goal else Outcome.SAVED if on_target else Outcome.OFF_TARGET
        got = self.ev(EventType.SHOT, clock_ms, team, player, x=x, y=y, outcome=out, shot=sa, nearest_opponent_m=near)
        if goal:
            got += self.ev(EventType.GOAL, clock_ms + 1000, team, player, x=x, y=y, outcome=Outcome.GOAL)
        return got


def test_misplaced_pass_origin_with_its_difficulty_and_expected_completion():
    c = Chain()
    c.pass_(5_000, "NBA", "NBA-04", difficulty=0.8)
    c.pass_(8_000, "NBA", "NBA-06", ok=False, difficulty=2.2, ec=0.78, x=60.0)  # misplaced in RVR's half
    c.ev(EventType.INTERCEPTION, 9_000, "RVR", "RVR-06", x=40.0, y=30.0, outcome=Outcome.SUCCESS)
    c.pass_(12_000, "RVR", "RVR-06", x=40.0)
    c.pass_(15_000, "RVR", "RVR-08", x=55.0)
    c.pass_(18_000, "RVR", "RVR-10", ok=False, x=70.0)  # an incomplete pass doesn't count, nor end the move...
    c.pass_(19_000, "RVR", "RVR-10", x=72.0)  # ...if RVR keep the ball
    goal = next(m for m in c.shot(23_400, "RVR", "RVR-09") if m.type == "goal")
    f = _coach(goal)
    nba = {p.player_id: p.short_name for p in load_teams()["NBA"].players}
    rvr = {p.player_id: p.short_name for p in load_teams()["RVR"].players}
    assert f["coach_defending_team"] == "Northbridge Athletic"
    assert f["coach_move_origin"] == "misplaced pass"
    assert f["coach_ball_lost_by"] == nba["NBA-06"] and f["coach_ball_won_by"] == rvr["RVR-06"]
    assert f["coach_misplaced_pass_difficulty"] == 2.2
    assert f["coach_misplaced_pass_expected_completion_pct"] == 78
    assert f["coach_move_start_minute"] == "1'"
    assert f["coach_move_start_zone"] == "middle third"  # won at x=40 in RVR's frame = 65 m from NBA's goal
    assert f["coach_move_passes"] == 3
    assert f["coach_move_seconds"] == 14  # 9.0 s → 23.4 s
    assert f["coach_nearest_defender_m"] == 4.2
    assert f["coach_chance_xg"] == 0.35 and f["coach_chance_quality"] == "clear chance"
    assert f["coach_shot_on_target"] == "yes" and f["coach_shot_speed_kmh"] == 101
    assert f["coach_goalkeeper"] == "Penhallow"  # NBA-01, the defending keeper
    assert "coach_keeper_shot_profile" not in f  # a 0.35 xG chance from 11 m is not a low-probability finish
    assert "coach_defenders_goal_side" not in f  # no tracking frames in this chain: never faked


def test_penalty_goal_names_the_situation_and_both_scores():
    c = Chain()
    c.pass_(5_000, "NBA", "NBA-04")
    c.ev(EventType.FOUL, 18_000, "RVR", "RVR-04", x=94.0, y=34.0)  # the sim emits no restart event for a penalty
    goal = next(m for m in c.shot(22_000, "NBA", "NBA-09", xg=0.76, situation="penalty") if m.type == "goal")
    f = _facts(goal)
    assert f["situation"].value == "penalty"  # 0.76 xG from 11 m reads as a penalty, not a wonder chance
    assert (f["score_before"].value, f["score_after"].value) == ("0-0", "1-0")


def test_corner_header_has_no_zero_pass_count():
    c = Chain()
    c.pass_(5_000, "NBA", "NBA-04")
    c.ev(EventType.CORNER, 20_000, "NBA", "NBA-07", x=105.0, y=0.0)
    goal = next(m for m in c.shot(23_000, "NBA", "NBA-09", situation="corner") if m.type == "goal")
    f = _facts(goal)
    assert f["situation"].value == "header from a corner" and f["coach_move_origin"].value == "corner"
    assert "coach_move_passes" not in f  # never "needed 0 passes"
    assert f["coach_move_seconds"].value == 3


def test_open_play_situation_and_away_goal_score_before():
    c = Chain()
    c.pass_(5_000, "RVR", "RVR-06")
    goal = next(m for m in c.shot(9_000, "RVR", "RVR-09") if m.type == "goal")
    f = _facts(goal)
    assert f["situation"].value == "open play"
    assert (f["score_before"].value, f["score_after"].value) == ("0-0", "0-1")


def test_free_kick_origin_names_the_fouler():
    c = Chain()
    c.pass_(5_000, "NBA", "NBA-04")
    c.ev(EventType.FOUL, 7_000, "RVR", "RVR-04", x=40.0, y=30.0)
    c.ev(EventType.FREE_KICK, 30_000, "NBA", "NBA-06", x=65.0, y=30.0)
    c.pass_(30_000, "NBA", "NBA-06", x=65.0)
    goal = next(m for m in c.shot(36_000, "NBA", "NBA-09") if m.type == "goal")
    f = _coach(goal)
    assert f["coach_defending_team"] == "Riverside Rovers"
    assert f["coach_move_origin"] == "free kick" and f["coach_free_kick_foul_by"]
    assert f["coach_move_seconds"] == 6 and f["coach_move_passes"] == 1  # from the free kick, not the possession
    assert f["coach_move_start_zone"] == "middle third"  # x=65 for NBA = 40 m from RVR's goal
    assert "coach_ball_lost_by" not in f and "coach_misplaced_pass_difficulty" not in f


def test_tackle_origin_names_the_dribbler():
    c = Chain()
    c.ev(EventType.DRIBBLE, 5_000, "NBA", "NBA-07", x=50.0, y=30.0, outcome=Outcome.FAIL)
    c.ev(EventType.TACKLE, 5_500, "RVR", "RVR-04", x=55.0, y=38.0, outcome=Outcome.SUCCESS)
    c.pass_(8_000, "RVR", "RVR-04", x=55.0)
    goal = next(m for m in c.shot(14_000, "RVR", "RVR-09") if m.type == "goal")
    f = _coach(goal)
    assert f["coach_move_origin"] == "tackle" and f["coach_ball_lost_by"] and f["coach_ball_won_by"]


def test_no_pressure_fact_without_a_measurement_and_goal_side_from_tracking():
    c = Chain()
    c.pass_(5_000, "NBA", "NBA-04", x=80.0)
    # NBA attack +x in period 1: the frame is absolute == NBA's frame. Two RVR outfielders goal-side.
    c.frame(
        _frame(
            (94.0, 34.0),
            [("RVR-01", "RVR", 104.0, 34.0), ("RVR-02", "RVR", 99.0, 30.0), ("RVR-03", "RVR", 97.0, 40.0)]
            + [("RVR-04", "RVR", 90.0, 40.0), ("NBA-09", "NBA", 94.0, 34.0)],
            clock_ms=7_000,
        )
    )
    goal = next(m for m in c.shot(7_400, "NBA", "NBA-09", near=None) if m.type == "goal")
    f = _coach(goal)
    assert "coach_nearest_defender_m" not in f
    assert f["coach_defenders_goal_side"] == 2


def test_stale_frame_is_not_used():
    c = Chain()
    c.frame(_frame((94.0, 34.0), [("RVR-02", "RVR", 99.0, 30.0)], clock_ms=3_000))
    c.pass_(5_000, "NBA", "NBA-04", x=80.0)
    goal = next(m for m in c.shot(7_400, "NBA", "NBA-09") if m.type == "goal")
    assert "coach_defenders_goal_side" not in _coach(goal)  # 4.4 s old


def test_big_chance_gets_coach_facts_from_the_defending_side():
    c = Chain()
    c.pass_(5_000, "NBA", "NBA-04", x=80.0)
    big = [m for m in c.shot(9_000, "NBA", "NBA-09", xg=0.45, goal=False) if m.type == "big_chance"]
    assert len(big) == 1
    f = _coach(big[0])
    assert f["coach_defending_team"] == "Riverside Rovers" and f["coach_chance_quality"] == "clear chance"
    assert f["coach_shot_on_target"] == "yes" and f["coach_goalkeeper"] == "Venn"


def test_off_target_chance_has_no_keeper_facts():
    c = Chain()
    c.pass_(5_000, "NBA", "NBA-04", x=80.0)
    big = [m for m in c.shot(9_000, "NBA", "NBA-09", xg=0.45, goal=False, on_target=False) if m.type == "big_chance"]
    f = _coach(big[0])
    assert not {"coach_goalkeeper", "coach_shot_on_target", "coach_shot_speed_kmh"} & f.keys()


def test_low_probability_finish_label_on_a_long_range_goal():
    c = Chain()
    c.pass_(5_000, "NBA", "NBA-04", x=70.0)
    sa = ShotAttributes(
        distance_m=26.4, angle_rad=0.2, body_part="right_foot", xg=0.05, shot_speed_kmh=112, on_target=True
    )
    c.ev(EventType.SHOT, 8_000, "NBA", "NBA-08", x=79.0, y=30.0, outcome=Outcome.GOAL, shot=sa)
    got = c.ev(EventType.GOAL, 9_000, "NBA", "NBA-08", x=79.0, y=30.0, outcome=Outcome.GOAL)
    f = _coach(next(m for m in got if m.type == "goal"))
    assert f["coach_keeper_shot_profile"] == "low-probability finish"
    assert f["coach_chance_quality"] == "low-quality chance"


# --------------------------------------------------------------------------- replayed fixtures


@pytest.fixture(scope="module")
def seed11():
    if not SEED11.exists():
        pytest.skip("sample fixtures not present")
    return run_file(SEED11)


def test_every_goal_and_big_chance_has_coach_facts(seed11):
    shots = [m for m in seed11.moments if m.type in ("goal", "big_chance")]
    assert [m.type for m in shots].count("goal") == 4
    for m in shots:
        f, base = _coach(m), _facts(m)
        names = {base["home_team"].value, base["away_team"].value}
        assert f["coach_defending_team"] in names and f["coach_defending_team"] != base["team"].value
        assert {"coach_chance_xg", "coach_shot_distance_m", "coach_chance_quality"} <= f.keys()
        assert f["coach_chance_xg"] == base["xg"].value and f["coach_shot_distance_m"] == base["distance_m"].value
        assert f.get("coach_move_passes", 1) > 0 and 0 <= f["coach_move_seconds"] <= 600  # no "0 passes"
        if "coach_nearest_defender_m" in f:
            assert 0 < f["coach_nearest_defender_m"] < 40
        if "coach_defenders_goal_side" in f:
            assert 0 <= f["coach_defenders_goal_side"] <= 10
        for v in f.values():  # readable values only
            assert not (isinstance(v, str) and "_" in v), v


def test_seed11_goal_values(seed11):
    """Pinned values on the replayed fixture (the event chains were checked by hand)."""
    goals = {_facts(m)["minute"].value: _coach(m) for m in seed11.moments if m.type == "goal"}
    first = goals["45+2'"]  # Rookwood: free kick after a foul, 2 passes, 21 s
    assert first["coach_move_origin"] == "free kick" and first["coach_move_passes"] == 2
    assert first["coach_move_seconds"] == 21 and first["coach_nearest_defender_m"] == 8.2
    assert first["coach_defenders_goal_side"] == 3 and first["coach_chance_quality"] == "half-chance"
    third = goals["66'"]  # Okonkwo-Reid: from a 31%-completion pass misplaced in midfield
    assert third["coach_move_origin"] == "misplaced pass"
    assert third["coach_misplaced_pass_expected_completion_pct"] == 31
    assert third["coach_misplaced_pass_difficulty"] == 6.9
    assert third["coach_move_start_zone"] == "middle third"
    late = goals["90+1'"]  # a header from a corner: no nearest-opponent measurement on the event, so no fact
    assert late["coach_move_origin"] == "corner" and "coach_nearest_defender_m" not in late


def test_coach_facts_are_deterministic():
    if not SEED7.exists():
        pytest.skip("sample fixtures not present")
    a, b = run_file(SEED7), run_file(SEED7)
    strip = lambda e: [[(f.key, f.value) for f in m.facts if f.key.startswith("coach_")] for m in e.moments]  # noqa: E731
    assert strip(a) == strip(b) and any(strip(a))
