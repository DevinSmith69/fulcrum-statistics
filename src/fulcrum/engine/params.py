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
    phase_chaos_threshold: float = 65.0  # spec start 60; tuned on WS1 sims (see HANDOFFS)
    phase_control_diff: float = 20.0
    phase_persist_s: float = 120.0  # spec start 60
    # No phase classification until the 5-minute window has filled (avoids "control" after two passes).
    phase_min_elapsed_s: float = 300.0

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
    w_live: float = 0.3  # full weight of the recent xG rate (pre-match rate gets 1 − w)
    live_ramp_min: float = 30.0  # the live weight ramps in linearly from kick-off (no step at 15')
    xg_decay_min: float = 10.0  # τ of the exponential decay on recent xG (no hard window edge)
    xg_prior_min: float = 10.0  # pseudo-minutes at the pre-match rate the recent rate is shrunk towards
    xg_memory_min: float = 40.0  # shots older than this are dropped (weight e^-4 ≈ 0.02)
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

    global_cooldown_s: float = 60.0  # spec minimum 45
    same_type_cooldown_s: float = 180.0
    # Exempt from every cooldown (and still reset the global cooldown clock when they fire).
    fully_exempt: tuple[str, ...] = ("goal", "red_card", "half_time", "full_time")
    # Exempt from the 45 s global cooldown only (same-type cooldown still applies).
    global_exempt: tuple[str, ...] = ("big_chance",)
    # Per-type overrides of `same_type_cooldown_s` for the chattier rules.
    type_cooldowns_s: tuple[tuple[str, float], ...] = (
        ("phase_change", 420.0),
        ("momentum_run", 360.0),
        ("pressure_shift", 420.0),
    )

    goal_base: float = 0.6
    goal_wp_cap: float = 0.4

    big_chance_xg: float = 0.30
    big_chance_base: float = 0.3
    big_chance_per_xg: float = 0.5

    wp_swing_delta: float = 0.06
    wp_swing_window_s: float = 90.0
    wp_swing_sig_per_delta: float = 5.0
    # Only these events can *cause* a swing. In the §3 model WP moves on goals, reds (own moments) and
    # shot xG; everything else is clock drift or the 15' xG window rolling over — not a story.
    wp_swing_cause_types: tuple[str, ...] = ("shot",)

    pressure_flip_diff: float = 30.0  # spec start 25
    pressure_rise: float = 50.0  # spec start 30: one decent shot from a quiet spell adds ~30
    pressure_rise_window_s: float = 180.0
    pressure_surge_min_after: float = 75.0  # a surge must end at a genuinely high index
    pressure_sig_min: float = 0.4
    pressure_sig_max: float = 0.6

    phase_change_sig: float = 0.35
    phase_change_report_balanced: bool = False  # settling back to "balanced" is rarely a story

    tempo_change_pct: float = 0.35
    tempo_hold_s: float = 120.0
    tempo_min_elapsed_s: float = 600.0
    tempo_change_sig: float = 0.25

    red_card_sig: float = 0.8

    shot_speed_record_kmh: float = 110.0
    sprint_record_kmh: float = 33.0
    sprint_record_min_elapsed_s: float = 300.0  # opening-frame tracking speeds are noisy
    sprint_record_min_gain_kmh: float = 0.5  # must beat the previous match high by this much
    speed_record_sig: float = 0.3

    elite_pass_difficulty: float = 7.5
    elite_pass_progressive_m: float = 15.0
    elite_pass_sig_min: float = 0.35
    elite_pass_sig_max: float = 0.5

    milestone_passes: int = 50
    milestone_distance_km: float = 10.0
    # Most outfielders cover 10 km (sim calibration 9.5–12 km/90): only the first player to get there is news.
    milestone_distance_first_only: bool = True
    milestone_ht_accuracy: float = 95.0
    milestone_ht_min_passes: int = 30
    milestone_sig_passes: float = 0.2
    milestone_sig_distance: float = 0.25
    milestone_sig_accuracy: float = 0.3
    milestone_sig_brace: float = 0.4
    milestone_sig_hat_trick: float = 0.5

    momentum_shots: int = 4  # spec start 3
    momentum_window_s: float = 300.0
    momentum_final_third_possessions: int = 5  # spec start 4
    momentum_run_sig: float = 0.4

    half_time_sig: float = 0.5
    full_time_sig: float = 1.0


@dataclass(frozen=True)
class CoachParams:
    """MODEL_SPEC §4.1 — concession analysis (`coach_` facts on goal and big_chance moments)."""

    # Pitch thirds (MODEL_SPEC §1.1), in the frame of the team the zone is described for.
    own_third_x: float = 35.0
    final_third_x: float = FINAL_THIRD_X
    # Chance quality bands on shot xG: < low → "low-quality chance", < clear → "half-chance", else "clear chance"
    # (clear = the big_chance threshold, so every big_chance moment is a clear chance).
    chance_low_xg: float = 0.10
    chance_clear_xg: float = 0.30
    # Keeper label: an on-target shot with xG below this from beyond this distance → "low-probability finish".
    low_probability_finish_xg: float = 0.10
    low_probability_finish_min_m: float = 18.0
    # Tracking frame used for "defenders goal-side": the latest frame at or before the shot, at most this old,
    # whose ball is within this distance of the shot location (proves the frame shows the shot).
    frame_max_age_ms: int = 1500
    frame_max_ball_gap_m: float = 3.0


@dataclass(frozen=True)
class EngineParams:
    """Top-level engine parameters."""

    metrics: MetricParams = field(default_factory=MetricParams)
    ratings: RatingParams = field(default_factory=RatingParams)
    winprob: WinProbParams = field(default_factory=WinProbParams)
    moments: MomentParams = field(default_factory=MomentParams)
    coach: CoachParams = field(default_factory=CoachParams)
    # Publish MatchState at most once per this much sim time (plus on every moment / period change).
    state_interval_ms: int = 1000


DEFAULT_PARAMS = EngineParams()
