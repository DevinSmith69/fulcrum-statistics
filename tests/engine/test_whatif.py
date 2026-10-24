"""What-if counterfactuals (MODEL_SPEC §3.1): the live model at the same instant with one more goal."""

from pathlib import Path

import pytest

from fulcrum.contracts import EventType, Outcome
from fulcrum.engine.moments import pct
from fulcrum.engine.replay import run_file
from fulcrum.engine.winprob import TeamStrength, win_probability
from tests.engine.test_moments import Script

EVEN = TeamStrength(goal_rate=1.35, defence=75)
WHATIF_KEYS = {
    "whatif_goal_score",
    "whatif_win_pct_now",
    "whatif_goal_win_pct",
    "whatif_goal_win_delta",
    "whatif_goal_draw_pct",
}
SAMPLES = sorted((Path(__file__).resolve().parents[2] / "data" / "matches").glob("sample_*.jsonl"))


def _facts(m) -> dict:
    return {f.key: f.value for f in m.facts}


@pytest.mark.parametrize("minute", [5, 30, 60, 85])
def test_more_goals_monotonically_raise_win_chance(minute):
    """Each extra goal for a team raises its win chance and lowers the opponent's (same minute, same rates)."""
    period = 1 if minute < 45 else 2
    home = [
        win_probability(home=EVEN, away=EVEN, period=period, minute=minute, score_home=k, score_away=1)
        for k in range(4)
    ]
    for a, b in zip(home, home[1:], strict=False):
        assert b.home > a.home and b.away < a.away
    away = [
        win_probability(home=EVEN, away=EVEN, period=period, minute=minute, score_home=1, score_away=k)
        for k in range(4)
    ]
    for a, b in zip(away, away[1:], strict=False):
        assert b.away > a.away and b.home < a.home


def test_tracker_whatif_is_the_live_model_with_one_more_goal():
    """`win_prob_if_scored` changes the score and nothing else: identical to the live model at that score."""
    s = Script()
    s.send(EventType.KICK_OFF, 0, "NBA", "NBA-10", x=52.5, y=34)
    s.shot(20 * 60_000, 0.45, team="RVR", outcome=Outcome.SAVED)
    t = s.engine.tracker
    now = t.win_prob()
    for team, attr in (("RVR", "score_away"), ("NBA", "score_home")):
        cf = t.win_prob_if_scored(team)
        setattr(t, attr, getattr(t, attr) + 1)
        try:
            assert t.win_prob() == cf
        finally:
            setattr(t, attr, getattr(t, attr) - 1)
    assert t.win_prob() == now
    assert t.win_prob_if_scored("RVR").away > now.away > t.win_prob_if_scored("NBA").away


def test_big_chance_carries_whatif_facts():
    s = Script()
    s.send(EventType.KICK_OFF, 0, "NBA", "NBA-10", x=52.5, y=34)
    got = s.shot(30 * 60_000, 0.5, team="RVR", outcome=Outcome.OFF_TARGET)
    chance = next(m for m in got if m.type == "big_chance")
    f = _facts(chance)
    assert WHATIF_KEYS <= f.keys()
    assert f["whatif_goal_score"] == "0-1"
    assert f["whatif_win_pct_now"] == pct(chance.wp_after.away)
    assert f["whatif_goal_win_pct"] == pct(s.engine.tracker.win_prob_if_scored("RVR").away)
    assert f["whatif_goal_win_delta"] == f["whatif_goal_win_pct"] - f["whatif_win_pct_now"] > 0
    labels = {x.key: x.label for x in chance.facts}
    assert labels["whatif_goal_win_pct"] == "Riverside Rovers win probability if it had gone in"


def test_whatif_on_replayed_fixture(replayed):
    """Both hand-made big chances carry consistent what-if facts; goals don't (wp_scorer_before covers it)."""
    chances = [m for m in replayed.moments if m.type == "big_chance"]
    assert len(chances) == 2
    for m in chances:
        f = _facts(m)
        assert WHATIF_KEYS <= f.keys()
        assert f["whatif_win_pct_now"] == pct(m.wp_after.home if m.team_id == "NBA" else m.wp_after.away)
        assert 0 < f["whatif_goal_win_delta"] == f["whatif_goal_win_pct"] - f["whatif_win_pct_now"]
        assert all(isinstance(f[k], int) and 0 <= f[k] <= 100 for k in ("whatif_goal_win_pct", "whatif_goal_draw_pct"))
    late = _facts(chances[1])  # NBA's 0.55 miss at 1-1, 86': would have been the winner
    assert late["whatif_goal_score"] == "2-1" and late["whatif_goal_win_pct"] >= 85
    for m in replayed.moments:
        if m.type == "goal":
            assert not WHATIF_KEYS & _facts(m).keys()


@pytest.mark.skipif(not SAMPLES, reason="sample fixtures not present")
@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_whatif_on_sim_samples(path):
    for m in run_file(path).moments:
        f = _facts(m)
        if m.type == "big_chance":
            assert WHATIF_KEYS <= f.keys()
        if WHATIF_KEYS & f.keys():
            assert f["whatif_goal_win_pct"] >= f["whatif_win_pct_now"]
