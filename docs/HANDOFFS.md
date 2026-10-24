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
- **Known gaps (review, not fixed):** (a) the WS `hello` snapshot is built when it is delivered, so it can
  overlap the live stream by a few items. Clients de-dupe by `seq` / ids, as documented. (b) No topic carries
  the engine's per-event `WinProbPoint`s, so the API's WP series is rebuilt from `MatchState` (≤ 1 Hz sim,
  plus every moment). That is fine for the chart; a `winprob` topic would be an additive contract change if
  ever needed.
- **Replay re-labelling:** a replay re-labels the recording's teams to the POSTed `home_team_id` /
  `away_team_id` (team ids and `TEAM-NN` player ids). The recording's home side comes from
  `match_start.extra`, else from P1 tracking positions.

## WS3a (agents, offline) — 2026-10-24
- [ ] (WS3 → WS2) Every `Moment` should carry `home_team` / `away_team` facts (team name, or team_id; the agents
  resolve by id, name or short_name, and `home_team_id` / `away_team_id` also work). Better still, call
  `AgentService.register_match(config)` when `POST /api/matches` starts a match. A `match_start` event whose
  `extra` holds `home_team_id` / `away_team_id` also works. Until the teams are known, the agents hold the
  match's moments (up to 50) and process them as soon as the teams arrive. Coded against MODEL_SPEC §4 "Every moment also carries minute, score,
  home_team, away_team".
- [ ] (WS3 → WS2) Put player **short names** (e.g. `"Brennick"`) in player facts (`scorer`, `shooter`, `player`,
  `passer`, `recipient`, `assist`). A player_id still works because templates map ids to short names, but the
  LLM will quote ids literally.
- [ ] (WS3 → WS2) Half-time and full-time "top 3 player ratings" facts: the templates read `top_player_1` (name)
  and `top_player_1_rating` (also `_2`, `_3`). Please use these keys or tell WS3.
- [ ] (WS3 → WS2) Fact labels count as fact-pack text: numbers in a label (e.g. "Shots in last 5 min") may be
  quoted. Keep labels truthful, and don't put numbers in labels that you don't want quoted.
- [ ] (WS3 → WS2) API integration, single process: create one `fulcrum.agents.AgentService(bus, get_llm())` in
  the lifespan and `await svc.start()` it. On-demand hooks:
  `await svc.translate(insight_id, language)` → `InsightVariant[]` (cached; English on failure; `[]` if the id
  is unknown), `await svc.translate_cues(insight_id, language)` → `OverlayCue[]`,
  `await svc.recap(match_id, persona, language)` → `Recap | None` (None before full time).
  The casual/en recap is published on `insights` right after the full-time insight.
- [ ] (WS3 → WS2/WS4) Per moment, `insights` carries the Insight first, then its cues: one per English variant
  per cue spec (e.g. goal = 4 × lower_third + 4 × wp_swing), plus `player_tag` cues (persona `player_lens`,
  `perspective_player_id` = the player) for each id in `moment.player_ids`. A player-lens viewer filters
  on `perspective_player_id`. The agents do not produce player_lens *variants* yet (Player Lens is on the cut list).
- [ ] (WS3 → WS5) The agents also consume `match-events` with consumer group `agents` (recent sequence +
  score for prompts). Please create that consumer group on the Event Hub, or set `FULCRUM_AGENTS_EVENTS=0`.
  The agents then rely on the `score` fact and send no recent sequence.
- [ ] (WS3a → WS3b) Port `orchestrator.py` to an Agent Framework Workflow: the steps become executors,
  `EDGES` become `add_edge(..., condition)`, and `MomentContext` becomes shared state. Set
  `Insight.agent_trace_id` (currently None, `TODO(azure)`). The §3 analyst tools (`get_wp_history`,
  `get_team_trend`) are not built; the facts and recent sequence are passed inline instead.
  `PromptSpec.temperature` holds the §2 temperatures for the real client.

### Decisions made overnight (WS3a)
- **Retry policy:** a fact-check failure gets one repair, with the issues appended ("Fix these issues…").
  An LLM error uses up that single retry, so `fail_times=2` ends in FALLBACK and `fail_times=1` ends in
  REPAIRED. A timeout (6 s per call, 15 s per run) skips the retry and falls back immediately to protect
  latency. A crash inside a step also falls back.
- **Repair scope:** if only variants failed, only the Persona Editor re-runs. If the explanation failed, the
  Analyst and the Editor both re-run.
- **Number matching is numeric, not string:** `0.30` matches a fact stored as `0.3`, `0,34` matches `0.34`,
  and signs are ignored. "11,240" is read as one number. Numbers inside string facts and fact labels are
  allowed. Both the "minute being played" and "completed minutes" readings of the moment clock are allowed.
- **Scorelines get no free pass**, which deviates from §5.1. Every digit in "3-2" must be a fact-pack number.
  The §5.1 allowance (any 0–3 scoreline) would let a wrong score through, and the real score and the
  previous score digits are normally in the pack anyway.
- **Unknown-name rule:** we check against a denylist (real clubs and competitions, any case, hyphen- and
  NBSP-tolerant) plus league names not in this match (any case, including a foreign team's first word,
  e.g. "Harlow"). We do not flag arbitrary capitalised words, which would false-positive on sentence starts.
- **Banned terms** are checked in English plus the copy's own language. German "Quote" and French "cote"
  are not banned, because both also mean an ordinary rate or rating.
- **Template fallback is self-verifying:** each candidate template is fact-checked and skipped on failure,
  ending in slot-free copy. So a bad string fact (e.g. a `cause_event` naming a foreign player) can never
  reach FALLBACK copy.
- **REPAIRED** also covers "attempt 1 hit an LLM error, attempt 2 passed". `check_notes` says which happened.
- **Cue choices the contract leaves open:** big_chance → stat_card(3); red_card → lower_third(4) +
  wp_swing(3); tempo_change → pressure_bar(2); half_time → recap_card(4); player_tag anchored top-left.
  `wp_swing` cue headlines are a fixed localised label ("Win probability · Team"), and the body is the
  variant's commentary line.
- **Translations** that fail checks twice return the English variants and are not cached, so a later
  request can retry. Recap fallbacks are not cached either. Translations are checked against the same score
  the English copy used.
- **Fake LLM in dev:** `get_llm()` returns an empty `FakeLLM`, so `make dev` shows template copy marked
  `fallback` for every moment. That is intended; real copy arrives with WS3b.

## WS4 (web) — 2026-10-24

- [ ] (WS4 → WS2) Add `period_start_clock_ms: {"1": 0, "2": <clock at H2 kick-off>}` to the WS `hello` payload.
  The scoreboard clock and minute labels need it to show 46:00 (not 47:00) after first-half stoppage. The web app
  falls back to `period_start` events and then to 45:00, so this is cosmetic, not blocking. The mock sends it.
  Coded against: API_CONTRACT §3 `hello` + `web/mock/server.mjs`.
- [ ] (WS4 → WS2) On `update_prefs`, please re-send a `hello` snapshot (with insights re-filtered for the new
  persona/language). The web app treats `hello` as "replace the feed", so the moment list switches language and
  persona immediately; without it only *new* insights change. The mock does this.
- [ ] (WS4 → WS2) Confirm units for `TeamLiveStats.pass_accuracy`, `possession_pct` and `PlayerLiveStats.pass_accuracy`
  (0–1 or 0–100?). The web app accepts either (`as100()` in `web/src/lib/format.ts`); the mock sends 0–100.
- [ ] (WS4 → WS2/WS3) `OverlayCue.show_at_clock_ms`: the web app shows a cue once the latest match clock is ≥ this
  value, and shows late cues immediately. At 10× sim speed agent latency is ~30 s of match clock, so the mock sets
  `show_at = moment clock + latency × speed`, rounded up to the next second. Please do the same (or tell WS4 if you
  intend wall-clock latency only).
- [ ] (WS4 → WS3) `OverlayCue.duration_ms` is applied in **wall-clock** time by the renderer (a 6 s card stays 6 s
  on screen even at 10× speed). Keep durations in the 4–15 s range per API_CONTRACT §4.
- [ ] (WS4 → WS3) Cue mapping beyond the API_CONTRACT §4 list, as implemented in the mock (`web/mock/text.mjs`
  `buildCues`) — please match or tell WS4: `big_chance` → `stat_card` p3 bottom_right; `red_card` → `lower_third`
  p4 + `wp_swing` p4 (for the opponent); `tempo_change` → `pressure_bar`; `half_time` → `stat_card` p3.
  Anchors used by the web overlay: `lower_third`/`recap_card` bottom_center, `wp_swing` top_right,
  `pressure_bar` top_left, `stat_card` bottom_right, `player_tag` bottom_left.
- [ ] (WS4 → WS2) `wp_swing` copy wants to say "inside 90 seconds"; please include the window in the fact pack
  (the mock uses `window_s = 90`, unit `s`) so the fact-checker allows the number.
- [ ] (WS4 → WS5) Static Web App build: `cd web && npm ci && npm run build`, output `web/dist`, env
  `VITE_API_URL=https://<api-container-app>` at build time. WebSocket path is `/ws` on the same host.
- [ ] (WS4 → WS6) Recording tips: `npm run mock -- --from 82` lands just before the winning goal; `#/split` with
  Analyst·EN v Casual·ES is the money shot; `#/broadcast` then `I` shows the cue inspector.

## WS4 decisions (made overnight, unattended)
- Hash routing (`#/`, `#/split`, `#/broadcast`, `#/recap`), no router dependency. Static Web Apps needs no rewrite rules.
- Studio, broadcast and recap share one subscription; split view opens two of its own (the brief's "two independent
  WS subscriptions").
- Player positions are inferred client-side from events (4-3-3 anchors around the ball; out-of-possession side drops
  into a block). Tracking frames are not on the WS protocol; if WS2 adds them later the pitch can use them directly.
- Prefs persist per view in `localStorage` (`fulcrum.prefs.*`). Default studio viewer: Casual · EN following NBA-10.
- Team colours are used as data only (kit stripes, dots, chart series via OKLCH re-derivation). The single UI accent
  is signal cyan `#3DE0FF`. Overlays have no backdrop blur: it forced a GPU blur over the animating canvas every frame.
