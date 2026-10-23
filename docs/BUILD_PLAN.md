# BUILD_PLAN — Fulcrum (deadline-driven)

**Package to organisers by Mon 26 Oct 18:00 UK** (they upload for us and reply about once a day). Tuesday is buffer only.

## Timeline (UK time)
| When | Devin | Claude Code agents |
|---|---|---|
| **Fri night** | Azure setup (AZURE_SETUP.md) · email organisers about the exact close time and upload format · push repo to GitHub (private until Monday) · kick off overnight agents | **WS1 sim**, **WS2 engine+api**, **WS3a agents-offline**, **WS4 web-on-mock** in parallel git worktrees |
| **Sat AM** | Review and merge WS1→WS2→WS3a→WS4. Run `make dev` end to end with fake LLM | Fix-up agents on integration bugs |
| **Sat PM** | **WS3b Foundry**: real models, Agent Framework workflow, tracing. Tune moment thresholds by watching 2–3 matches | **WS5 infra** (azd/Bicep, Event Hubs bus) · WS4 wires to the real API |
| **Sat night** | Short review | WS4 polish (split view, cue inspector) · WS5 finish · test hardening |
| **Sun** | Deploy to Azure · live run · `eval_live.py` numbers · fix | **WS6 docs**: README, architecture diagram, "path to production" |
| **Sun night** | Draft video script and pitch | Docs and final test sweep |
| **Mon AM** | Record the < 2-min video, upload to YouTube (unlisted is fine if public URL works), finalise text, make repo public | – |
| **Mon 18:00** | **Send the package to organisers** | – |

**Cut list, in order, if behind:** LLM reviewer → Player Lens → recap per persona (keep casual only) → translations
beyond Spanish → tracking-based speed/distance (keep shot and ball speed) → Event Hubs (run memory bus in one
Container App; keep Event Hubs in the diagram as "production topology") ← cut this last, it's a hero tech.

**Never cut:** moment detection + WP model, Analyst + Editor + Fact-Checker loop, overlay UI with persona switch,
Foundry, Container Apps deployment, demo video.

## Running agents in parallel
```bash
git worktree add ../fulcrum-ws1 -b ws1-sim
git worktree add ../fulcrum-ws2 -b ws2-engine
git worktree add ../fulcrum-ws3 -b ws3-agents
git worktree add ../fulcrum-ws4 -b ws4-web
# in each: claude  (paste the brief below; for unattended runs use your usual permission settings)
```
Every brief starts with: *"Read CLAUDE.md, docs/API_CONTRACT.md, docs/MODEL_SPEC.md, docs/AI_PROMPT_DESIGN.md first."*

---------------------------------------------------------------------------------------------------
## WS1 — Simulator (`src/fulcrum/sim/`) · overnight Fri
**Brief (paste):**
> Read CLAUDE.md, docs/API_CONTRACT.md and docs/MODEL_SPEC.md §1. Implement the synthetic match simulator in
> `src/fulcrum/sim/` exactly per MODEL_SPEC §1, emitting `MatchEvent` and `TrackingFrame` from
> `fulcrum.contracts` onto the bus via `fulcrum.bus.get_bus()` (topics `match-events`, `tracking-frames`).
> Structure: `params.py` (all tunables, a dataclass), `pitch.py` (geometry, xG, angles), `players.py`
> (formation anchors, tracking positions), `engine.py` (the possession-chain loop, pure and seeded and
> independent of time and bus — it yields models), `runner.py` (paces to wall clock by `speed`, publishes
> to the bus), and `__main__.py` (CLI: `--home --away --seed --speed --out file.jsonl`; `--out` writes
> JSONL of `{"topic","data"}` and skips the bus).
> Load teams from `data/teams.json`. Same seed must give an identical stream. Write tests:
> determinism, contract validity of every emitted model, seq strictly increasing, coordinates in bounds,
> score consistency with goal events. Write `scripts/calibrate_sim.py` that runs 200 seeds at speed 0 and prints
> the MODEL_SPEC §1.9 table (mean ± sd vs target). Tune `params.py` until every metric is inside target, and
> paste the final table into `src/fulcrum/sim/README.md`. Then generate fixtures:
> `data/matches/sample_seed7.jsonl` (NBA v RVR) and `sample_seed11.jsonl` (HLC v MDU). Commit to branch `ws1-sim`.

**Acceptance:** calibration table in range · `make test` green · 90-minute match at speed 0 in < 5 s · fixtures committed.

## WS2 — Engine + API (`src/fulcrum/engine/`, `src/fulcrum/api/`) · overnight Fri
**Brief (paste):**
> Read CLAUDE.md, docs/API_CONTRACT.md and docs/MODEL_SPEC.md §2–4. Build the match engine in
> `src/fulcrum/engine/`: `state.py` (applies `MatchEvent`/`TrackingFrame` to an in-memory `MatchState`,
> idempotent on seq), `metrics.py` (pressure/control/chaos/tempo/momentum/player ratings with sim-time
> rolling windows), `winprob.py` (MODEL_SPEC §3 exactly, plus the sanity tests listed there),
> `moments.py` (MODEL_SPEC §4 rules, cooldowns, fact packs with exact formatting and deltas), `params.py`,
> and `service.py` (subscribes to `match-events` + `tracking-frames`, publishes `MatchState` ≤ 1 Hz sim time
> and `Moment`s). WS1 is being built in parallel: until `data/matches/sample_seed7.jsonl` exists, write your
> own small hand-made fixture in `tests/fixtures/` that conforms to the contracts (include a goal, a big
> chance, a red card, a long pass sequence and turnovers).
> Then build the FastAPI gateway in `src/fulcrum/api/` per API_CONTRACT §2–3: REST endpoints, `/ws`
> WebSocket with subscribe/prefs, per-match read models (ring buffers), server-side persona/language
> filtering (translation is a call into `fulcrum.agents.translate` — stub it to return the English variant
> until WS3 lands), and the lifespan for single-process dev mode (`FULCRUM_BUS=memory`) which starts sim +
> engine + agents tasks when `POST /api/matches` is called. Import sim and agents lazily, and guard with
> try/except so the API runs even if those packages are stubs. Tests: winprob sanity, moment rules on the
> fixture (count by type, cooldowns), a `MatchState` invariants test, API tests with httpx/TestClient
> including a WebSocket subscribe flow. Write `scripts/print_moments.py <fixture.jsonl>` that prints a
> timeline of moments (minute, type, significance, key facts) so a human can tune thresholds.
> Commit to `ws2-engine`.

**Acceptance:** WP sanity tests pass · 20–35 moments on both sample fixtures (once WS1 has delivered) · WS flow test green.

## WS3a — Agents, offline (`src/fulcrum/agents/`) · overnight Fri
**Brief (paste):**
> Read CLAUDE.md, docs/AI_PROMPT_DESIGN.md (all of it) and docs/API_CONTRACT.md. Build the agent layer
> against `fulcrum.llm.LLMClient` using only `FakeLLM` tonight. No Azure calls. Files: `schemas.py`
> (EditorOutput, ReviewOutput, MomentContext shared state), `prompts.py` (the prompts from
> AI_PROMPT_DESIGN §4, as versioned constants), `context.py` (builds the user prompt: facts table, roster,
> recent sequence, score), `factcheck.py` (§5, deterministic, heavily unit-tested including translation
> decimal commas and denylisted real-club names), `denylist.py`, `templates.py` (§6, one per MomentType ×
> persona; a test proves every template passes the fact-checker on synthetic facts), `cues.py` (Insight →
> OverlayCue[] per API_CONTRACT §4), `orchestrator.py` (the explain_moment flow: Analyst → Editor →
> FactCheck → repair once → fallback → cues; per-match ordered worker; timeouts), `translate.py` (on-demand
> with cache), `recap.py`, and `service.py` (subscribes to `moments`, publishes envelopes to `insights`).
> Structure the orchestrator so its steps map 1:1 onto Microsoft Agent Framework workflow executors
> later, but do NOT import agent-framework tonight. Tests: golden flow with canned FakeLLM output; repair
> path (first output has an invented number); fallback path (`fail_times=2`); timeouts; cue mapping.
> Commit to `ws3-agents`.

**Acceptance:** all three paths (PASSED/REPAIRED/FALLBACK) tested · every template validates · no network in tests.

## WS3b — Foundry + Agent Framework · Sat PM (Devin-driven)
> Implement `src/fulcrum/llm/foundry.py` (`FoundryLLM.from_env()` using `FOUNDRY_PROJECT_ENDPOINT` +
> `DefaultAzureCredential`) and port the orchestrator to a Microsoft Agent Framework Workflow per
> AI_PROMPT_DESIGN §2. First read the installed agent-framework version's README and Python samples and
> follow them exactly. Enable OTel tracing to App Insights. Add `scripts/eval_live.py` (§7). Keep `FakeLLM`
> tests green.

## WS4 — Web overlay (`web/`) · overnight Fri, wired Sat
**Brief (paste):**
> Read CLAUDE.md and docs/API_CONTRACT.md. Build the Fulcrum web app in `web/` (Vite + React + TypeScript
> + Tailwind; recharts for charts; no UI kit beyond that). Generate TS types from `../schemas/*.json` with
> json-schema-to-typescript (`npm run types`). Until the real API is up, write `web/mock/server.mjs`: a
> Node WebSocket server on :8000 `/ws` that speaks API_CONTRACT §3 and replays a believable synthetic match
> with canned moments, insights for all personas, and cues, at 10× speed.
> Screens:
> (1) **Match Studio**: top scoreboard bar (fictional team names and colours, clock). A 2D top-down pitch
>     canvas animating ball and player dots from events/state. Overlay layer anchored on the pitch rendering
>     `OverlayCue`s by template (lower_third, wp_swing, stat_card, player_tag, pressure_bar, recap_card) with
>     priority pre-emption per API_CONTRACT §4 and smooth enter/exit animations. Right rail: live match
>     outcome probability line chart (home/draw/away), momentum bar, pressure/control gauges, and an insight
>     feed showing "analysing…" when a moment arrives and before its insight does, with a small
>     PASSED/REPAIRED/FALLBACK badge.
> (2) **Prefs drawer**: persona (Analyst / Casual / Club fan / Player lens), language (EN/ES/FR/PT/DE),
>     favourite club, follow a player (searchable roster). Send `update_prefs` on change.
> (3) **Split view** (the demo money shot): the same match side by side for two different viewer
>     prefs, e.g. Analyst-EN vs Casual-ES. Implement as two independent WS subscriptions.
> (4) **Broadcast mode**: pitch and overlays only, full screen, plus a toggleable **Cue inspector** panel
>     showing the raw OverlayCue JSON stream (this proves "machine-readable for the rendering partner").
> (5) **Recap view** after full time.
> Visual direction: dark broadcast-studio look, high contrast, one accent colour, tabular numerals,
> 1080p-friendly. Must look great in a screen recording. No real club or league branding anywhere.
> API base URL from `VITE_API_URL`. Commit to `ws4-web`.

**Acceptance:** `npm run build` clean · works end to end against the mock · split view and cue inspector work · no console errors.

## WS5 — Azure infra + Event Hubs bus · Sat PM / Sat night
> Implement `src/fulcrum/bus/eventhubs.py` (aiokafka against `<ns>.servicebus.windows.net:9093`, SASL_SSL
> PLAIN with the connection string for dev; document managed-identity/OAUTHBEARER as the production path)
> with the same semantics as MemoryBus (consumer group = `group`). Write `Dockerfile` (one image, entrypoint
> per service via env `FULCRUM_SERVICE=sim|engine|agents|api`), and `infra/` with **azd** + Bicep:
> Log Analytics + App Insights, Container Registry, Container Apps environment with 4 apps (api external
> ingress with WebSockets; engine/agents/sim internal), Event Hubs Standard namespace with the five hubs
> and consumer groups, user-assigned managed identity, Key Vault for the connection string, and Static
> Web App for `web/`. Reference the existing Foundry project via parameter (do not create it). Target `azd up` in
> one command. Write `docs/DEPLOY.md`.

## WS6 — Docs, submission, video · Sun–Mon
README (hero GIF, the five-stage table, an architecture diagram in Mermaid, "How Microsoft tech is used",
eval numbers, "Path to production": auth, Cosmos persistence, real tracking/vision auto-eventing, rendering
partner integration, multi-tenant). Then the submission fields (title, tagline ≤ 300, keywords, description)
and the video script.

---------------------------------------------------------------------------------------------------
## Judging rubric → where we score (keep this in mind when cutting)
| Criterion (20 % each) | Evidence |
|---|---|
| Tech implementation | typed contracts, tests, CI-able `make test`, azd one-command deploy, clean README |
| Agentic design | 5 distinct roles, shared typed state, repair loop, fallback, tracing |
| Real-world impact | broadcast cue feed, persona engine, latency numbers, path to production |
| UX & presentation | split view, broadcast mode, 2-minute video with a clear story |
| Category adherence | the five pipeline stages map 1:1 to the brief (table in the README) |
| Category prizes | Foundry (hosted agents, tracing, evals) · Multi-agent (Agent Framework workflow) · Azure cloud native (Event Hubs, Container Apps, SWA, MI, App Insights) · Enterprise (guardrails, fact-check, observability) |
