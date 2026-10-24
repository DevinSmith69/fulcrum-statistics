"""Pitch geometry plus the closed-form xG and pass-completion models (MODEL_SPEC §1.3, §1.5).

All coordinates here are TEAM-RELATIVE: the acting team attacks towards x = 105.
"""

from __future__ import annotations

import math

from fulcrum.contracts import PITCH_LENGTH_M, PITCH_WIDTH_M

from .params import DEFAULT_PARAMS, SimParams

LENGTH = PITCH_LENGTH_M
WIDTH = PITCH_WIDTH_M
CENTRE = (LENGTH / 2, WIDTH / 2)
GOAL_X = LENGTH
GOAL_Y = WIDTH / 2
GOAL_WIDTH = 7.32
POST_LOW = GOAL_Y - GOAL_WIDTH / 2  # 30.34
POST_HIGH = GOAL_Y + GOAL_WIDTH / 2  # 37.66
BOX_X = LENGTH - 16.5  # 88.5
BOX_Y_LOW = GOAL_Y - 20.16  # 13.84
BOX_Y_HIGH = GOAL_Y + 20.16  # 54.16
PENALTY_SPOT = (LENGTH - 11.0, GOAL_Y)


def sigmoid(z: float) -> float:
    """Numerically safe logistic function."""
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def clamp(v: float, lo: float, hi: float) -> float:
    """Clamp `v` into [lo, hi]."""
    return lo if v < lo else hi if v > hi else v


def flip(x: float, y: float) -> tuple[float, float]:
    """Rotate a point 180° about the centre spot (switches between the two teams' frames)."""
    return LENGTH - x, WIDTH - y


def clamp_point(x: float, y: float, margin: float = 0.0) -> tuple[float, float]:
    """Keep a point on the pitch, optionally `margin` metres inside the lines."""
    return clamp(x, margin, LENGTH - margin), clamp(y, margin, WIDTH - margin)


def in_box(x: float, y: float) -> bool:
    """True if (x, y) is inside the opponent's penalty area."""
    return x >= BOX_X and BOX_Y_LOW <= y <= BOX_Y_HIGH


def is_wide(y: float, p: SimParams = DEFAULT_PARAMS) -> bool:
    """True in the wide channels where crosses are an option."""
    return y < p.wide_y_low or y > p.wide_y_high


def shot_distance(x: float, y: float) -> float:
    """Distance in metres from (x, y) to the centre of the goal."""
    return math.hypot(GOAL_X - x, GOAL_Y - y)


def shot_angle(x: float, y: float) -> float:
    """Angle (rad) subtended by the goal mouth at (x, y).

    atan2 already returns the obtuse branch when the denominator is negative, which is the
    spec's "add π" correction for a plain arctan.
    """
    dx = max(GOAL_X - x, 0.0)
    dy = abs(GOAL_Y - y)
    return math.atan2(GOAL_WIDTH * dx, dx * dx + dy * dy - (GOAL_WIDTH / 2) ** 2)


def xg(x: float, y: float, *, header: bool = False, bonus: float = 0.0, p: SimParams = DEFAULT_PARAMS) -> float:
    """Open-play expected goals for a shot from (x, y). `bonus` is added before the clamp."""
    z = p.xg_intercept + p.xg_distance * shot_distance(x, y) + p.xg_angle * shot_angle(x, y)
    value = sigmoid(z)
    if header:
        value *= p.xg_header_factor
    return clamp(value + bonus, p.xg_min, p.xg_max)


def pass_completion(
    *,
    length_m: float,
    progressive_m: float,
    under_pressure: bool = False,
    line_breaking: bool = False,
    into_box: bool = False,
    is_cross: bool = False,
    passing: int = 70,
    opp_defence: int = 70,
    strength: float = 0.0,
    home: bool = False,
    p: SimParams = DEFAULT_PARAMS,
) -> float:
    """p(complete) for a pass (MODEL_SPEC §1.3 + §1.7 strength and home advantage)."""
    z = (
        p.pc_intercept
        + p.pc_length * length_m
        + p.pc_progressive * max(progressive_m, 0.0)
        + p.pc_pressure * under_pressure
        + p.pc_line_breaking * line_breaking
        + p.pc_into_box * into_box
        + p.pc_cross * is_cross
        + p.pc_passing * (passing - 70)
        + p.pc_opp_defence * (opp_defence - 70)
        + p.strength_pass_logit * strength
        + (p.home_pass_logit if home else 0.0)
    )
    return clamp(sigmoid(z), p.pc_min, p.pc_max)


def pass_difficulty(p_complete: float) -> float:
    """0 trivial … 10 near-impossible, one decimal."""
    return round(10.0 * (1.0 - p_complete), 1)
