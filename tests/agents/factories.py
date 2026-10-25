"""Synthetic moments and contexts for agent tests (fact keys and formatting per MODEL_SPEC §4)."""

from __future__ import annotations

from functools import cache
from typing import Any

from fulcrum.agents.context import MatchContextStore, load_teams
from fulcrum.agents.schemas import MomentContext
from fulcrum.contracts import Fact, MatchConfig, Moment, MomentType, Team, WinProbability

MATCH_ID = "m-test"
HOME, AWAY = "NBA", "RVR"


@cache
def league() -> tuple[Team, ...]:
    return tuple(load_teams())


def team(team_id: str) -> Team:
    return next(t for t in league() if t.team_id == team_id)


def F(key: str, value: float | int | str, unit: str | None = None, label: str | None = None) -> Fact:  # noqa: N802
    return Fact(key=key, label=label or key.replace("_", " ").capitalize(), value=value, unit=unit)


def _common(minute: str, score: str) -> list[Fact]:
    return [
        F("minute", minute, label="Minute"),
        F("score", score, label="Score"),
        F("home_team", "Northbridge Athletic", label="Home team"),
        F("away_team", "Riverside Rovers", label="Away team"),
    ]


# type → (team_id, player_ids, minute, score, specific facts)
SPECS: dict[MomentType, tuple[str | None, list[str], str, str, list[Fact]]] = {
    MomentType.GOAL: (
        HOME,
        ["NBA-10", "NBA-09"],
        "67'",
        "1-0",
        [
            F("scorer", "Brennick", label="Scorer"),
            F("xg", 0.34, "xG", "Shot xG"),
            F("shot_speed_kmh", 98, "km/h", "Shot speed"),
            F("distance_m", 14.2, "m", "Shot distance"),
            F("wp_scorer_before", 48, "%", "Scorer win probability before"),
            F("wp_scorer_after", 71, "%", "Scorer win probability after"),
            F("wp_scorer_delta", 23, "%", "Scorer win probability change"),
            F("score_before", "0-0", label="Score before the goal"),
            F("score_after", "1-0", label="Score after"),
            F("situation", "open play", label="Shot situation"),
            F("assist", "Aldane", label="Assist"),
            F("pass_difficulty_of_assist", 7.8, label="Assist pass difficulty (0-10)"),
        ],
    ),
    MomentType.BIG_CHANCE: (
        AWAY,
        ["RVR-09"],
        "23'",
        "0-0",
        [
            F("shooter", "Draycott", label="Shooter"),
            F("xg", 0.41, "xG", "Shot xG"),
            F("outcome", "saved", label="Outcome"),
            F("distance_m", 9.5, "m", "Shot distance"),
            F("team_xg_total", 0.87, "xG", "Team xG so far"),
            # what-if facts (MODEL_SPEC §3.1), as the engine emits them
            F("whatif_goal_score", "0-1", label="Score if it had gone in (Northbridge Athletic first)"),
            F("whatif_win_pct_now", 31, "%", "Riverside Rovers win probability now"),
            F("whatif_goal_win_pct", 52, "%", "Riverside Rovers win probability if it had gone in"),
            F("whatif_goal_win_delta", 21, "pp", "Rise in Riverside Rovers win probability if it had gone in"),
            F("whatif_goal_draw_pct", 26, "%", "Draw probability if it had gone in"),
        ],
    ),
    MomentType.WP_SWING: (
        AWAY,
        ["RVR-03"],
        "58'",
        "1-1",
        [
            F("wp_home_before", 52, "%", "Home win probability before"),
            F("wp_home_after", 40, "%", "Home win probability after"),
            F("wp_home_delta", -12, "%", "Home win probability change"),
            F("wp_away_before", 22, "%", "Away win probability before"),
            F("wp_away_after", 33, "%", "Away win probability after"),
            F("wp_away_delta", 11, "%", "Away win probability change"),
            F("cause_event", "Yardley interception", label="Cause"),
            F("pressure_index_home", 41, label="Home pressure index"),
            F("pressure_index_away", 68, label="Away pressure index"),
        ],
    ),
    MomentType.PRESSURE_SHIFT: (
        AWAY,
        [],
        "34'",
        "0-0",
        [
            F("pressure_index_home_before", 62, label="Home pressure before"),
            F("pressure_index_home_after", 35, label="Home pressure after"),
            F("pressure_index_away_before", 30, label="Away pressure before"),
            F("pressure_index_away_after", 66, label="Away pressure after"),
            F("pressure_home_delta", -27, label="Home pressure change"),
            F("pressure_away_delta", 36, label="Away pressure change"),
            F("shots_last_5", 3, label="Shots in last 5 min"),
            F("box_entries_last_5", 5, label="Box entries in last 5 min"),
        ],
    ),
    MomentType.PHASE_CHANGE: (
        HOME,
        [],
        "41'",
        "0-0",
        [
            F("phase_before", "balanced", label="Phase before"),
            F("phase_after", "Northbridge Athletic in control", label="Phase after"),
            F("control_index_home", 72, label="Home control index"),
            F("control_index_away", 31, label="Away control index"),
            F("chaos_index", 18, label="Chaos index"),
            F("turnovers_last_5", 4, label="Turnovers in last 5 min"),
        ],
    ),
    MomentType.TEMPO_CHANGE: (
        None,
        [],
        "52'",
        "1-0",
        [
            F("tempo_ppm", 14.2, "passes/min", "Tempo"),
            F("tempo_match_avg", 9.8, "passes/min", "Match average tempo"),
        ],
    ),
    MomentType.RED_CARD: (
        AWAY,
        ["RVR-02"],
        "71'",
        "1-1",
        [
            F("player", "Castellane", label="Player sent off"),
            F("wp_home_before", 45, "%", "Home win probability before"),
            F("wp_home_after", 58, "%", "Home win probability after"),
            F("wp_home_delta", 13, "%", "Home win probability change"),
            F("wp_away_before", 30, "%", "Away win probability before"),
            F("wp_away_after", 19, "%", "Away win probability after"),
            F("wp_away_delta", -11, "%", "Away win probability change"),
        ],
    ),
    MomentType.SPEED_RECORD: (
        HOME,
        ["NBA-11"],
        "15'",
        "0-0",
        [
            F("player", "Fenwick-Shaw", label="Player"),
            F("speed_kmh", 116, "km/h", "Shot speed"),
            F("previous_match_high", 104, "km/h", "Previous match high"),
        ],
    ),
    MomentType.ELITE_PASS: (
        HOME,
        ["NBA-09", "NBA-10"],
        "64'",
        "0-0",
        [
            F("passer", "Aldane", label="Passer"),
            F("recipient", "Brennick", label="Recipient"),
            F("difficulty", 8.1, label="Pass difficulty (0-10)"),
            F("expected_completion_pct", 18, "%", "Expected completion"),
            F("length_m", 38.5, "m", "Pass length"),
            F("progressive_m", 27.0, "m", "Metres gained"),
            F("ball_speed_kmh", 74, "km/h", "Ball speed"),
        ],
    ),
    MomentType.PLAYER_MILESTONE: (
        HOME,
        ["NBA-08"],
        "61'",
        "1-0",
        [
            F("player", "Loxley", label="Player"),
            F("milestone", "50th completed pass", label="Milestone"),
            F("value", 50, label="Completed passes"),
        ],
    ),
    MomentType.MOMENTUM_RUN: (
        AWAY,
        [],
        "77'",
        "1-0",
        [
            F("team", "Riverside Rovers", label="Team"),
            F("shots_last_5", 4, label="Shots in last 5 min"),
            F("opp_shots_last_5", 0, label="Opponent shots in last 5 min"),
            F("xg_last_5", 0.86, "xG", "xG in last 5 min"),
            F("final_third_possessions_in_row", 5, label="Consecutive possessions reaching the final third"),
        ],
    ),
    MomentType.HALF_TIME: (
        None,
        [],
        "45'",
        "1-0",
        [
            F("xg_home", 1.12, "xG", "Home xG"),
            F("xg_away", 0.64, "xG", "Away xG"),
            F("possession_home", 57, "%", "Home possession"),
            F("possession_away", 43, "%", "Away possession"),
            F("wp_home", 61, "%", "Home win probability"),
            F("wp_draw", 25, "%", "Draw probability"),
            F("wp_away", 14, "%", "Away win probability"),
            F("top_player_1", "Brennick", label="Top rated player"),
            F("top_player_1_rating", 8.2, label="Top player rating"),
        ],
    ),
    MomentType.FULL_TIME: (
        None,
        [],
        "90'",
        "2-1",
        [
            F("xg_home", 2.31, "xG", "Home xG"),
            F("xg_away", 1.05, "xG", "Away xG"),
            F("possession_home", 55, "%", "Home possession"),
            F("possession_away", 45, "%", "Away possession"),
            F("wp_home", 100, "%", "Home win probability"),
            F("wp_draw", 0, "%", "Draw probability"),
            F("wp_away", 0, "%", "Away win probability"),
            F("top_player_1", "Brennick", label="Top rated player"),
            F("top_player_1_rating", 8.6, label="Top player rating"),
            F("top_player_2", "Draycott", label="Second rated player"),
            F("top_player_2_rating", 7.9, label="Second player rating"),
        ],
    ),
}


def make_moment(mtype: MomentType = MomentType.GOAL, **overrides: Any) -> Moment:
    """A valid Moment of `mtype` with a realistic fact pack."""
    team_id, players, minute, score, facts = SPECS[mtype]
    clock_ms = (int(minute.rstrip("'")) - 1) * 60_000 + 30_000
    data: dict[str, Any] = {
        "match_id": MATCH_ID,
        "type": mtype,
        "significance": 0.5,
        "period": 1 if clock_ms < 45 * 60_000 else 2,
        "clock_ms": clock_ms,
        "seq_start": 100,
        "seq_end": 104,
        "team_id": team_id,
        "player_ids": players,
        "wp_before": WinProbability(home=0.48, draw=0.3, away=0.22),
        "wp_after": WinProbability(home=0.71, draw=0.17, away=0.12),
        "facts": [*_common(minute, score), *facts],
        "trigger": f"test rule for {mtype}",
    }
    data.update(overrides)
    return Moment(**data)


def make_store() -> MatchContextStore:
    store = MatchContextStore(league())
    store.register_match(MatchConfig(match_id=MATCH_ID, home_team_id=HOME, away_team_id=AWAY))
    return store


def make_context(moment: Moment | None = None) -> MomentContext:
    ctx = make_store().build_context(moment or make_moment())
    assert ctx is not None
    return ctx
