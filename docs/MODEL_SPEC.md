# MODEL_SPEC — simulator, metrics, win probability, moments

All parameters below are **starting values**. Put them in one `params.py` per module (dataclass), never
inline magic numbers, so we can tune quickly. Calibration targets are acceptance criteria.

Pitch 105 × 68 m. Event coordinates are team-relative (actor attacks → x = 105). Goal centre (105, 34),
posts at y = 30.34 / 37.66 (goal width 7.32 m).

---------------------------------------------------------------------------------------------------
## 1. Simulator (`fulcrum.sim`) — possession-chain model

A discrete-event loop over **sim time**. State: period, clock_ms, team in possession, ball carrier,
ball (x, y) in the possessing team's frame, possession_id, score, cards, on-pitch players.
Emission is paced to wall clock by `speed` (`speed=0` = as fast as possible, used for fixtures).

### 1.1 Action choice for the ball carrier (by zone, x in team frame)
| Zone | pass | carry | dribble | cross | shot | clearance |
|---|---|---|---|---|---|---|
| own third x < 35 | 0.78 | 0.15 | 0.02 | – | – | 0.05 (only if under pressure, else → pass) |
| middle 35 ≤ x < 70 | 0.72 | 0.18 | 0.07 | – | 0.03 (only if x > 60) | – |
| final third x ≥ 70 | 0.50 | 0.12 | 0.13 | 0.10 if wide (y < 14 or y > 54) else 0 | `clamp(0.04 + 1.8·xG_here, 0, 0.6)` | – |
Renormalise each row after applying conditions. Trailing team after 60' : +20 % relative weight on
forward passes and shots.

### 1.2 Pressure
Before each action, the nearest opponent distance `d` is drawn ~ `Gamma(k=2, θ=3)` metres, shifted
by the defending team's pressing intensity (`defence_rating`). If `d < 3`: emit a `pressure` event by
that opponent, set `under_pressure = True`.

### 1.3 Passes
Pick a recipient from on-pitch teammates using their **current tracking positions** (see 1.8), weight
∝ `exp(0.04·progressive_m) · openness`, excluding the passer. Compute
```
length_m, progressive_m, line_breaking (passes ≥ 2 opponent "lines" in the block model), into_box, is_cross
p_complete = sigmoid( 3.2 − 0.045·length − 0.04·max(progressive,0) − 0.9·under_pressure
                      − 0.8·line_breaking − 0.6·into_box − 0.7·is_cross
                      + 0.03·(passing − 70) − 0.02·(opp_defence − 70) )      clamp [0.20, 0.98]
difficulty = round(10·(1 − p_complete), 1)
```
Sanity: 15 m square pass, no pressure → p ≈ 0.93 (difficulty 0.7). 30 m line-breaking forward pass
(20 m progressive) under pressure → p ≈ 0.34 (difficulty 6.6).
Success → recipient becomes carrier at end point. Failure → `interception` (60 %) or opponent
`ball_recovery` at a point 40–90 % along the pass line → `possession_change`.
Ball speed (km/h): ground `N(45 + 0.6·length, 6)`, high `N(70, 10)`, clamp 20–110. Duration
`= length / (speed/3.6) + 0.6 s` reaction.

### 1.4 Carries & dribbles
Carry: move 4–15 m with forward bias, 2–4 s. Dribble: `p_success = sigmoid(0.05·(dribbling − opp_tackling) + 0.3)`;
fail → `tackle` (success) by the defender → possession change.

### 1.5 Shots
```
angle = atan2(7.32·dx, dx² + dy² − (7.32/2)²)   # dx = 105 − x, dy = |34 − y|; if denominator<0 add π
xG    = sigmoid(−0.4 − 0.10·distance + 1.5·angle)  ×0.6 if header ;  penalty xG = 0.76
        clamp [0.01, 0.95]
```
Sanity: 6 m central ≈ 0.66; penalty-spot open play ≈ 0.37; 20 m central ≈ 0.14; 25 m ≈ 0.08.
`big_chance = xG ≥ 0.30`. Through-ball / cutback recipients get +0.10 xG before clamp.
Outcome: blocked 15 %; else on-target with `p = 0.30 + 0.6·xG`;
`p_goal | on target = clamp(xG/(0.30+0.6·xG) · (1 + 0.01·(finishing−70)) · (1 − 0.008·(gk−70)), 0.02, 0.97)`;
woodwork 2 % of off-target. Saved → corner 30 %, keeper possession 70 %. Off target → goal kick.
Shot speed (km/h): foot `N(98, 12)` clamp 55–135 (+0.2 per finishing point above 70), header `N(55, 8)`.
Goal → `goal` event (in addition to the `shot` with outcome `goal`), score update, kick-off to conceding side.

### 1.6 Set pieces, fouls, cards, subs, time
- Fouls: hazard so each team commits ≈ 10–12 per match; 15 % of fouls → yellow; second yellow → red;
  straight red ≈ 0.08 per match. Foul in final third → `free_kick` with shot option.
- Corners: from saves/blocks/deflected crosses; target 4–6 per team. Corner → cross into box, 22 % produces a header shot.
- Subs: 3–5 per team between 55' and 85', tired/low-rated players first.
- Dead-ball gaps (sim time): throw-in 10–20 s, goal kick 15–25 s, corner 25–40 s, goal celebration 60–90 s.
- Stoppage: H1 1–4 min, H2 3–7 min (added at period end).

### 1.7 Team strength
`s = (attack_rating − opp.defence_rating)/100` feeds pass completion (+1.5·s logit), shot share in
final third (+30 %·s relative), and dribble success. Home advantage: +0.15 logit on pass completion.

### 1.8 Tracking frames (player speed & distance)
4-3-3 anchor positions (absolute, home attacking → 105 in P1). Each frame (1 Hz):
`x = anchor_x + 0.5·(ball_x − 52.5)`, `y = anchor_y + 0.3·(ball_y − 34)` + smooth noise (OU process,
σ ≈ 1.5 m); the carrier and the nearest 2 players of each team move towards the ball at up to
`pace`-dependent top speed (pace 90 → ~34 km/h, pace 60 → ~29 km/h). Speed = Δposition / Δt.
Calibration: outfield distance 9.5–12.0 km per 90, GK 4–6 km; per-player top speed 27–35 km/h.

### 1.9 Calibration targets (run 200 seeds, report mean ± sd; acceptance for WS1)
| Metric (per match, both teams) | Target |
|---|---|
| Events (excl. tracking) | 1,400 – 2,000 |
| Passes | 800 – 1,000; completion 78 – 86 % |
| Shots | 20 – 30; on target 30 – 40 % |
| Total xG | 2.4 – 3.2 · Goals 2.4 – 3.1 (mean) |
| Stronger side (NBA v HLC) wins | 50 – 60 % |
| Corners 8 – 12 · Fouls 18 – 26 · Yellows 2.5 – 4.5 |

---------------------------------------------------------------------------------------------------
## 2. Engine metrics (`fulcrum.engine`)

Rolling windows use **sim time** (clock_ms), not wall clock.

- **Possession %**: time-weighted by possession_id durations.
- **Pass accuracy / avg difficulty**: from `pass` events.
- **Key pass**: completed pass whose recipient shoots within the same possession.
- **Pressure index** (per team, 0–100): threat points with exponential decay, half-life 180 s:
  final-third entry 1 · box entry 2 · completed pass into box 1.5 · shot 2 + 10·xG · corner 1.5 ·
  recovery in opponent half 1.5. `index = 100·(1 − exp(−raw/8))`.
- **Control index** (per team, last 5 min): `100 · poss_share · completion · min(1, mean_passes_per_possession/8)`.
- **Chaos index** (match, last 5 min): `100 · (0.5·min(1, turnovers_per_min/2.5) + 0.3·share_possessions_<3_passes + 0.2·share_passes_>35m)`.
- **Phase** (with 60 s persistence hysteresis): chaos ≥ 60 → `chaos`; control_home − control_away ≥ 20 →
  `control_home`; ≤ −20 → `control_away`; else `balanced`.
- **Tempo**: passes per minute, rolling 5 min.
- **Momentum** (−100…100): EMA (α = 0.2 per event) of `pressure_home − pressure_away`.
- **Player rating** (3–10, start 6.0): goal +1.0 · assist +0.6 · key pass +0.2 · xG ×1.0 · shot on target +0.1 ·
  tackle won/interception +0.1 · completed pass +0.004·(1 + difficulty) · failed pass −0.03 ·
  dribble won +0.1 · yellow −0.3 · red −1.5 · own error leading to shot −0.3.
  Rating = 6 + 4·tanh(Σ/4) for Σ ≥ 0, 6 + 3·tanh(Σ/3) below (soft cap; a plain clamp let a no-goal winger hit 10.0).
- **Speed**: max shot speed per team/match; player top speed & distance from tracking frames.

## 3. Live win probability (`engine/winprob.py`)

Independent Poisson goals for the remaining time.
```
pre-match rate per 90:   λ_home = home.pre_match_goal_rate · (75/away.defence_rating) · 1.08
                         λ_away = away.pre_match_goal_rate · (75/home.defence_rating) · 0.95
per-minute:              r = λ/90
in-play:                 r_live = (1 − w)·r_pre + w·r_recent,  w = 0.3·min(1, minute/30)   (ramps in, no step)
recent xG rate:          r_recent = (Σ xG_i·e^(−age_i/10) + r_pre·10) / (10·(1 − e^(−elapsed/10)) + 10)
                         (exponential decay τ = 10 min instead of a hard 15-min window, shrunk towards r_pre
                          by 10 pseudo-minutes so one early big chance can't double the rate)
red card:                team down ×0.65, opponent ×1.25 (per red, multiplicative)
game state after 60':    trailing ×1.15, leading ×0.90
remaining minutes:       T = max(0, 90 + expected_stoppage(4) − minute_now)   (in H1 use 45-min halves sum)
goals_rest ~ Poisson(r_live·T) each team; P(H/D/A) by summing the joint grid to 10 goals each.
```
Sanity tests (must pass; reference values from the formulas above in brackets): even teams
(rate 1.35, defence 75) at kick-off → home 0.40–0.48 [0.417], draw 0.22–0.30 [0.248];
1-0 at 89' → leader ≥ 0.90 [0.927]; 0-0 goal at 80' → scorer ≥ 0.80 [0.829]; probabilities sum to 1 ± 1e-6.
Present it in UI as **"Match outcome probability"**, never as odds/prices — this is not a betting product.
Store a `WinProbPoint` per event for the WP chart.
*Changed 2026-10-24:* the original hard switch at 15' and the 15-minute window edge made the chart jump by
up to 34 points with nothing happening (blend switching on; a chance dropping out of the window). Every
step in the chart should now come from a shot, card or goal.

### 3.1 What-if counterfactuals (added 2026-10-24)
"What if that chance had gone in?" is answered by the same model, not a new one: at the instant of the shot
(after the shot is applied, so the same minute, recent-xG rates, red cards and remaining time) re-evaluate
with the shooting team's score + 1.
```
P_if = win_probability(…same inputs…, score_team = score_team + 1)      # MatchTracker.win_prob_if_scored(team)
```
Only the score changes, so the game-state factor after 60' follows the hypothetical score (a team that would
have led is treated as leading), exactly as it would have had the goal happened. Facts (shooting team = the
moment's `team`; percents rounded like every other `%` fact; the delta from the rounded values):

| key | label | example |
|---|---|---|
| `whatif_goal_score` | Score if it had gone in (`<home>` first) | `"2-1"` |
| `whatif_win_pct_now` | `<team>` win probability now | `24` |
| `whatif_goal_win_pct` | `<team>` win probability if it had gone in | `93` |
| `whatif_goal_win_delta` | Rise in `<team>` win probability if it had gone in (pp) | `69` |
| `whatif_goal_draw_pct` | Draw probability if it had gone in | `7` |

Attached to every **big_chance**, and to a **wp_swing** caused by the swinging team's own shot. Not added to
goals: "without this goal" (score − 1 at the goal) differs from `wp_scorer_before` (WP just before the shot)
only by the shot's own xG in the recent rate — 0–5 points on the fixtures — so it would be a confusing third
number rather than new information. Significance and triggers are unchanged.

## 4. Moment detection (`engine/moments.py`)

Deterministic rules → `Moment` with `significance` (0–1), `trigger` text, `wp_before/after` and a
**fact pack**. Cooldowns: non-goal moments ≥ 45 s sim time apart; same type ≥ 180 s apart (per team).
Target **20 – 35 moments per match**; if over, raise thresholds in params, don't drop goals.

| Type | Rule | Significance | Required facts (keys) |
|---|---|---|---|
| goal | every goal | 0.6 + min(0.4, largest \|Δ\| across P(home), P(draw), P(away)) | scorer, minute, xg, shot_speed_kmh, distance_m, wp_scorer_before, wp_scorer_after, score_after, assist (if any), pass_difficulty_of_assist |
| big_chance | shot xG ≥ 0.30 not scored | 0.3 + 0.5·xG | shooter, xg, outcome, distance_m, team_xg_total, whatif_* (§3.1) |
| wp_swing | \|ΔP(home) or ΔP(away)\| ≥ 0.06 within 90 s, not a goal | min(1, 5·\|Δ\|) | wp_*_before, wp_*_after, cause_event, minute, pressure_index_*; whatif_* (§3.1) when the swing is the shooting team's |
| pressure_shift | sign flip of (P_home − P_away) with \|diff\| ≥ 25, or +30 in 3 min | 0.4–0.6 | pressure_index_*_before/after, shots_last_5, box_entries_last_5 |
| phase_change | phase transition (persisted) | 0.35 | phase_before, phase_after (readable: "<team> in control", "chaotic", "balanced"), control_index_*, chaos_index, turnovers_last_5 |
| tempo_change | \|tempo − match avg\| > 35 % for ≥ 2 min | 0.25 | tempo_ppm, tempo_match_avg |
| red_card | red | 0.8 | player, minute, card, team_red_cards, players_left, wp_*_before/after |
| speed_record | match-high shot ≥ 110 km/h, or player first ≥ 33 km/h | 0.3 | player, speed_kmh, previous_match_high |
| elite_pass | completed, difficulty ≥ 7.5 and progressive ≥ 15 m (max 1 per 3 min) | 0.35–0.5 | passer, recipient, difficulty, expected_completion_pct, length_m, progressive_m, ball_speed_kmh |
| player_milestone | 50th completed pass, 2nd/3rd goal, 10 km, ≥ 95 % accuracy on ≥ 30 passes at HT | 0.2–0.5 | player, milestone, value |
| momentum_run | ≥ 3 shots in 5 min with 0 opponent shots, or ≥ 4 consecutive possessions reaching final third | 0.4 | team, shots_last_5, opp_shots_last_5, xg_last_5 |
| half_time / full_time | period end | 0.5 / 1.0 | score, xg_*, possession_*, wp_* , top 3 player ratings |

**Fact formatting** (so the fact-checker can match strings): percentages as integers 0–100 with unit
`%` (e.g. `wp_home_after = 71`), xG two decimals, speeds integers, distances one decimal, minutes as
`"67'"` strings. Agents quote facts exactly as formatted.
**Always include derived deltas** agents will want to say (e.g. `wp_home_delta = 22`, `pressure_home_delta`),
because agents are forbidden from computing numbers themselves. Every moment also carries `minute`, `score`
(e.g. `"2-1"`), `home_team`, `away_team`.

### 4.1 Concession analysis — `coach_` facts (added 2026-10-24)
The evidence behind the **Coach's view** ("how did we concede that?"), computed by `engine/concession.py` when
the shot is applied and attached to every **goal** and **big_chance** moment, written from the **defending**
team's side (the moment's `team` is the attacking side). Existing facts and moment thresholds are unchanged.
Parameters live in `params.CoachParams`. A fact that cannot be computed reliably is **omitted**, never guessed.

**The move.** `ConcessionTracker` follows possessions as `MatchTracker` defines them (a new possession starts on
an event of the other team that gains the ball, §2). The move starts at the start of the attacking team's
possession, or at the latest set piece (kick-off, goal kick, throw-in, corner, free kick) that team took inside
it — "the goal came from a corner" is the coach's unit, not a 3-minute possession. How it started
(`coach_move_origin`), in priority order:

| origin | rule |
|---|---|
| set piece (`free kick`, `corner`, `throw-in`, `goal kick`, `kick-off`) | the move starts with that restart; a free kick also names the defending player whose foul (the event right before it, after the last on-ball action) gave it away |
| `misplaced pass` | the defending team's last on-ball action before the regain was a failed pass; the facts add that pass's difficulty (0–10) and expected completion (%) from the pass event (§1.3) |
| `tackle` | regain by a tackle, or the defending team's last action was a failed dribble (then the dribbler is the player who lost the ball) |
| `interception` / `clearance` | regain event of that type (without a failed pass before it) |
| `loose ball after a clearance` / `goalkeeper save` / `loose ball after a blocked shot` | the defending team's last on-ball action was a clearance / a saved shot / a blocked shot |
| `loose-ball recovery` | a `ball_recovery` regain otherwise |
| — (no origin facts) | anything else, e.g. a bare `possession_change` |

The player "who won the ball" is only named when the regain event is an interception, tackle, ball recovery or
clearance (never e.g. the first pass of a possession).

**Pressure on the shooter.** `coach_nearest_defender_m` is the shot event's `nearest_opponent_m` — the
simulator's own pressure measurement (§1.2), absent on headers from crosses/corners, in which case the fact is
omitted. It is *not* taken from tracking frames: the two nearest players of each team chase the ball (§1.8), so
the frame distance is ~0 m on most shots and would be misleading. `coach_defenders_goal_side` comes from the
tracking frame: the latest frame at or before the shot, at most 1.5 s old, converted into the shooting team's
frame (home attacks towards x = 105 in odd periods); it is used only if its ball is within 3 m of the shot
location (proving it shows the shot), and counts the defending team's outfield players (goalkeepers excluded)
strictly closer to their goal line than the ball. No frame → no fact.

**Chance quality and the keeper.** Bands on the shot's xG: `< 0.10` → `low-quality chance`, `0.10–0.30` →
`half-chance`, `≥ 0.30` (the big-chance threshold) → `clear chance`. Keeper facts only when the shot was on
target; the only keeper label is `low-probability finish` for an on-target shot with xG `< 0.10` from
`> 18 m`. There is no "should have saved" fact: nothing in the data supports one.

| key | label (abridged) | example | when |
|---|---|---|---|
| `coach_defending_team` | Defending team | `"Marsh Dock United"` | always |
| `coach_move_origin` | How the attacking move started | `"misplaced pass"` | origin known |
| `coach_ball_won_by` | `<attacking team>` player who won the ball | `"Lindqvist"` | regain event |
| `coach_ball_lost_by` | `<defending team>` player who lost the ball | `"Gillow"` | misplaced pass / failed dribble |
| `coach_misplaced_pass_difficulty` | Difficulty of the misplaced pass (0-10) | `6.9` | misplaced pass |
| `coach_misplaced_pass_expected_completion_pct` | Expected completion of the misplaced pass (%) | `31` | misplaced pass |
| `coach_free_kick_foul_by` | `<defending team>` player whose foul gave away the free kick | `"Bellweather"` | free kick after a foul |
| `coach_move_start_minute` | Minute the attacking move started | `"65'"` | always (with a move) |
| `coach_move_start_zone` | Where the move started, seen from `<defending team>`: `own third` (x < 35), `middle third`, `final third` (x ≥ 70) | `"middle third"` | start has x |
| `coach_move_passes` | Completed passes from the start of the move to the shot | `4` | with a move |
| `coach_move_seconds` | Seconds from the start of the move to the shot (s) | `46` | with a move |
| `coach_nearest_defender_m` | Distance from the shooter to the nearest `<defending team>` player (m) | `6.1` | measured |
| `coach_defenders_goal_side` | `<defending team>` outfield players goal-side of the ball at the shot | `2` | tracking frame |
| `coach_chance_xg` | Expected goals of the chance conceded | `0.11` | always |
| `coach_shot_distance_m` | Shot distance (m) | `21.3` | always |
| `coach_chance_quality` | Chance quality band | `"half-chance"` | always |
| `coach_goalkeeper` | `<defending team>` goalkeeper | `"Quennell"` | on target, one keeper on the pitch |
| `coach_shot_on_target` | Shot on target | `"yes"` | on target |
| `coach_shot_speed_kmh` | Shot speed (km/h) | `96` | on target |
| `coach_keeper_shot_profile` | Shot profile for the goalkeeper | `"low-probability finish"` | rule above |

Examples are seed 11's 66' goal (`data/matches/sample_seed11.jsonl`). Formatting follows §4 (percent integers,
xG two decimals, metres one decimal, speeds integers, minutes as `"67'"`), so the fact-checker can match them.
