# Cross-workstream requests
Format: `- [ ] (WSx → WSy) what you need, why, which contract/fixture you coded against`

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
