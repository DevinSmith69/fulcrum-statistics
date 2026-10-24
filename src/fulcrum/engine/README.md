# `fulcrum.engine` — Interpret stage (WS2)

Turns `MatchEvent` + `TrackingFrame` into live `MatchState` (≤ 1 Hz sim time) and `Moment`s with fact packs.
Pure, deterministic, synchronous core; the bus is only touched in `service.py`.

| File | What |
|---|---|
| `params.py` | **Every tunable** (MODEL_SPEC §2–4) as frozen dataclasses. Tune here, never inline. |
| `state.py` | `MatchTracker`: applies events/frames, idempotent on `seq` / `frame_id`; team/player stats, possession spells, rolling windows, ratings; `snapshot()` → `MatchState`. Each event returns `EventEffects` for the detector. |
| `metrics.py` | Pure helpers: decayed pressure, control/chaos indices, phase hysteresis, EMA momentum, rating formulas. |
| `winprob.py` | MODEL_SPEC §3 independent-Poisson outcome probability (pure functions). |
| `moments.py` | `MomentDetector`: §4 rules, cooldowns, fact packs with exact formatting + deltas. |
| `match.py` | `MatchEngine`: tracker + WP + detector behind `process_event` / `process_frame`, with the state throttle. |
| `service.py` | `EngineService`: bus pump (`match-events` + `tracking-frames` → `match-state` + `moments`), one engine per match. `python -m fulcrum.engine`. |
| `replay.py` | Read `{"topic","data"}` JSONL recordings and replay them through an engine. |
| `teams.py` | `load_teams()` from `data/teams.json` (or `FULCRUM_TEAMS_PATH`). |

## Usage
```python
from fulcrum.engine.match import MatchEngine

engine = MatchEngine(match_id, home_team, away_team)
out = engine.process_event(event)  # EngineOutput(state | None, moments, winprob point)
```
```bash
uv run python scripts/print_moments.py tests/fixtures/handmade_nba_rvr.jsonl          # timeline for tuning
uv run python scripts/print_moments.py data/matches/sample_seed7.jsonl --all-facts
```

## Conventions
- Windows are **sim time** (`clock_ms`). The football minute restarts at 45 in P2 whatever the first-half stoppage.
- `MatchState`: `possession_pct`, `pass_accuracy`, `pressure_index`, `control_index`, `chaos_index` are 0–100
  (one decimal); xG two decimals; `momentum` −100…100 (+ = home on top).
- Fact formatting (the fact-checker matches these): percentages are integers with unit `%`; deltas of
  percentages are integer percentage points with unit `pp`, computed from the **rounded** before/after values;
  xG two decimals; speeds integers; distances one decimal; minutes `"67'"` / `"45+2'"`; players by short name.
  Every moment carries `minute`, `score`, `home_team`, `away_team`.
- Home/away: `MatchConfig` (via `EngineService.register_match`) → `match_start.extra.home_team_id/away_team_id`
  → first two teams seen.

## Moment tuning (MODEL_SPEC §4 target 20–35 per match)
Tuned on five WS1 matches (seeds 3, 7, 11, 21, 42): 21 / 27 / 25 / 35 / 23 moments, every goal kept. The
values that differ from the spec's starting values are listed in `docs/HANDOFFS.md` (WS2 section).

## Win probability and goal significance (changed 2026-10-24, after watching a live match)
- The live xG blend ramps in over the first 30' and uses exponentially decayed xG (τ = 10 min, shrunk
  towards the pre-match rate) instead of switching on at 15' over a hard 15-minute window. The old version
  moved the chart up to 34 points with no event. Check a recording with
  `uv run python scripts/print_moments.py <file>`; every step in the chart should come from a shot, card or goal.
- Goal significance uses the largest swing across home/draw/away, so a stoppage-time equaliser ranks as a
  top moment (it used to rank 6th, behind a red card).

## What-if counterfactuals (MODEL_SPEC §3.1)
"Had it gone in" is computed, never guessed: `MatchTracker.win_prob_if_scored(team_id)` re-runs the live model
at the same instant with that team's score + 1 (`win_prob(extra_home=…, extra_away=…)`; nothing else changes).
Every `big_chance` (and a `wp_swing` caused by the swinging team's own shot) carries `whatif_goal_score`,
`whatif_win_pct_now`, `whatif_goal_win_pct`, `whatif_goal_win_delta` (pp, from the rounded values) and
`whatif_goal_draw_pct`, labelled with the team name. Example, hand-made fixture 86': NBA miss a 0.55 xG chance
at 1-1 — 24% to win now, 93% had it gone in (2-1). Goals get no what-if: "without this goal" is within a few
points of `wp_scorer_before`. Significance and triggers are unchanged. Tests: `tests/engine/test_whatif.py`.

## Concession analysis — `coach_` facts (MODEL_SPEC §4.1)
`concession.py` (`ConcessionTracker`, fed every event and frame by `MomentDetector`) adds the evidence for the
Coach's view to every `goal` and `big_chance`, from the defending side: how the move started (set piece,
misplaced pass with its difficulty and expected completion, tackle, interception, …), who won / lost the ball,
where and when it started, completed passes and seconds to the shot, the shooter's nearest-opponent distance
(from the shot event), defenders goal-side (from the tracking frame at the shot, only if the frame provably
shows the shot), chance quality band and keeper facts. Facts that can't be computed are omitted. Example, seed
11 66': `coach_move_origin = "misplaced pass"`, `coach_misplaced_pass_expected_completion_pct = 31`,
`coach_move_passes = 4`, `coach_move_seconds = 46`, `coach_nearest_defender_m = 6.1`,
`coach_defenders_goal_side = 2`, `coach_chance_quality = "half-chance"`. Tests: `tests/engine/test_concession.py`.
