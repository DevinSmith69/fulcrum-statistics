"""Every engine tunable lives here (MODEL_SPEC §2–4). Never inline magic numbers in engine modules.

Tune by editing the defaults, or pass a modified copy: `EngineParams(moments=MomentParams(wp_swing_delta=0.08))`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Pitch geometry (team-relative event coordinates, actor attacks towards x = 105).
FINAL_THIRD_X = 70.0
BOX_X = 88.5
BOX_Y_MIN = 13.84
BOX_Y_MAX = 54.16
HALF_X = 52.5


@dataclass(frozen=True)
class MetricParams:
    """MODEL_SPEC §2 — rolling-window metrics (all windows in sim time)."""

    # Pressure index: decayed threat points per team.
    pressure_half_life_s: float = 180.0
    pressure_scale: float = 8.0
    pts_final_third_entry: float = 1.0
    pts_box_entry: float = 2.0
    pts_pass_into_box: float = 1.5
    pts_shot_base: float = 2.0
    pts_shot_per_xg: float = 10.0
    pts_corner: float = 1.5
    pts_recovery_opp_half: float = 1.5

    # Control / chaos / tempo windows.
    window_s: float = 300.0
    control_target_passes_per_possession: float = 8.0
    chaos_turnovers_per_min_cap: float = 2.5
    chaos_w_turnovers: float = 0.5
    chaos_w_short_possessions: float = 0.3
    chaos_w_long_passes: float = 0.2
    chaos_short_possession_passes: int = 3
    chaos_long_pass_m: float = 35.0
    # Rate denominators never drop below this, so the first minutes don't produce absurd rates.
    min_rate_window_s: float = 120.0

    # Phase classification + hysteresis.
    phase_chaos_threshold: float = 60.0
    phase_control_diff: float = 20.0
    phase_persist_s: float = 60.0

    # Momentum: EMA per event of pressure_home - pressure_away.
    momentum_alpha: float = 0.2

    # Tracking.
    sprint_kmh: float = 25.0
    max_tracking_gap_s: float = 5.0
    max_plausible_kmh: float = 45.0


@dataclass(frozen=True)
class RatingParams:
    """MODEL_SPEC §2 — player rating deltas (start 6.0, clamped 3–10)."""

    base: float = 6.0
    floor: float = 3.0
    ceiling: float = 10.0
    goal: float = 1.0
    assist: float = 0.6
    key_pass: float = 0.2
    per_xg: float = 1.0
    shot_on_target: float = 0.1
    tackle_or_interception: float = 0.1
    completed_pass_per_difficulty: float = 0.004  # × (1 + difficulty)
    failed_pass: float = -0.03
    dribble_won: float = 0.1
    yellow: float = -0.3
    red: float = -1.5
    error_leading_to_shot: float = -0.3


@dataclass(frozen=True)
class WinProbParams:
    """MODEL_SPEC §3 — independent-Poisson live outcome probability."""

    reference_defence: float = 75.0
    home_factor: float = 1.08
    away_factor: float = 0.95
    default_goal_rate: float = 1.35
    default_defence: float = 75.0
    live_after_min: float = 15.0
    w_pre: float = 0.7
    w_live: float = 0.3
    xg_window_min: float = 15.0
    red_card_down: float = 0.65
    red_card_opponent: float = 1.25
    game_state_after_min: float = 60.0
    trailing_factor: float = 1.15
    leading_factor: float = 0.90
    expected_stoppage_min: float = 4.0
    half_length_min: float = 45.0
    max_goals: int = 10


@dataclass(frozen=True)
class MomentParams:
    """MODEL_SPEC §4 — detection thresholds and cooldowns."""

    global_cooldown_s: float = 45.0
    same_type_cooldown_s: float = 180.0

    goal_base: float = 0.6
    goal_wp_cap: float = 0.4

    big_chance_xg: float = 0.30
    big_chance_base: float = 0.3
    big_chance_per_xg: float = 0.5

    wp_swing_delta: float = 0.06
    wp_swing_window_s: float = 90.0
    wp_swing_sig_per_delta: float = 5.0

    pressure_flip_diff: float = 25.0
    pressure_rise: float = 30.0
    pressure_rise_window_s: float = 180.0
    pressure_sig_min: float = 0.4
    pressure_sig_max: float = 0.6

    phase_change_sig: float = 0.35

    tempo_change_pct: float = 0.35
    tempo_hold_s: float = 120.0
    tempo_min_elapsed_s: float = 600.0
    tempo_change_sig: float = 0.25

    red_card_sig: float = 0.8

    shot_speed_record_kmh: float = 110.0
    sprint_record_kmh: float = 33.0
    speed_record_sig: float = 0.3

    elite_pass_difficulty: float = 7.5
    elite_pass_progressive_m: float = 15.0
    elite_pass_sig_min: float = 0.35
    elite_pass_sig_max: float = 0.5

    milestone_passes: int = 50
    milestone_distance_km: float = 10.0
    milestone_ht_accuracy: float = 95.0
    milestone_ht_min_passes: int = 30
    milestone_sig_passes: float = 0.2
    milestone_sig_distance: float = 0.25
    milestone_sig_accuracy: float = 0.3
    milestone_sig_brace: float = 0.4
    milestone_sig_hat_trick: float = 0.5

    momentum_shots: int = 3
    momentum_window_s: float = 300.0
    momentum_final_third_possessions: int = 4
    momentum_run_sig: float = 0.4

    half_time_sig: float = 0.5
    full_time_sig: float = 1.0


@dataclass(frozen=True)
class EngineParams:
    """Top-level engine parameters."""

    metrics: MetricParams = field(default_factory=MetricParams)
    ratings: RatingParams = field(default_factory=RatingParams)
    winprob: WinProbParams = field(default_factory=WinProbParams)
    moments: MomentParams = field(default_factory=MomentParams)
    # Publish MatchState at most once per this much sim time (plus on every moment / period change).
    state_interval_ms: int = 1000


DEFAULT_PARAMS = EngineParams()
