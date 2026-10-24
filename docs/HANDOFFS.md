# Cross-workstream requests
Format: `- [ ] (WSx → WSy) what you need, why, which contract/fixture you coded against`

## WS1 (simulator), delivered 2026-10-24
Fixtures: `data/matches/sample_seed7.jsonl` (NBA v RVR, match_id `nba-rvr-s7`) and
`data/matches/sample_seed11.jsonl` (HLC v MDU, `hlc-mdu-s11`). Each is JSONL `{"topic","data"}` with events and
1 Hz tracking, about 11 MB. Stream semantics are in `src/fulcrum/sim/README.md` ("Stream semantics").

- [ ] (WS1 → WS2) **API lifespan:** start a match with `from fulcrum.sim import run_match` and
  `asyncio.create_task(run_match(config))`. It paces by `config.speed`, publishes to `get_bus()` and returns
  `(score_home, score_away)`. It loads teams from `data/teams.json` (override with `FULCRUM_TEAMS_PATH`).
  Pass `speed=0` for as-fast-as-possible.
- [ ] (WS1 → WS2) **Don't double count:** a goal is a `shot` (outcome `goal`) followed by a `goal` event
  (`extra.shot_seq` points at the shot). Take xG, shot speed and distance from the shot. Shots and xG
  come from `shot` events only.
- [ ] (WS1 → WS2) **Match minute in P2** = `45 + (clock_ms − period_start(P2).clock_ms)/60000`. The clock
  is continuous, and P2 starts at 45:00 plus the actual first-half added time (`period_end.extra.added_time_ms`).
- [ ] (WS1 → WS2) **Turnovers:** the winning event (`interception`/`ball_recovery`/`tackle`/`clearance`/`save`)
  already carries the new `possession_id`. The next event is `possession_change`. Defensive events use the
  defender's frame.
- [ ] (WS1 → WS2/WS6) `make replay` writes `data/matches/seed7.jsonl` (gitignored). The committed fixtures
  are `sample_seed*.jsonl`. Regenerate them with
  `uv run python -m fulcrum.sim --home NBA --away RVR --seed 7 --out data/matches/sample_seed7.jsonl`
  (and HLC/MDU/11). I left the Makefile alone because it's shared.
- [ ] (WS1 → WS5) The container image needs `data/teams.json`, or set `FULCRUM_TEAMS_PATH`.

### WS1 decisions (made unattended; shout if you disagree)
- No contract changes. Extra info goes in `MatchEvent.extra` (`offside`, `cutback`, `fouled_player_id`,
  `defender_id`, goal → `shot_event_id/shot_seq/assist_player_id`, period_end → `stoppage_min/added_time_ms`).
- Deterministic ids and `ts` (see the README), so fixtures are byte-identical across runs. Live runs restamp `ts`.
- The spec's shot-choice weights produced about 55 shots and 10 xG per match. Tuned values (in-box cap, no long-shot
  base, stronger team-strength effect on shots) are listed in the README "Where the tuned values differ" section.
  Every §1.9 metric is in range over 200 seeds.
- Gaps the spec leaves open, filled as *ext* params: 20 % of failed passes go out for a throw-in; failed
  crosses go behind for a corner 55 % of the time and blocked shots 55 %; offside on 10 % of completed through
  balls; direct free-kick shots inside 30 m (xG × 0.6); fouls in the box ×0.3, giving about 0.25 penalties
  per match; booked players foul ×0.4 as often; subs at the next dead ball after a scheduled minute, taking
  off the lowest `mean rating − 4·km covered`.
- Assists: the shot must come within one carry of the reception. A dribble makes the goal the shooter's own work.
