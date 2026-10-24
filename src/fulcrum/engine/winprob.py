"""Live match outcome probability (MODEL_SPEC §3) — independent Poisson goals for the remaining time.

Pure functions, no state. Presented in the UI as "Match outcome probability", never as odds.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
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


def recent_xg_rate(
    shots: Iterable[tuple[float, float]],
    elapsed_min: float,
    prior_rate: float | None = None,
    params: WinProbParams = DEFAULT_PARAMS.winprob,
) -> float | None:
    """Recent attacking rate (xG per minute) from `(age_min, xg)` shots, exponentially decayed.

    Each shot's weight is exp(−age/τ), normalised by the weight mass of the minutes played so far, so a team
    creating xG at a steady rate r reads r, and a chance fades out smoothly instead of falling off a window
    edge. With `prior_rate`, the estimate is shrunk towards it by `xg_prior_min` pseudo-minutes so one big
    chance early on cannot double the rate. None before any time has been played.
    """
    tau = params.xg_decay_min
    norm = tau * (1.0 - math.exp(-max(0.0, elapsed_min) / tau))
    if norm <= 1e-9:
        return None
    decayed = sum(xg * math.exp(-max(0.0, age) / tau) for age, xg in shots)
    if prior_rate is None:
        return decayed / norm
    k = params.xg_prior_min
    return (decayed + prior_rate * k) / (norm + k)


def live_weight(minute: float, params: WinProbParams = DEFAULT_PARAMS.winprob) -> float:
    """Weight of the live xG rate: ramps linearly from 0 at kick-off to `w_live` at `live_ramp_min`."""
    return params.w_live * min(1.0, max(0.0, minute) / params.live_ramp_min)


def live_rate_per_min(
    pre_per_90: float,
    minute: float,
    recent_rate: float | None,
    params: WinProbParams = DEFAULT_PARAMS.winprob,
) -> float:
    """Per-minute rate: pre-match rate blended with the recent xG rate (None = no live data → pre-match rate)."""
    r_pre = pre_per_90 / 90.0
    if recent_rate is None:
        return r_pre
    w = live_weight(minute, params)
    return (1.0 - w) * r_pre + w * recent_rate


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
    xg_rate_home: float | None = None,
    xg_rate_away: float | None = None,
    reds_home: int = 0,
    reds_away: int = 0,
    finished: bool = False,
    params: WinProbParams = DEFAULT_PARAMS.winprob,
) -> WinProbability:
    """Full MODEL_SPEC §3 model. `xg_rate_*` is the team's recent xG per minute (`recent_xg_rate`; None → pre-match)."""
    lam_h90, lam_a90 = pre_match_rates(home, away, params)
    r_home = live_rate_per_min(lam_h90, minute, xg_rate_home, params)
    r_away = live_rate_per_min(lam_a90, minute, xg_rate_away, params)

    r_home *= params.red_card_down**reds_home * params.red_card_opponent**reds_away
    r_away *= params.red_card_down**reds_away * params.red_card_opponent**reds_home

    if minute >= params.game_state_after_min and score_home != score_away:
        home_leads = score_home > score_away
        r_home *= params.leading_factor if home_leads else params.trailing_factor
        r_away *= params.trailing_factor if home_leads else params.leading_factor

    t = 0.0 if finished else remaining_minutes(period, minute, params)
    return outcome_probabilities(score_home, score_away, r_home * t, r_away * t, params.max_goals)
