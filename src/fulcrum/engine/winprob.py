"""Live match outcome probability (MODEL_SPEC §3) — independent Poisson goals for the remaining time.

Pure functions, no state. Presented in the UI as "Match outcome probability", never as odds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from fulcrum.contracts import Team, WinProbability

from .params import DEFAULT_PARAMS, WinProbParams


@dataclass(frozen=True)
class TeamStrength:
    """The two team inputs the model needs."""

    goal_rate: float  # expected goals per 90 vs an average opponent
    defence: float  # defence_rating 0-100

    @classmethod
    def from_team(cls, team: Team | None, params: WinProbParams = DEFAULT_PARAMS.winprob) -> TeamStrength:
        """Strength from a `Team`, or league-average defaults when the team is unknown."""
        if team is None:
            return cls(params.default_goal_rate, params.default_defence)
        return cls(team.pre_match_goal_rate, float(team.defence_rating))


def pre_match_rates(
    home: TeamStrength, away: TeamStrength, params: WinProbParams = DEFAULT_PARAMS.winprob
) -> tuple[float, float]:
    """Pre-match goal rates per 90 minutes: (λ_home, λ_away)."""
    lam_home = home.goal_rate * (params.reference_defence / away.defence) * params.home_factor
    lam_away = away.goal_rate * (params.reference_defence / home.defence) * params.away_factor
    return lam_home, lam_away


def remaining_minutes(period: int, minute: float, params: WinProbParams = DEFAULT_PARAMS.winprob) -> float:
    """Expected minutes left. `minute` is the football minute (P2 starts at 45 regardless of H1 stoppage)."""
    half = params.half_length_min
    if period <= 1:
        return max(0.0, half - minute) + half + params.expected_stoppage_min
    return max(0.0, 2 * half + params.expected_stoppage_min - minute)


def live_rate_per_min(
    pre_per_90: float,
    minute: float,
    xg_last_window: float | None,
    params: WinProbParams = DEFAULT_PARAMS.winprob,
) -> float:
    """Per-minute rate, blended with recent xG after `live_after_min` (None = no live data → pre-match rate)."""
    r_pre = pre_per_90 / 90.0
    if minute < params.live_after_min or xg_last_window is None:
        return r_pre
    return params.w_pre * r_pre + params.w_live * (xg_last_window / params.xg_window_min)


def _poisson_pmf(lam: float, n: int) -> list[float]:
    if lam <= 0:
        return [1.0] + [0.0] * n
    out = [math.exp(-lam)]
    for k in range(1, n + 1):
        out.append(out[-1] * lam / k)
    return out


def outcome_probabilities(
    score_home: int,
    score_away: int,
    goals_home_rest: float,
    goals_away_rest: float,
    max_goals: int = DEFAULT_PARAMS.winprob.max_goals,
) -> WinProbability:
    """P(home win / draw / away win) from the current score and expected remaining goals (grid to `max_goals`)."""
    ph = _poisson_pmf(goals_home_rest, max_goals)
    pa = _poisson_pmf(goals_away_rest, max_goals)
    home = draw = away = 0.0
    diff0 = score_home - score_away
    for i, pi in enumerate(ph):
        for j, pj in enumerate(pa):
            p = pi * pj
            d = diff0 + i - j
            if d > 0:
                home += p
            elif d == 0:
                draw += p
            else:
                away += p
    total = home + draw + away
    return WinProbability(home=home / total, draw=draw / total, away=away / total)


def win_probability(
    *,
    home: TeamStrength,
    away: TeamStrength,
    period: int,
    minute: float,
    score_home: int,
    score_away: int,
    xg_last_window_home: float | None = None,
    xg_last_window_away: float | None = None,
    reds_home: int = 0,
    reds_away: int = 0,
    finished: bool = False,
    params: WinProbParams = DEFAULT_PARAMS.winprob,
) -> WinProbability:
    """Full MODEL_SPEC §3 model. `xg_last_window_*` is team xG in the last 15 minutes (None → pre-match rate)."""
    lam_h90, lam_a90 = pre_match_rates(home, away, params)
    r_home = live_rate_per_min(lam_h90, minute, xg_last_window_home, params)
    r_away = live_rate_per_min(lam_a90, minute, xg_last_window_away, params)

    r_home *= params.red_card_down**reds_home * params.red_card_opponent**reds_away
    r_away *= params.red_card_down**reds_away * params.red_card_opponent**reds_home

    if minute >= params.game_state_after_min and score_home != score_away:
        home_leads = score_home > score_away
        r_home *= params.leading_factor if home_leads else params.trailing_factor
        r_away *= params.trailing_factor if home_leads else params.leading_factor

    t = 0.0 if finished else remaining_minutes(period, minute, params)
    return outcome_probabilities(score_home, score_away, r_home * t, r_away * t, params.max_goals)
