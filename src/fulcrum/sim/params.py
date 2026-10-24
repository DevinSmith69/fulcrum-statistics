"""Every tunable of the simulator in one frozen dataclass (MODEL_SPEC §1).

Values marked "spec" are taken verbatim from MODEL_SPEC §1; values marked "tuned" were set by
`scripts/calibrate_sim.py` to hit the §1.9 calibration targets; values marked "ext" fill a gap the
spec leaves open (documented in docs/HANDOFFS.md). Never inline a magic number in the sim — add it here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SimParams:
    """All simulator tunables. Override with `dataclasses.replace(DEFAULT_PARAMS, field=value)`."""

    # --- §1.1 zones (team frame, metres) -------------------------------------------------- spec
    own_third_x: float = 35.0
    final_third_x: float = 70.0
    middle_shot_min_x: float = 60.0
    wide_y_low: float = 14.0
    wide_y_high: float = 54.0

    # --- §1.1 action weights per zone ------------------------------------------------------ spec
    own_pass: float = 0.78
    own_carry: float = 0.15
    own_dribble: float = 0.02
    own_clearance: float = 0.05  # only under pressure, else folded into pass
    mid_pass: float = 0.72
    mid_carry: float = 0.18
    mid_dribble: float = 0.07
    mid_shot: float = 0.015  # tuned (spec 0.03); only if x > middle_shot_min_x
    fin_pass: float = 0.50
    fin_carry: float = 0.12
    fin_dribble: float = 0.13
    fin_cross: float = 0.10  # only if wide
    fin_shot_base: float = 0.0  # tuned (spec 0.04 → ~10 % shots from 33 m)
    fin_shot_xg_coef: float = 0.65  # tuned (spec 1.8)
    fin_shot_max: float = 0.045  # tuned (spec 0.6): caps in-box shot propensity
    trailing_after_min: float = 60.0
    trailing_boost: float = 1.20  # relative weight on forward passes and shots

    # --- §1.2 pressure --------------------------------------------------------------------- spec
    pressure_gamma_k: float = 2.0
    pressure_gamma_theta: float = 3.0
    pressure_def_shift_per_point: float = 0.08  # ext: metres closer per defence point above pressure_def_ref
    pressure_def_ref: int = 80  # ext
    pressure_min_m: float = 0.3  # ext
    pressure_threshold_m: float = 3.0

    # --- §1.3 pass recipient choice -------------------------------------------------------- spec+ext
    recipient_prog_coef: float = 0.04  # spec: weight ∝ exp(0.04·progressive_m)
    recipient_length_decay: float = 0.11  # ext/tuned: × exp(-decay·length) so long balls stay rare
    recipient_open_scale_m: float = 5.0  # ext: openness = 1 − exp(−d_nearest_opp/scale)
    recipient_gk_weight: float = 0.15  # ext: keepers rarely receive
    restart_max_len_m: float = 25.0  # ext: throw-ins / kick-offs stay short
    through_min_prog_m: float = 15.0  # ext: line-breaking + this much progress = through ball
    through_lead_m: float = 3.0  # ext: through balls are played into space ahead of the runner
    cutback_min_x: float = 94.0  # ext: backward pass from the byline area into the box
    offside_prob: float = 0.10  # ext: share of completed through balls flagged offside

    # --- §1.3 pass completion (logit coefficients) ----------------------------------------- spec
    pc_intercept: float = 3.2
    pc_length: float = -0.045
    pc_progressive: float = -0.04
    pc_pressure: float = -0.9
    pc_line_breaking: float = -0.8
    pc_into_box: float = -0.6
    pc_cross: float = -0.7
    pc_passing: float = 0.03
    pc_opp_defence: float = -0.02
    pc_min: float = 0.20
    pc_max: float = 0.98

    # --- §1.3 failed pass ------------------------------------------------------------------ spec+ext
    pass_fail_out_prob: float = 0.20  # ext: share of failed passes that leave the pitch (throw-in)
    interception_share: float = 0.60  # spec: of the rest, interception vs ball_recovery
    fail_point_min: float = 0.40
    fail_point_max: float = 0.90

    # --- §1.3 ball speed & flight ---------------------------------------------------------- spec
    high_pass_min_len: float = 30.0  # ext
    high_pass_prob: float = 0.60  # ext
    ground_speed_base: float = 45.0
    ground_speed_per_m: float = 0.6
    ground_speed_sd: float = 6.0
    high_speed_mean: float = 70.0
    high_speed_sd: float = 10.0
    ball_speed_min: float = 20.0
    ball_speed_max: float = 110.0
    pass_reaction_s: float = 0.6

    # --- §1.4 carries & dribbles ----------------------------------------------------------- spec
    carry_min_m: float = 4.0
    carry_max_m: float = 15.0
    carry_angle_sd: float = 0.7  # ext: radians around "straight at goal"
    carry_max_x: float = 91.0  # ext/tuned: defenders close the space, no carrying past the penalty spot
    carry_min_s: float = 2.0
    carry_max_s: float = 4.0
    dribble_rating_coef: float = 0.05
    dribble_base: float = 0.3
    dribble_strength_coef: float = 1.5  # §1.7: strength feeds dribble success
    dribble_min_m: float = 3.0  # ext
    dribble_max_m: float = 8.0  # ext
    dribble_min_s: float = 1.5  # ext
    dribble_max_s: float = 3.0  # ext

    # --- §1.5 shots ------------------------------------------------------------------------ spec
    xg_intercept: float = -0.4
    xg_distance: float = -0.10
    xg_angle: float = 1.5
    xg_header_factor: float = 0.6
    xg_penalty: float = 0.76
    xg_min: float = 0.01
    xg_max: float = 0.95
    xg_assist_bonus: float = 0.10  # through-ball / cutback recipients (first-time shots only)
    assist_max_touches: int = 1  # ext: an assist survives at most one carry by the shooter
    big_chance_xg: float = 0.30
    shot_block_prob: float = 0.15
    on_target_base: float = 0.30
    on_target_xg: float = 0.6
    finishing_coef: float = 0.01
    keeper_coef: float = 0.008
    p_goal_min: float = 0.02
    p_goal_max: float = 0.97
    woodwork_prob: float = 0.02  # of off-target
    save_corner_prob: float = 0.30
    blocked_corner_prob: float = 0.55  # ext: blocked shot deflected behind
    loose_ball_defender_prob: float = 0.60  # ext: who wins a loose ball after a block
    shot_speed_foot_mean: float = 98.0
    shot_speed_foot_sd: float = 12.0
    shot_speed_foot_min: float = 55.0
    shot_speed_foot_max: float = 135.0
    shot_speed_finishing_coef: float = 0.2
    shot_speed_head_mean: float = 55.0
    shot_speed_head_sd: float = 8.0
    counter_max_s: float = 12.0  # ext: shot within this long of a win in own half = counter
    counter_start_max_x: float = 50.0  # ext

    # --- §1.6 set pieces, fouls, cards ---------------------------------------------------- spec+tuned
    foul_prob: float = 0.0125  # tuned: per carrier action
    foul_pressure_mult: float = 1.8  # ext
    foul_dribble_prob: float = 0.10  # ext: a dribble attempt ends in a foul
    box_foul_factor: float = 0.3  # ext/tuned: defenders rarely risk a foul in the box (≈ 0.25 penalties / match)
    yellow_prob: float = 0.15
    booked_foul_factor: float = 0.4  # ext: a booked player is more careful
    straight_red_prob: float = 0.0036  # ≈ 0.08 per match at ~22 fouls
    free_kick_shot_max_dist: float = 30.0  # ext
    free_kick_shot_max_dy: float = 16.0  # ext
    free_kick_shot_prob: float = 0.45  # ext
    free_kick_xg_factor: float = 0.6  # ext: wall + keeper set
    corner_header_prob: float = 0.22  # spec
    cross_target_x: tuple[float, float] = (88.0, 99.0)  # ext: where crosses land (team frame)
    cross_target_y: tuple[float, float] = (24.0, 44.0)  # ext
    cross_shot_prob: float = 0.15  # ext: completed open-play cross → immediate shot
    cross_header_prob: float = 0.70  # ext: that shot is a header
    cross_fail_corner_prob: float = 0.55  # ext/tuned: failed cross deflected behind
    clearance_min_m: float = 25.0  # ext
    clearance_max_m: float = 50.0  # ext
    clearance_out_prob: float = 0.35  # ext
    clearance_keep_prob: float = 0.40  # ext: clearing team wins the second ball

    # --- §1.6 subs & time ----------------------------------------------------------------- spec
    subs_min: int = 3
    subs_max: int = 5
    subs_window_start_min: float = 55.0
    subs_window_end_min: float = 85.0
    sub_fatigue_per_km: float = 4.0  # ext: rating points lost per km covered when picking who goes off
    gap_throw_in: tuple[float, float] = (10.0, 20.0)
    gap_goal_kick: tuple[float, float] = (15.0, 25.0)
    gap_corner: tuple[float, float] = (25.0, 40.0)
    gap_goal: tuple[float, float] = (60.0, 90.0)
    gap_free_kick: tuple[float, float] = (15.0, 30.0)  # ext
    gap_penalty: tuple[float, float] = (45.0, 75.0)  # ext
    gap_keeper_hold: tuple[float, float] = (4.0, 10.0)  # ext
    gap_substitution: float = 15.0  # ext: extra dead time per substitution window
    stoppage_h1_min: tuple[int, int] = (1, 4)
    stoppage_h2_min: tuple[int, int] = (3, 7)
    touch_min_s: float = 0.6  # tuned: time on the ball before each action
    touch_max_s: float = 2.9  # tuned
    touch_pressure_factor: float = 0.6  # ext: under pressure you act quicker
    shot_flight_s: float = 1.0  # ext

    # --- §1.7 team strength --------------------------------------------------------------- spec
    strength_pass_logit: float = 1.5
    strength_shot_rel: float = 0.6  # tuned (spec 0.30): NBA v HLC needed a clearer edge
    home_pass_logit: float = 0.15

    # --- §1.8 tracking -------------------------------------------------------------------- spec+tuned
    tracking_shift_x: float = 0.5
    tracking_shift_y: float = 0.3
    tracking_gk_shift_x: float = 0.15  # ext: keepers don't follow the ball upfield
    tracking_gk_shift_y: float = 0.1  # ext
    tracking_front_line_x: float = 60.0  # ext: anchors at/after this x are the front three
    tracking_narrow_from_x: float = 70.0  # ext: ball in the final third (team frame) ...
    tracking_attack_narrowing: float = 0.3  # ext: ... pulls the front three this share towards the centre
    tracking_possession_offset_m: float = 5.0  # ext: attacking side steps up, defending side drops
    tracking_noise_sd_m: float = 1.5  # spec: OU process σ
    tracking_noise_tau_s: float = 12.0  # tuned
    tracking_gk_noise_factor: float = 0.8  # ext
    tracking_chasers_per_team: int = 2
    tracking_jog_factor: float = 0.6  # ext: non-chasers move at most this share of top speed
    top_speed_base_kmh: float = 29.0  # pace 60
    top_speed_per_pace_kmh: float = 5.0 / 30.0  # pace 90 → 34 km/h
    top_speed_min_kmh: float = 27.0
    top_speed_max_kmh: float = 35.0


DEFAULT_PARAMS = SimParams()
