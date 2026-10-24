# API_CONTRACT — topics, REST, WebSocket, overlay cues

Types referenced here live in `src/fulcrum/contracts/models.py` (JSON Schema in `schemas/`).
The pydantic models win over this document if they ever disagree — then fix this document.

## 1. Data flow
```
 sim ──MatchEvent──────▶ [match-events]   ──▶ engine ──MatchState──▶ [match-state] ──▶ api ──WS──▶ web
     ──TrackingFrame───▶ [tracking-frames] ──▶ engine ──Moment─────▶ [moments]     ──▶ agents
                                                          agents ──Insight/OverlayCue/Recap──▶ [insights] ──▶ api ──WS──▶ web
```
| Topic (Event Hub) | Model | Key | Producer | Consumers (consumer group) | Partitions |
|---|---|---|---|---|---|
| `match-events` | MatchEvent | match_id | sim | engine (`engine`), agents (`agents`), api (`api-feed`) | 2 |
| `tracking-frames` | TrackingFrame | match_id | sim | engine (`engine`) | 2 |
| `match-state` | MatchState | match_id | engine | api (`api`) | 2 |
| `moments` | Moment | match_id | engine | agents (`agents`), api (`api`) | 2 |
| `insights` | Insight \| OverlayCue \| Recap (envelope below) | match_id | agents | api (`api`) | 2 |

`insights` carries an envelope so one topic serves three types:
`{"kind": "insight" | "cue" | "recap", "data": {...}}` — this is `InsightsEnvelope` in `fulcrum.contracts`.
Producers publish `InsightsEnvelope.wrap(item)`; consumers subscribe with `InsightsEnvelope` and branch on `kind`.

**Ordering & idempotency:** key = match_id ⇒ per-match ordering within a partition. Consumers de-dupe on
`(match_id, seq)` for events and on ids for everything else. Engine publishes `MatchState` at most every
**1 s sim-time or on every moment**, not per event (bandwidth).

**Single-process dev mode:** with `FULCRUM_BUS=memory` the API process starts sim, engine and agents as
asyncio tasks on the shared `MemoryBus` (`api/app.py` lifespan). In Azure each runs as its own Container App.

**Topologies** (`FULCRUM_TOPOLOGY`, `fulcrum.topology`, docs/DEPLOY.md §4) change *which process* runs each
consumer, never the topics or the consumer groups above: in `hybrid` the API process hosts the engine (group
`engine`) and the agents (group `agents`) next to its own `api` / `api-feed` consumers, reading the sim's
topics from Event Hubs and publishing `match-state` / `moments` / `insights` back to it. A group must have only
one kind of process in it, so in `hybrid` the engine and agents apps stand by instead of consuming.

## 2. REST (FastAPI, `fulcrum.api.app:app`, prefix `/api`)
| Method | Path | Body / Query | Returns |
|---|---|---|---|
| GET | `/api/health` | – | `{"status":"ok","bus":"memory","llm":"fake","topology":"inprocess","in_process":true,"sim":"simulator","agents":true,"agents_state":"running","agents_error":null,"speech":false,"match_controls":true}` (`match_controls`: see Match controls below); `topology` is `split` \| `inprocess` \| `hybrid`; `sim` is `simulator` \| `replay` (in this process), `service` (forwarded to `FULCRUM_SIM_URL`) or null; `agents*` are null in `split`; `agents` is true only once the agents service is running (it starts with the first match); `agents_state` is `off` \| `unavailable` \| `not_started` \| `starting` \| `running` \| `failed` (reason in `agents_error`) |
| GET | `/api/teams` | – | `Team[]` (from data/teams.json) |
| POST | `/api/matches` | `MatchConfig` (match_id optional) | `{"match_id": str}` — starts a simulated match |
| GET | `/api/matches` | – | `[{match_id, home_team_id, away_team_id, status, clock_ms, score}]` |
| GET | `/api/matches/{id}/state` | – | latest `MatchState` |
| GET | `/api/matches/{id}/events` | `since_seq` | `MatchEvent[]` |
| GET | `/api/matches/{id}/winprob` | – | `WinProbPoint[]` |
| GET | `/api/matches/{id}/moments` | – | `Moment[]` |
| GET | `/api/matches/{id}/insights` | `persona`, `language` | `Insight[]` (variants filtered) |
| GET | `/api/matches/{id}/overlay-cues` | `persona`, `language`, `since_clock_ms` | `OverlayCue[]` — **the machine-readable feed for a broadcast graphics partner** |
| GET | `/api/matches/{id}/recap` | `persona`, `language` | `Recap` (404 until full time; generated lazily per persona/lang, cached) |
| POST | `/api/matches/{id}/translate` | `{"insight_id", "language"}` | `InsightVariant[]` (on-demand translation, cached) |
| GET | `/api/speech` | – | `{"enabled": bool, "auth": "key" \| "entra" \| null, "voices": {lang: voice}}` — is neural voice configured? |
| POST | `/api/speech` | `{"text"` (1–400 chars)`, "language"}` | `audio/mpeg`: the line in that language's Azure AI Speech neural voice (cached per voice + text). **503** when Azure Speech is not configured (the overlay then uses the browser's `speechSynthesis`); 502 if the Speech service fails |
| GET | `/api/eval/latest` | – | `{"live": report \| null, "foundry": summary \| null}` — read-only, the newest offline evaluation reports in `data/eval/` (`FULCRUM_EVAL_DIR` overrides). `live` = `scripts/eval_live.py` (`status_counts`, `status_rates_pct`, `latency_ms.p50/p95/mean`, `tokens_total`, `llm`, `recap`, …); `foundry` = `scripts/eval_foundry.py` (`evaluators.{groundedness,relevance,coherence,fluency}.{mean,min,n,pass_rate_pct}` on the 1–5 scale, `by_check_status`, `lowest[]` with judge `reasons`, `report_url` to the Foundry portal, `judge_deployment`). Both carry `file` and `generated_at`; per-item detail is omitted |
| POST | `/api/matches/{id}/ask` | `AskRequest` `{"question", "persona"?, "language"?, "favourite_team_id"?}` | `AskAnswer` `{answer, answerable, check_status, cited_moment_ids, fact_keys, persona, language, latency_ms, …}` — **Ask the studio** (see below) |

**Ask the studio** (`POST /api/matches/{id}/ask`): the Studio Analyst agent answers a viewer's question (1–300
chars) about the live match in `language` (en/es/fr/pt/de, default en) and the `persona`'s tone (default casual;
`favourite_team_id` sets the club_fan side). It uses only a context pack built from the match so far (score,
minute, team and top-player live stats from `MatchState`, the most significant + latest moments with their
facts, rosters), and every number is fact-checked against that pack (one repair). `answerable=false` with
`check_status="fallback"` is a safe deterministic reply: the question was about betting/odds or a real club
(refused without an LLM call), or the answer could not be verified. Answers are cached per (match, normalised
question, language, persona, team, number of moments so far). Errors: 404 unknown match · 422 bad body ·
429 rate limit (per client IP, `FULCRUM_ASK_RATE`, default `6/30` = 6 questions per 30 s; `Retry-After` header) ·
503 when no agents service is running in the API process (out-of-process deployments, or the agents failed
to start).

### Match controls (demo director)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/matches/{id}/control` | – | `ControlState` (below) |
| POST | `/api/matches/{id}/control` | `{"action": "set_speed", "speed": 30}` · `{"action": "pause"}` · `{"action": "resume"}` · `{"action": "next_moment", "min_significance"?: 0.65, "types"?: MomentType[]}` · `{"action": "cancel_skip"}` | `ControlState` after the change |

`ControlState` = `{"match_id", "available": true, "speed", "effective_speed", "paused", "skipping",
"skip_min_significance", "position_ms", "finished", "last_skip": {"moment_id", "type", "clock_ms",
"significance"} | null}`.

- **Pacing only.** The controls change how fast the simulator's items are *released* (wall clock), never
  the items: same seed → identical event stream at any speed, paused or not (`tests/sim/test_sim_control.py`).
  Event `ts` is the real publish time whenever a match is paced, as before.
- `speed` is the sim-time multiplier (0.25–600; the overlay offers 1×, 5×, 10×, 30×, 60×). A change applies
  at once, also to an item already waiting, and never bursts to "catch up". Pause holds the stream before the
  next item; resume continues from there.
- `next_moment` runs at 120× (or `speed`, if faster) until the engine emits a moment with
  `significance >= min_significance` (default 0.65: goals, red cards, big chances, full time; not
  phase/tempo/pressure notes) — or, with `types`, a moment of one of those types — then drops back to
  `speed` and records it in `last_skip`. It also resumes a paused match. `cancel_skip` stops early.
  Moments passed on the way are still detected and explained (agents explain in arrival order).
- `GET /api/health` → `"match_controls": true` when this API process runs the simulator (in-process mode).
- Errors: 404 unknown match · 422 bad body (`set_speed` without `speed`, speed out of range) · 409 the match has
  finished · **501** when the match is not simulated in this process (out-of-process topology: the sim
  Container App paces itself; the overlay disables its controls).

The API keeps per-match read models in memory (dict of match_id → ring buffers). Persistence is a stretch
goal (Cosmos DB) — do not build it before Sunday evening.

## 3. WebSocket `/ws`
Client → server (`ClientMessage`):
```json
{"type":"subscribe","match_id":"…","prefs":{"persona":"casual","language":"en","favourite_team_id":"NBA","followed_player_id":"NBA-10"}}
{"type":"update_prefs","prefs":{…}}
{"type":"ping"}
```
Server → client (`ServerMessage`), `payload` is the JSON of the named model:
| type | payload | when |
|---|---|---|
| `hello` | `{"match_id", "home": Team, "away": Team, "state": MatchState, "winprob": WinProbPoint[], "moments": Moment[], "insights": Insight[], "status", "period_start_clock_ms": {"1": 0, "2": <clock at H2 kick-off>}}` | on subscribe (catch-up snapshot) |
| `event` | `MatchEvent` | every event (web may throttle rendering) |
| `state` | `MatchState` | ≤ 1 Hz |
| `moment` | `Moment` | when detected (before the insight is ready → UI can show "analysing…") |
| `insight` | `Insight` filtered to the subscriber's persona+language variant (translate on demand if missing) | when ready |
| `cue` | `OverlayCue` for the subscriber's persona/language | when ready |
| `recap` | `Recap` | after full time |
| `match_end` | `{"score_home","score_away"}` | full time |

Personalisation is **server-side filtering + on-demand translation**; the client never receives other
personas' text. `followed_player_id` additionally makes the server emit `player_tag` cues whenever that
player is involved in an event (no LLM needed — template from facts).

## 4. Overlay cue semantics
- One `Insight` produces ≥ 1 `OverlayCue` per persona (the agent layer emits cues for all 4 personas in
  English; other languages are produced when first requested and then cached).
- Template mapping: goal → `lower_third` (priority 5, 8 s) + `wp_swing` (priority 4, 6 s);
  wp_swing → `wp_swing`; pressure_shift/momentum_run/phase_change → `pressure_bar`; elite_pass /
  speed_record / milestone → `stat_card`; player involvement (followed player) → `player_tag`;
  full_time → `recap_card`.
- `show_at_clock_ms` = moment `clock_ms` + processing latency rounded up to the next second;
  `sync_seq` lets a renderer align to the event feed. Renderers must never show two cues with the same
  `anchor` at once — higher `priority` pre-empts; equal priority queues.
- Copy limits (enforced by the contract max_length): headline ≤ 90 chars, body ≤ 300, ≤ 4 stats.

## 5. Errors
REST: RFC 7807 problem JSON (`{"type","title","status","detail"}`). WS: `{"type":"error","payload":{"code","message"}}`
then keep the socket open unless the code is `fatal`.

## 6. CORS / auth
CORS: allow `http://localhost:5173` and the Static Web App origin (env `FULCRUM_ALLOWED_ORIGINS`).
No auth for the hackathon demo (documented as a production gap in the README "Path to production" section).
