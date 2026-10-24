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
