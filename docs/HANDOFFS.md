# Cross-workstream requests
Format: `- [ ] (WSx → WSy) what you need, why, which contract/fixture you coded against`

## WS2 (engine + API) — 2026-10-24

### Requests
- [ ] (WS2 → WS1) Put `home_team_id` / `away_team_id` in `match_start.extra`. In-process the engine gets the
  teams from `MatchConfig`, but the standalone engine Container App otherwise has to guess (it takes the
  first two teams it sees, in order). Coded against `MatchTracker._learn_teams` (`engine/state.py`).
- [ ] (WS1 → WS2 check) Once `data/matches/sample_seed7.jsonl` / `sample_seed11.jsonl` are committed,
  `tests/engine/test_moments.py::test_target_moment_count_on_sim_samples` turns on (20–35 moments, every goal
  reported). Checked against WS1's final samples (`95ae048`): sample_seed7 → 26 moments (2 goals),
  sample_seed11 → 30 moments (4 goals). If the sim's calibration changes, re-run `scripts/print_moments.py`
  and tune `engine/params.py`.
- [ ] (WS2 → WS3) `AgentService.translate / translate_cues / recap` only know about insights produced by the
  same process. That covers single-process dev mode. In Azure (separate Container Apps) the API's on-demand
  translation therefore serves English. Please add a stateless hook, e.g.
  `translate_insight(insight, moment, language)`, or a request topic. The API already passes everything
  through `api/translation.py`, so it is a one-line switch there.
- [ ] (WS2 → WS3, FYI) Done on WS2's side: every moment has `home_team` / `away_team` (team names), player
  facts use short names, HT/FT use `top_player_N` + `top_player_N_rating`. The API creates
  `AgentService(bus, get_llm())`, `await start()` on the first POST, calls `register_match(config)`, and uses
  the translate / cue / recap hooks. Fact labels contain only truthful numbers ("last 5 minutes", "(0-10)").
  Percentage-point deltas use unit `pp` (e.g. `wp_home_delta = 22`, unit `pp`).
- [ ] (WS2 → WS4) Real API vs the mock: `POST /api/matches` returns **201**. `hello.payload` also carries
  `status`. `update_prefs` re-sends `hello` (same as the mock). Errors are `bad_request` / `unknown_match`.
  `MatchState` percentages (`possession_pct`, `pass_accuracy`, indices) are 0–100. Server-side
  `player_tag` cues for `followed_player_id` arrive at most every 20 s of sim time, `anchor=top_right`,
  priority 2, English. The agents additionally emit moment-linked `player_tag` cues for `player_lens`.
  De-dupe by `seq` / ids: a `hello` and the live stream may overlap by one item.
- [ ] (WS2 → WS5) Entry points: `uvicorn fulcrum.api.app:app` (api) and `python -m fulcrum.engine` (engine).
  Consumer groups: engine = `engine` on `match-events` + `tracking-frames`; api = `api-feed` on `match-events`
  and `api` on `match-state`, `moments`, `insights`. The API needs WebSocket ingress. Set
  `FULCRUM_ALLOWED_ORIGINS` to the Static Web App origin. With `FULCRUM_BUS=eventhubs`, `POST /api/matches`
  only registers the match (`TODO(azure)` in `api/app.py`: trigger the sim app). Alternatively set
  `FULCRUM_INPROCESS=1` to run sim + engine + agents inside the API container (the cut-list fallback).

### Decisions made overnight (no one to ask — change them in `engine/params.py` if you disagree)
- **WP model:** MODEL_SPEC §3 exactly. The spec's reference values (0.417 / 0.248 at KO, 0.927 at 89',
  0.829 after an 80' goal) reproduce when each team's last-15' xG equals its pre-match rate, which is how
  the sanity tests are written. "Minute" is the football minute (P2 restarts at 45). In H1,
  T = (45 − m) + 45 + 4.
- **wp_swing fires only on shots.** In the §3 model WP moves on goals and reds (both have their own moments),
  on shot xG, and on "noise": the 15' xG window rolling over, and the in-play blend switching on at 15'.
  The noise produced swings "caused" by a random pass, so a swing compares WP now against WP just before
  each shot in the last 90 s (`wp_swing_cause_types`).
- **Cooldowns:** goal, red_card, half_time, full_time and brace/hat-trick milestones skip every cooldown.
  big_chance skips only the global one, so a big chance right after an elite pass is not lost. Same-type
  cooldown keys: per team, except elite_pass / phase_change (any team) and player_milestone (per player).
  Spec cooldowns are minimums; the tuned values are longer.
- **Tuned vs the spec's starting values** (on 5 WS1 seeds: 21/27/25/35/23 moments, all goals kept):
  global cooldown 45 → 60 s; same-type overrides phase_change 420 s, momentum_run 360 s, pressure_shift
  420 s; pressure surge +30 → +50 and it must end ≥ 75; pressure flip |diff| 25 → 30; chaos phase threshold
  60 → 65; phase persistence 60 → 120 s; momentum_run 3 → 4 shots, 4 → 5 final-third possessions.
- **Noise filters:** phase changes are reported only when *entering* chaos/control (settling to
  "balanced" is not a story). Only the first player to 10 km gets a milestone (most outfielders reach it).
  Sprint records need ≥ 5' played and a ≥ 0.5 km/h gain (frame-0 tracking speeds are noisy).
  Phase classification starts once the 5' window has filled.
- **MatchState publish:** at most once per 1 s of sim time, plus on every moment and period change. A
  tracking-driven moment can re-publish the same `seq` with a later `clock_ms`.
- **Hand-made fixture** `tests/fixtures/handmade_nba_rvr.jsonl` (built by `tests/fixtures/build_handmade.py`):
  goal, big chances, red card (second yellow), 14-pass sequence, a turnover spell, elite pass, 118 km/h
  shot, sprint burst. It gives 17 moments; the 20–35 band is asserted on real sim output only.
- **API:** persona fallback = casual (then anything) when a persona has no variant. A missing language is
  translated on demand, else English. The recap order is exact persona+language → lazily generated by the
  agents → persona/en → casual. `FULCRUM_REPLAY_FILE` forces a replay even when the sim exists.
