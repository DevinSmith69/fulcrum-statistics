"""MODEL_SPEC §3 sanity tests — reference values in the spec are reproduced exactly."""

import pytest

from fulcrum.engine.winprob import TeamStrength, outcome_probabilities, pre_match_rates, win_probability

EVEN = TeamStrength(goal_rate=1.35, defence=75)


def _at_pre_match_pace(minute: float) -> tuple[float, float]:
    """xG in the last 15' if both teams play exactly at their pre-match rate (r_live == r_pre)."""
    lam_h, lam_a = pre_match_rates(EVEN, EVEN)
    return lam_h / 90 * 15, lam_a / 90 * 15


def _wp(**kw):
    return win_probability(home=EVEN, away=EVEN, **kw)


def test_even_teams_at_kick_off():
    wp = _wp(period=1, minute=0, score_home=0, score_away=0)
    assert 0.40 <= wp.home <= 0.48
    assert 0.22 <= wp.draw <= 0.30
    assert wp.home == pytest.approx(0.417, abs=0.001)
    assert wp.draw == pytest.approx(0.248, abs=0.001)
    assert wp.home > wp.away  # home advantage


def test_one_nil_at_89_leader_wins():
    xh, xa = _at_pre_match_pace(89)
    wp = _wp(period=2, minute=89, score_home=1, score_away=0, xg_last_window_home=xh, xg_last_window_away=xa)
    assert wp.home >= 0.90
    assert wp.home == pytest.approx(0.927, abs=0.001)


def test_goal_at_80_scorer_favoured():
    xh, xa = _at_pre_match_pace(80)
    before = _wp(period=2, minute=80, score_home=0, score_away=0, xg_last_window_home=xh, xg_last_window_away=xa)
    after = _wp(period=2, minute=80, score_home=1, score_away=0, xg_last_window_home=xh, xg_last_window_away=xa)
    assert after.home >= 0.80
    assert after.home == pytest.approx(0.829, abs=0.001)
    assert after.home - before.home > 0.4


@pytest.mark.parametrize(
    "kw",
    [
        dict(period=1, minute=0, score_home=0, score_away=0),
        dict(period=1, minute=30, score_home=2, score_away=1, xg_last_window_home=0.8, xg_last_window_away=0.1),
        dict(period=2, minute=70, score_home=0, score_away=3, reds_home=1),
        dict(period=2, minute=95, score_home=1, score_away=1),
        dict(period=2, minute=90, score_home=2, score_away=2, finished=True),
    ],
)
def test_probabilities_sum_to_one(kw):
    wp = _wp(**kw)
    assert wp.home + wp.draw + wp.away == pytest.approx(1.0, abs=1e-6)
    assert all(0 <= p <= 1 for p in (wp.home, wp.draw, wp.away))


def test_red_card_hurts_the_team_down_a_man():
    base = _wp(period=1, minute=30, score_home=0, score_away=0)
    red = _wp(period=1, minute=30, score_home=0, score_away=0, reds_home=1)
    assert red.home < base.home and red.away > base.away


def test_finished_match_is_certain():
    wp = _wp(period=2, minute=94, score_home=2, score_away=1, finished=True)
    assert wp.home == pytest.approx(1.0)
    wp = outcome_probabilities(1, 1, 0.0, 0.0)
    assert wp.draw == pytest.approx(1.0)


def test_stronger_team_favoured():
    strong = TeamStrength(goal_rate=1.75, defence=78)
    weak = TeamStrength(goal_rate=1.1, defence=68)
    wp = win_probability(home=weak, away=strong, period=1, minute=0, score_home=0, score_away=0)
    assert wp.away > wp.home
