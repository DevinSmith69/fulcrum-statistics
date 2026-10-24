"""Moment rules (MODEL_SPEC §4) on the hand-made fixture, plus targeted synthetic cases."""

import re
from collections import Counter

import pytest

from fulcrum.contracts import EventType, MatchEvent, Moment, Outcome, PassAttributes, ShotAttributes
from fulcrum.engine.match import MatchEngine
from fulcrum.engine.params import DEFAULT_PARAMS
from fulcrum.engine.teams import load_teams

MP = DEFAULT_PARAMS.moments
FULLY_EXEMPT = set(MP.fully_exempt) | {"player_milestone_goal"}
MINUTE_RE = re.compile(r"^\d{1,3}(\+\d{1,2})?'$")


def _facts(m: Moment) -> dict:
    return {f.key: f for f in m.facts}


def test_counts_by_type(replayed):
    counts = Counter(m.type for m in replayed.moments)
    assert counts["goal"] == 2
    assert counts["big_chance"] == 2  # 0.42 saved at 8', 0.55 missed at 85'
    assert counts["red_card"] == 1
    assert counts["half_time"] == 1
    assert counts["full_time"] == 1
    assert counts["elite_pass"] == 1
    assert counts["speed_record"] == 2  # 118 km/h shot + 34.6 km/h sprint
    assert counts["momentum_run"] >= 1
    assert counts["pressure_shift"] >= 1
    assert counts["phase_change"] >= 1
    assert 20 <= len(replayed.moments) <= 35


def test_goal_fact_pack(replayed):
    first, second = [m for m in replayed.moments if m.type == "goal"]
    f = _facts(first)
    for key in [
        "scorer",
        "minute",
        "xg",
        "shot_speed_kmh",
        "distance_m",
        "wp_scorer_before",
        "wp_scorer_after",
        "score_after",
        "assist",
        "pass_difficulty_of_assist",
        "wp_scorer_delta",
    ]:
        assert key in f, key
    assert f["scorer"].value == "Brennick" and f["assist"].value == "Loxley"
    assert f["score_after"].value == "1-0" and f["minute"].value == "16'"
    assert f["xg"].value == 0.35 and f["shot_speed_kmh"].value == 104
    assert f["wp_scorer_after"].value - f["wp_scorer_before"].value == f["wp_scorer_delta"].value > 0
    assert first.team_id == "NBA" and "NBA-10" in first.player_ids and "NBA-08" in first.player_ids
    assert first.seq_start < first.seq_end  # spans shot → goal
    assert first.wp_after.home > first.wp_before.home
    assert 0.6 <= first.significance <= 1.0
    assert _facts(second)["score_after"].value == "1-1"


def test_red_card_and_big_chance_packs(replayed):
    red = next(m for m in replayed.moments if m.type == "red_card")
    f = _facts(red)
    assert f["player"].value == "Yardley" and f["card"].value == "second yellow"
    assert red.wp_after.home > red.wp_before.home  # RVR down to ten
    assert red.significance == pytest.approx(0.8)
    chance = next(m for m in replayed.moments if m.type == "big_chance")
    f = _facts(chance)
    assert {"shooter", "xg", "outcome", "distance_m", "team_xg_total"} <= f.keys()
    assert f["xg"].value == 0.42 and f["outcome"].value == "saved"
    assert chance.significance == pytest.approx(0.3 + 0.5 * 0.42)


def test_every_moment_has_base_facts_and_formatting(replayed):
    for m in replayed.moments:
        f = _facts(m)
        assert {"minute", "score", "home_team", "away_team"} <= f.keys(), m.type
        assert MINUTE_RE.match(f["minute"].value), f["minute"].value
        assert f["home_team"].value == "Northbridge Athletic"
        assert len(f) == len(m.facts), "fact keys must be unique"
        for fact in m.facts:
            if fact.unit == "%":
                assert isinstance(fact.value, int) and 0 <= fact.value <= 100 or fact.key.endswith("delta_pct")
            if fact.unit == "xG":
                assert round(fact.value, 2) == fact.value
            if fact.unit == "km/h":
                assert isinstance(fact.value, int)
            if fact.unit == "m":
                assert round(fact.value, 1) == fact.value
            if fact.key.endswith("_delta"):
                base = fact.key[: -len("_delta")]
                if f"{base}_before" in f and f"{base}_after" in f:
                    assert f[f"{base}_after"].value - f[f"{base}_before"].value == fact.value
        assert 0 <= m.significance <= 1
        assert m.seq_start <= m.seq_end
        assert m.trigger


def test_cooldowns(replayed):
    ms = replayed.moments  # emission order
    gated = [m for m in ms if m.type not in FULLY_EXEMPT and not _is_goal_milestone(m)]
    for i, m in enumerate(ms):
        if i and m in gated and m.type not in MP.global_exempt:
            assert m.clock_ms - ms[i - 1].clock_ms >= MP.global_cooldown_s * 1000, (ms[i - 1].type, m.type)
    by_key: dict = {}
    for m in gated:
        key = (m.type, "*" if m.type in ("elite_pass", "phase_change") else m.team_id)
        if m.type == "player_milestone":
            key = (m.type, m.player_ids[0])
        if key in by_key:
            assert m.clock_ms - by_key[key] >= MP.same_type_cooldown_s * 1000, key
        by_key[key] = m.clock_ms


def _is_goal_milestone(m: Moment) -> bool:
    return m.type == "player_milestone" and _facts(m)["milestone"].value in ("brace", "hat-trick")


def test_replay_is_deterministic_and_idempotent(fixture_items):
    a, b = (
        MatchEngine("fixture-nba-rvr", teams_by_id=load_teams()),
        MatchEngine("fixture-nba-rvr", teams_by_id=load_teams()),
    )
    for item in fixture_items:
        for eng in (a, b):
            if isinstance(item, MatchEvent):
                eng.process_event(item)
                if eng is b:
                    assert eng.process_event(item).moments == []  # redelivery is ignored
            else:
                eng.process_frame(item)
    strip = lambda ms: [(m.type, m.clock_ms, m.seq_start, m.seq_end, [f.value for f in m.facts]) for m in ms]  # noqa: E731
    assert strip(a.moments) == strip(b.moments)


# --------------------------------------------------------------------------- synthetic


class Script:
    """Tiny event writer for targeted rule tests (NBA home v RVR away)."""

    def __init__(self) -> None:
        self.engine = MatchEngine("syn", teams_by_id=load_teams())
        self.seq = 0
        self.out: list[Moment] = []
        self.send(EventType.MATCH_START, clock_ms=0, extra={"home_team_id": "NBA", "away_team_id": "RVR"})

    def send(self, type_, clock_ms, team=None, player=None, **kw) -> list[Moment]:
        ev = MatchEvent(
            match_id="syn", seq=self.seq, period=1, clock_ms=clock_ms, type=type_, team_id=team, player_id=player, **kw
        )
        self.seq += 1
        got = self.engine.process_event(ev).moments
        self.out += got
        return got

    def elite_pass(self, clock_ms, team="NBA") -> list[Moment]:
        pa = PassAttributes(
            length_m=35,
            progressive_m=25,
            ball_speed_kmh=70,
            expected_completion=0.21,
            difficulty=7.9,
            recipient_id=f"{team}-10",
        )
        return self.send(
            EventType.PASS,
            clock_ms,
            team,
            f"{team}-08",
            x=50,
            y=30,
            end_x=75,
            end_y=40,
            outcome=Outcome.SUCCESS,
            **{"pass": pa},
        )

    def shot(self, clock_ms, xg, team="NBA", outcome=Outcome.SAVED) -> list[Moment]:
        sa = ShotAttributes(
            distance_m=14, angle_rad=0.5, body_part="right_foot", xg=xg, shot_speed_kmh=90, on_target=True
        )
        return self.send(EventType.SHOT, clock_ms, team, f"{team}-10", x=91, y=34, outcome=outcome, shot=sa)


def test_elite_pass_cooldown():
    s = Script()

    def elite(clock, team="NBA"):
        return [m.type for m in s.elite_pass(clock, team) if m.type == "elite_pass"]

    assert elite(600_000) == ["elite_pass"]
    assert elite(630_000) == []  # 30 s later: global + same-type cooldown
    assert elite(700_000, team="RVR") == []  # 100 s later, other team: elite_pass is max 1 per 3 min
    assert elite(600_000 + 181_000) == ["elite_pass"]


def test_goal_ignores_cooldown():
    s = Script()
    s.elite_pass(600_000)
    s.shot(605_000, 0.2, outcome=Outcome.GOAL)
    got = s.send(EventType.GOAL, 605_500, "NBA", "NBA-10", score_home=1)
    assert [m.type for m in got] == ["goal"]


def test_wp_swing_from_shots_after_15_minutes():
    s = Script()
    s.send(EventType.KICK_OFF, 0, "NBA", "NBA-10", x=52.5, y=34)
    s.send(EventType.PASS, 20 * 60_000, "NBA", "NBA-06", x=40, y=30, end_x=50, end_y=30, outcome=Outcome.SUCCESS)
    got = s.shot(20 * 60_000 + 10_000, 0.25, team="RVR") + s.shot(20 * 60_000 + 40_000, 0.25, team="RVR")
    swings = [m for m in got if m.type == "wp_swing"]
    assert len(swings) == 1
    f = _facts(swings[0])
    assert f["cause_event"].value == "shot"
    assert swings[0].team_id == "RVR"
    assert abs(f["wp_away_delta"].value) >= 6
    assert f["wp_away_after"].value - f["wp_away_before"].value == f["wp_away_delta"].value


def test_wp_swing_ignores_clock_drift():
    """The xG window rolling over changes WP but is not an on-pitch cause: no swing on passes."""
    s = Script()
    s.shot(60_000, 0.25, team="RVR")
    for i in range(40):
        s.send(
            EventType.PASS,
            15 * 60_000 + i * 30_000,
            "NBA",
            "NBA-06",
            x=40,
            y=30,
            end_x=50,
            end_y=30,
            outcome=Outcome.SUCCESS,
        )
    assert not [m for m in s.out if m.type == "wp_swing"]


def test_brace_milestone():
    s = Script()
    for k, clock in enumerate([600_000, 1_200_000]):
        s.shot(clock, 0.3, outcome=Outcome.GOAL)
        s.send(EventType.GOAL, clock + 500, "NBA", "NBA-10", score_home=k + 1)
    ms = [m for m in s.out if m.type == "player_milestone"]
    assert len(ms) == 1 and _facts(ms[0])["milestone"].value == "brace"
    assert [m.type for m in s.out].count("goal") == 2
