# `fulcrum.sim`: synthetic match simulator (WS1)

A seeded **possession-chain** simulator implementing [MODEL_SPEC §1](../../../docs/MODEL_SPEC.md). It emits
`MatchEvent`s on `match-events` and 1 Hz `TrackingFrame`s on `tracking-frames`, using the contract models
in `fulcrum.contracts`.

| File | Role |
|---|---|
| `params.py` | `SimParams`: every tunable, tagged *spec*, *tuned* or *ext* (fills a gap in the spec) |
| `pitch.py` | Geometry, shot angle, xG (§1.5) and pass completion/difficulty (§1.3); pure functions |
| `players.py` | `load_teams()`, 4-3-3 anchors and the vectorised tracking model (§1.8) |
| `engine.py` | `simulate()` / `MatchSimulator`: the possession-chain loop. Pure and seeded; no clock, I/O or bus |
| `runner.py` | `run_match()` paces to the wall clock by `speed` and publishes to `get_bus()`; `record_match()` writes JSONL |
| `__main__.py` | CLI |

## Usage
```bash
uv run python -m fulcrum.sim --home NBA --away RVR --seed 7 --speed 10            # publish to FULCRUM_BUS
uv run python -m fulcrum.sim --home NBA --away RVR --seed 7 --out data/matches/x.jsonl   # JSONL, no bus
#   --match-id ID (default nba-rvr-s7) · --no-tracking · --tracking-hz 1
uv run python scripts/calibrate_sim.py --seeds 200 [--markdown]                     # §1.9 table
```
```python
from fulcrum.sim import run_match, simulate, load_teams

await run_match(MatchConfig(home_team_id="NBA", away_team_id="RVR", seed=7), speed=10)  # → (home, away) score
for model in simulate(config, home_team, away_team):
    ...  # pure generator
```
JSONL records are `{"topic": "match-events" | "tracking-frames", "data": <model JSON, by_alias>}`. Pass
attributes therefore appear under `"pass"`, as on the bus.

## Live controls (demo director)
`control.py` — `MatchControl` is a small per-match pacing object that `run_match(..., control=ctl)` consults
before releasing each item: live speed (0.25–600×), pause / resume, and `skip_to_next(min_significance)` (run at
120× until `on_moment()` sees a moment that big, then drop back). It only decides *when* the next item is
released; the generator is never touched, so the stream stays identical per seed
(`tests/sim/test_sim_control.py`). The API wires it up in-process (`api/pipeline.py`, `POST
/api/matches/{id}/control`); the standalone sim service paces with `config.speed` only.

## Stream semantics (what consumers can rely on)
- **Determinism.** The same `MatchConfig` (incl. `seed` and `match_id`) gives a byte-identical stream.
  `event_id = md5(match_id:seq)`. `ts` is synthetic (`2026-10-24T15:00Z` + sim time, +15 min in P2). When
  paced (`speed > 0`) the runner restamps `ts` with the real publish time. `emit_tracking=False` drops the
  frames but leaves the event stream unchanged.
- **Order.** Events and frames come in non-decreasing `clock_ms`. Event `seq` runs 0, 1, 2, … with no gaps.
  Frame `frame_id` runs 0, 1, 2, … at `tracking_hz`. There are no frames during half-time.
- **Clock.** `clock_ms` is continuous. Period 2 starts at the P1 `period_end` clock (45:00 + actual added
  time). For the match minute in P2, use `45 + (clock_ms − p2_start_clock) / 60000`. `period_end.extra` carries
  `stoppage_min` (announced) and `added_time_ms` (actual).
- **Coordinates.** Event `x/y/end_x/end_y` are in the acting team's frame (attacks → x = 105). Defensive
  events (pressure, interception, tackle, foul, card, save) are in the defender's frame. Tracking is absolute
  (home attacks → 105 in P1).
- **Possession.** `possession_id` starts at 1 at the first kick-off and never decreases. Each turnover
  produces the winning event first (`interception` / `ball_recovery` / `tackle` / `clearance` / `save`),
  already carrying the new id, followed by `possession_change` (team = new owner). Restarts awarded to the
  other side (throw-in, goal kick, free kick, kick-off after a goal) emit `possession_change` before the restart event.
- **Passes.** `outcome` is `success`/`fail`. `pass.recipient_id` is set only for completed passes. `end_x/y`
  is the intended target. An offside pass is `fail` with `extra.offside = true`, followed by an `offside`
  event (by the receiver) and a `free_kick` for the defence. Cutbacks have `extra.cutback = true`. Crosses
  (open play and corners) have `is_cross`, `height = "high"`.
- **Shots & goals.** `shot.outcome` is one of `goal` / `saved` / `off_target` / `blocked` / `woodwork`. A goal is a
  `shot` (outcome `goal`, score still the old score) immediately followed by a `goal` event that carries the new score and
  `extra = {shot_event_id, shot_seq, assist_player_id}`. **Count shots and xG from `shot` events only.**
  Every event's `score_home/score_away` equals the goals so far. A save gives a `save` event by the keeper
  (then a corner, or the keeper's possession). `shot.situation` is `open_play`, `counter`, `set_piece`
  (direct free kick), `penalty` or `corner`.
- **Other.** `pressure` events precede the pressed action at the same clock (`under_pressure = true` and
  `nearest_opponent_m` on the action). `foul.extra.fouled_player_id`. A `card` follows its `foul`
  (`second_yellow` has outcome `red`, and the player leaves). `substitution.player_id` is the player coming on.
  Subs happen at dead balls between 55' and 85' (3–5 per team). `dribble.extra.defender_id`.

## Calibration (MODEL_SPEC §1.9)
`uv run python scripts/calibrate_sim.py --seeds 200 --markdown`: 200 seeds cycling all 12 ordered pairings
(seeds 1000–1199), plus 200 NBA v HLC with home and away alternating (seeds 5000–5199). Final table:

_Calibration: 200 seeds (league rotation) + 200 NBA v HLC_

| Metric (per match, both teams) | Mean ± sd | Target | |
|---|---|---|---|
| Events (excl. tracking) | 1891 ± 100 | 1400 – 2000 | ✅ |
| Passes | 853 ± 44 | 800 – 1000 | ✅ |
| Pass completion % | 82.5 ± 2.0 | 78.0 – 86.0 | ✅ |
| Shots | 23.4 ± 4.8 | 20.0 – 30.0 | ✅ |
| Shots on target % | 31.2 ± 9.6 | 30.0 – 40.0 | ✅ |
| Total xG | 2.86 ± 0.80 | 2.40 – 3.20 | ✅ |
| Goals | 2.53 ± 1.51 | 2.40 – 3.10 | ✅ |
| Stronger side (NBA v HLC) wins % | 57.5 ± 49.4 | 50.0 – 60.0 | ✅ |
| Corners | 10.4 ± 4.6 | 8.0 – 12.0 | ✅ |
| Fouls | 21.8 ± 4.2 | 18.0 – 26.0 | ✅ |
| Yellow cards | 2.99 ± 1.66 | 2.50 – 4.50 | ✅ |
| Outfield distance km/90 (per player) | 11.21 ± 0.41 | 9.50 – 12.00 | ✅ |
| GK distance km/90 | 4.44 ± 0.06 | 4.00 – 6.00 | ✅ |
| Outfield top speed km/h (per player) | 32.1 ± 0.4 | 27.0 – 35.0 | ✅ |
| Red cards (info) | 0.18 ± 0.43 | 0.00 – 1.00 | ✅ |
| Substitutions (info, both teams) | 8.1 ± 1.2 | 6.0 – 10.0 | ✅ |

NBA v HLC draws 25.5% · per-player top speed range 29.8–33.8 km/h

Shots on target % and the win rate have the least headroom. The win rate's sampling sd at n = 200 is about 3.5 pp,
so a 600-seed run was used as a check: NBA v HLC wins 55.5 % (draws 26.5 %), xG 2.94, goals 2.63, and every
metric stays in range. Tracking (§1.8) is reported for players who played the full match.

### Where the tuned values differ from the spec's starting values
The spec says its numbers are *starting values* to be tuned. Every change is in `params.py`:
- **Shot choice.** The spec's `0.04 + 1.8·xG` gave about 55 shots and xG ≈ 10 per match, because the ball sits
  in the final third more than real football allows. It is now `min(0.045, 0.65·xG_here)`: the cap
  (`fin_shot_max`) stops the shot rate climbing too high in the box, and the base 0 removes speculative 33 m shots.
  `mid_shot` is 0.015.
- **Recipient choice.** `exp(0.04·progressive)·openness` plus a length decay `exp(−0.11·length)`, without
  which passes averaged 25 m and completion was 74 %. Openness is `1 − exp(−d_opp/5 m)`.
- **Through ball** (for the +0.10 xG bonus) must split the opponent's back line and gain ≥ 15 m. The bonus
  applies to first-time shots only.
- **Carries and dribbles** stop at x = 91, so nobody walks the ball into the six-yard box. Fouls in the box are
  ×0.3 likely (≈ 0.25 penalties per match). A booked player fouls ×0.4 as often, keeping red cards near 0.2 per match.
- **Pressure** is `Gamma(2, 3) + 0.04·(84 − defence_rating)` (better defences press closer). Strength feeds
  dribble success with a 0.5 logit coefficient; the spec gives no number, and 1.5 made NBA v HLC ≈ 61 %.
- **Tracking.** The 4-3-3 anchors are compact, so that with the spec's 0.5 shift the back line holds 10–15 m from
  goal. The front three tuck 30 % inside when their team has the ball in the final third. OU τ = 12 s.
  Non-chasers move at ≤ 60 % of top speed.

Gaps the spec leaves open (*ext* in `params.py`; listed in `docs/HANDOFFS.md`): 20 % of failed passes go out for
a throw-in, failed crosses go behind for a corner 55 % of the time, blocked shots 55 %, offside on 10 % of
completed through balls, direct free-kick shots (xG × 0.6), clearance flight and second balls, keeper hold
time, free-kick and penalty dead time, who gets substituted (lowest `mean rating − 4·km covered`).

## Performance
A 90-minute match with tracking (about 1,900 events and 5,800 frames) generates in about 1.7 s on a laptop.
`tests/sim/test_sim_engine.py` asserts < 5 s. JSONL output with tracking is about 11 MB per match.
