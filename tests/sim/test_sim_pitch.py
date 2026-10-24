"""Sanity values for the closed-form models, straight from MODEL_SPEC §1.3 and §1.5."""

import math

import pytest

from fulcrum.sim import pitch


@pytest.mark.parametrize(
    ("x", "y", "expected"),
    [
        (99.0, 34.0, 0.66),  # 6 m central
        (94.0, 34.0, 0.37),  # penalty spot, open play
        (85.0, 34.0, 0.14),  # 20 m central
        (80.0, 34.0, 0.08),  # 25 m central
    ],
)
def test_xg_sanity(x, y, expected):
    assert pitch.xg(x, y) == pytest.approx(expected, abs=0.01)


def test_xg_header_bonus_and_clamp():
    open_play = pitch.xg(94.0, 34.0)
    assert pitch.xg(94.0, 34.0, header=True) == pytest.approx(open_play * 0.6)
    assert pitch.xg(94.0, 34.0, bonus=0.10) == pytest.approx(open_play + 0.10)
    assert pitch.xg(104.9, 34.0, bonus=0.5) == 0.95
    assert pitch.xg(0.0, 0.0) == 0.01


def test_shot_angle_geometry():
    assert pitch.shot_angle(94.0, 34.0) == pytest.approx(2 * math.atan(3.66 / 11.0), abs=1e-9)
    # Inside the goal-mouth semicircle the angle is obtuse (the spec's "+π" branch).
    assert pitch.shot_angle(104.0, 34.0) > math.pi / 2
    assert pitch.shot_angle(105.0, 10.0) == pytest.approx(0.0)


def test_pass_completion_sanity():
    square = pitch.pass_completion(length_m=15.0, progressive_m=0.0)
    assert square == pytest.approx(0.93, abs=0.01)
    assert pitch.pass_difficulty(square) == 0.7
    hard = pitch.pass_completion(length_m=30.0, progressive_m=20.0, under_pressure=True, line_breaking=True)
    assert hard == pytest.approx(0.34, abs=0.01)
    assert pitch.pass_difficulty(hard) == 6.6


def test_pass_completion_clamped():
    assert pitch.pass_completion(length_m=0.0, progressive_m=0.0, passing=100, strength=1.0) == 0.98
    assert pitch.pass_completion(length_m=90.0, progressive_m=80.0, under_pressure=True, is_cross=True) == 0.20


def test_flip_is_an_involution_and_box():
    assert pitch.flip(*pitch.flip(12.3, 45.6)) == pytest.approx((12.3, 45.6))
    assert pitch.flip(0.0, 0.0) == (105.0, 68.0)
    assert pitch.in_box(*pitch.PENALTY_SPOT)
    assert not pitch.in_box(88.0, 34.0)
