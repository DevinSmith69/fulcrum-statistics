# Fulcrum web (WS4)

The **Render** and **Personalise** stages of the pipeline: a React overlay that shows the match live, explains
each moment for the viewer's persona and language, and renders the machine-readable `OverlayCue` feed as
broadcast graphics.

Vite + React 19 + TypeScript + Tailwind v4, recharts for the probability chart. No UI kit. Fonts (Barlow,
Barlow Condensed) are bundled from `@fontsource`, so the app runs fully offline.

## Run it against the mock (no Python, no Azure)

```bash
cd web
npm install
npm run mock        # terminal 1: mock API + WebSocket on http://localhost:8000
npm run dev         # terminal 2: app on http://localhost:5173
```

The mock replays a synthetic Northbridge Athletic v Riverside Rovers match at **10× speed** (about 10 minutes
for 90+ minutes of play) with ~30 moments, insights for every persona in five languages, overlay cues and
a full-time recap. Everyone connected shares one match clock, so split view and multiple tabs stay in sync.

| Option | Example | What it does |
|---|---|---|
| `--from <minute>` | `npm run mock -- --from 80` | Start mid-match (handy for recording the finish) |
| `--speed <x>` | `--speed 20` | Sim-time multiplier (default 10) |
| `--home/--away` | `--home HLC --away MDU` | Any fixture from `data/teams.json` |
| `--seed <n>` | `--seed 11` | Different generated play between the scripted beats |
| `--ht-break <s>` | `--ht-break 5` | Wall-clock half-time pause (default 12 s) |
| `--loop` | | Restart 60 s after full time |
| `--port <n>` | `--port 8001` | Listen elsewhere (then set `VITE_API_URL`) |

`POST /api/matches` (or restarting the mock) starts a fresh match; connected clients are moved onto it.

## Run it against the real API

```bash
make dev            # repo root: FastAPI + sim + engine + agents on :8000
cd web && npm run dev
```

The API base comes from `VITE_API_URL` (default `http://localhost:8000`); the WebSocket URL is derived from it
(`http → ws`, `+ /ws`). Copy `.env.example` to `.env.local` to change it.

## Screens

| Route | Screen | Notes |
|---|---|---|
| `#/` | **Match Studio** | Scoreboard, live 2D pitch, overlay cues, outcome probability chart, momentum, pressure/control gauges, the explained-moments feed ("analysing…" until the insight lands, then a Passed / Repaired / Fallback badge) |
| prefs drawer | **Viewer preferences** | "Viewing as …" in the header: persona, language, favourite club, searchable roster to follow a player. Sends `update_prefs` live |
| `#/split` | **Split view** | Two independent WebSocket subscriptions with their own prefs (default Analyst·EN v Casual·ES) |
| `#/broadcast` | **Broadcast mode** | Pitch + overlays only. `I` toggles the **cue inspector** (raw `OverlayCue` JSON + the REST feed URL), `F` full screen, `Esc` back |
| prefs drawer → Audio | **Voice & audio description** | `V` voice commentary (speaks each new insight's `commentary_line` in the viewer's language), `D` audio description (localised, deterministic lines for goals, cards, big chances, subs, half/full time and the score, plus the commentary lines), `S` stop. Browser-local (`fulcrum.audio` in localStorage). Broadcast mode has Voice / Stop buttons too |
| `#/recap` | **Recap** | After full time: the persona's report, key moments, the whole outcome-probability story, player of the match, team stats |
| `#/quality` | **Quality** | Guardrails and observability for the current match, from the live feed: passed / repaired / fallback rates, moment → insight latency p50/p95, latency per moment over match time (colour + shape by fact-check status), the repaired and fallback moments with their `check_notes`; plus the latest offline agent run and Foundry evaluator scores (groundedness, relevance, coherence, fluency) from `GET /api/eval/latest` (the mock serves the committed `data/eval/` files) |

## How it works

- `src/types/generated.ts` is **generated** from `../schemas/*.schema.json` (`npm run types`, after `make schemas`).
  App code imports contract types from `src/types`, never redefines them.
- `src/lib/useMatchFeed.ts` — one WebSocket subscription (API_CONTRACT §3): subscribe, ping, reconnect with
  back-off, `update_prefs` on change. After each `hello` it fetches `/events` once to rebuild line-ups.
- `src/lib/pitchModel.ts` — turns team-relative event coordinates into absolute pitch positions (home attacks
  right in period 1), tracks subs/red cards, and places players from 4-3-3 anchors around the ball.
- `src/lib/cueStage.ts` — overlay scheduling per API_CONTRACT §4: one cue per anchor, higher priority pre-empts,
  equal priority queues, a cue shows once the match clock reaches `show_at_clock_ms`.
- `src/components/cues/CueTemplates.tsx` — the six templates: `lower_third`, `wp_swing`, `stat_card`,
  `player_tag`, `pressure_bar`, `recap_card`.
- `src/lib/factTrail.ts` — **fact trail**: finds numbers in insight copy with the same rules as
  `agents/factcheck.py` (decimal commas, `71 %`, `67'`, thousands groups, the moment's minute) and links each to
  its fact. Insight cards highlight traced numbers (hover/focus shows label · value · key) and a "Fact trail"
  disclosure lists the explanation's `key_fact_keys` with the check status.
- `src/lib/replay.ts` + `src/components/MiniReplay.tsx` — **mini replay** on each insight card: picks the run
  of play behind the moment (`seq_start`…`seq_end`, widened to the whole possession, never past a kick-off) from
  `/events` (cached per match) and replays it on a mini pitch with the normal `Pitch` renderer and its own
  `PitchModel`, ringing the moment's `player_ids`. Plays twice, then closes; `Esc` closes early.
- Voice and accessibility: `src/lib/speechQueue.ts` (one line at a time; goals and red cards pre-empt; lines older
  than 15 s of match time *and* 4 s of wall time are dropped; de-duped; bounded), `src/lib/describe.ts`
  (audio-description phrasing in en/es/fr/pt/de, no LLM), `src/lib/speech.ts` (Azure neural voice through
  `POST /api/speech`; after a 503, the browser's `speechSynthesis` with a matching voice), `src/lib/useCommentary.ts`
  (feed → queue; only live insights are spoken, not the `hello` catch-up). Match Studio and Broadcast carry a
  visually hidden `aria-live` log (polite; goals and red cards assertive) with the same lines for screen readers.
- `mock/` — `match.mjs` (seeded match generator: possession chains, live stats, the MODEL_SPEC §3
  outcome-probability model, moment rules and fact packs), `text.mjs` (persona × language copy built only
  from fact-pack numbers, cue mapping, recaps), `server.mjs` (REST + WS).

## Checks

```bash
npm run build       # tsc + vite build (must be clean)
npm test            # node:test — cue scheduling, coordinates, clock, line-ups, colours, fact trail, replay window, speech queue, audio description
node mock/inspect.mjs 7   # print the mock match's stats and moment timeline
```

Contract check for the mock (run from the repo root; every payload must validate against the pydantic models):

```bash
node web/mock/dump.mjs > /tmp/mock.jsonl
uv run python -c "import json,sys; from fulcrum.contracts import models as M; [getattr(M, r['model']).model_validate(r['data']) for r in map(json.loads, open('/tmp/mock.jsonl'))]; print('ok')"
```

## Design notes

Dark broadcast-studio look built for a 1080p screen recording: studio-black base, cool steel neutrals, one
accent (signal cyan `#3DE0FF`), Barlow Condensed for scores and graphics, tabular numerals everywhere. Team
colours appear only as kit stripes, dots and chart series; chart series are re-derived in OKLCH at a fixed
lightness so every fixture of the fictional clubs passes a colour-vision separation check. No real clubs,
leagues, crests or competition branding anywhere. Outcome probability is labelled "model, not odds".
