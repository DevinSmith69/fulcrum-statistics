# `fulcrum.api` — Render / Personalise gateway (WS2)

FastAPI app `fulcrum.api.app:app` implementing API_CONTRACT §2 (REST under `/api`) and §3 (WebSocket `/ws`).

| File | What |
|---|---|
| `app.py` | `create_app()`, lifespan, REST routes, RFC 7807 problem errors, `/ws` loop. |
| `hub.py` | `Hub` consumes `match-events` (`api-feed`), `match-state` / `moments` / `insights` (`api`) into read models and fans out to `Connection`s; each connection personalises in its own sender task. |
| `readmodel.py` | Per-match ring buffers (events, WP points, moments, insights, cues, recaps). In-memory only. |
| `personalise.py` | Persona / language / club / player filtering; server-side `player_tag` cues for a followed player. |
| `translation.py` | On-demand translation + lazy recaps via the agents' `AgentService` hooks; English passthrough fallback. |
| `pipeline.py` | Single-process dev mode: engine + agents + simulator as asyncio tasks, imported lazily and guarded. |

## Running
```bash
make dev      # FULCRUM_BUS=memory: POST /api/matches starts sim (or a replay) + engine + agents in-process
```
| Env | Default | Meaning |
|---|---|---|
| `FULCRUM_BUS` | `memory` | `eventhubs` in Azure (then nothing runs in-process unless `FULCRUM_INPROCESS=1`) |
| `FULCRUM_INPROCESS` | on for memory bus | run engine/agents/sim inside the API process |
| `FULCRUM_RUN_SIM` / `FULCRUM_RUN_AGENTS` | `1` | switch the in-process simulator / agents off |
| `FULCRUM_REPLAY_FILE` | – | replay this `{"topic","data"}` JSONL instead of simulating (deterministic demos) |
| `FULCRUM_ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | CORS, comma-separated |

Without `fulcrum.sim` the API replays `data/matches/sample_seed7.jsonl` (or the hand-made test fixture);
without `fulcrum.agents` moments still flow but there are no insights.

## Protocol notes (beyond API_CONTRACT)
- `POST /api/matches` → **201** `{"match_id"}`; 422 for unknown/identical teams; 409 for a duplicate id.
- `GET /api/matches` rows: `score` is `{"home": int, "away": int}`; `status` is `scheduled | live | finished | error`.
- `GET …/state` is 404 until the first state arrives. `GET …/recap` is 404 until full time and until a recap exists.
- `insights` / `overlay-cues` accept `favourite_team_id` / `followed_player_id` too; with neither `persona`
  nor `language` they return the unfiltered feed (for tools / the cue inspector).
- WS: every send goes through one ordered sender task. `hello` also carries `status` and `period_start_clock_ms`; `update_prefs` re-sends
  `hello` (replace your feed). Errors: `bad_request`, `unknown_match` (socket stays open). Clients should de-dupe
  by `seq` / `moment_id` / `insight_id` / `cue_id` — a `hello` and the live stream may overlap by an item.
- A viewer with no variant for their persona gets the casual one; missing languages are translated on demand
  (English until the agents answer).

- Out of process (Azure, `FULCRUM_INPROCESS` off): with `FULCRUM_SIM_URL` set, `POST /api/matches` forwards the
  config to the sim service (`POST {FULCRUM_SIM_URL}/matches`). If the sim is unreachable or refuses, the API
  answers 502 and does not keep the match registered.
