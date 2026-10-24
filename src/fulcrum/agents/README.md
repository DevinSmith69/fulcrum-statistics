# `fulcrum.agents` — the agent team (WS3)

Turns every engine `Moment` into an `Insight` (one explanation + four persona variants), its `OverlayCue`s and,
at full time, a `Recap`. **Agents explain numbers; they never invent them.** A deterministic fact-checker
enforces that, and deterministic templates take over whenever the LLM path can't produce verified copy.

Design reference: `docs/AI_PROMPT_DESIGN.md`. Wire formats: `docs/API_CONTRACT.md` §1 and §4.

## Flow (one moment)

```
  [analyst] ──▶ [persona_editor] ──▶ [fact_checker] ──pass──▶ [cue_producer] ──▶ Insight + OverlayCue[]
      ▲  ▲               ▲                 │fail, attempt 1
      │  └───────────────┴──── issues ─────┘   (analyst re-runs only if the explanation itself failed)
      │                                    │fail, attempt 2 · LLM error twice · timeout · step crash
      └─ LLM error, attempt 1              └──▶ [template_fallback] ──▶ [cue_producer]
```

| Status | Meaning |
|---|---|
| `passed` | first LLM attempt verified |
| `repaired` | attempt 1 failed (fact-check issues or an LLM error), attempt 2 verified |
| `fallback` | templates used; `Insight.check_notes` says why (also logged as a warning) |

- **Timeouts:** 8 s per LLM call (6 s timed out on ~4% of moments with 2–3 matches running at once), 15 s per run (`OrchestratorConfig`). A timeout skips the repair and goes
  straight to fallback, because the latency budget is already spent.
- **Ordering:** `MomentProcessor` runs one worker per match, so moments are explained in order, and different
  matches run in parallel.
- **LLM reviewer:** optional (`FULCRUM_LLM_REVIEW=1`, significance ≥ 0.6). Its issues feed the same repair
  loop. If the reviewer fails, the insight is not blocked.

### Microsoft Agent Framework + Foundry (WS3b, done)
- **Workflow:** `ExplainMomentWorkflow` runs an Agent Framework `Workflow` built by `build_workflow()` from
  `EDGES`: one `Executor` per step, and a switch-case edge group per step (one plain edge where a step has a
  single route). Each executor runs its step, picks the first matching `Edge`, bumps the attempt on a retry
  edge and routes there, so `EDGES` stays the only routing table. Built workflows refuse concurrent runs, so
  idle ones are pooled (one in use per match worker). Agent Framework logs a harmless "self-loop" warning
  when it builds the retry edges.
- **LLM:** `fulcrum.llm.foundry.FoundryLLM` (`FULCRUM_LLM=foundry`). Each role is an Agent Framework `Agent`
  named after the role on a `FoundryChatClient`, with the pydantic schema as `response_format`. The Match
  Reporter runs on `FOUNDRY_MODEL_DEPLOYMENT_RECAP` (`strong`, gpt-5-mini, no temperature); every other role runs
  on `FOUNDRY_MODEL_DEPLOYMENT` (`fast`, gpt-5.4-mini; temperature 0.4 for the Editor, 0.2 otherwise). Auth is
  `DefaultAzureCredential`. `AgentService.start()` calls `warm_up()` so the first moment isn't a cold start.
- **Foundry prompt agents:** at warm-up the roles with a fixed system prompt (`PROMPT_AGENT_ROLES`: Analyst,
  Editor, Reviewer) are published to the Foundry project as versioned prompt agents (`fulcrum-moment-analyst`,
  `fulcrum-persona-editor`, `fulcrum-fact-reviewer`) and called by name via `FoundryAgent`, so they are
  listed, versioned and traced in the Foundry portal. Model, instructions, temperature and output schema live
  in the agent definition; a new version is created only when they change (fingerprint + prompt version in the
  version metadata). The Reporter, Translator and Player Lens have per-call prompts (`{persona}`,
  `{language}`, `{player}`) and run as local Agent Framework agents, as does any role whose publish failed.
  `FOUNDRY_PROMPT_AGENTS=0` turns publishing off.
- **Editor fan-out:** with `FULCRUM_LLM=foundry` the Editor writes the four variants as four concurrent calls,
  one variant each (`OrchestratorConfig.editor_fanout`, `FULCRUM_EDITOR_FANOUT`). Each call writes a quarter
  of the text, which takes the Editor from about 2.9 s to about 1.8 s per moment, for about 4× the Editor's
  input tokens. Tests keep the single-call mode, which the scripted LLM fakes expect.
- **Tracing:** `fulcrum.telemetry.configure_telemetry()` (agents and api start-up) installs Azure Monitor when
  `APPLICATIONINSIGHTS_CONNECTION_STRING` is set and turns on Agent Framework instrumentation: workflow,
  executor and agent spans. `Insight.agent_trace_id` is the workflow run's trace id. Use
  `FULCRUM_OTEL_CONSOLE=1` to print spans locally.
- **Recap budget:** 60 s per call and 110 s overall (`recap_call_timeout_s`, `recap_budget_s`). The reasoning
  model needs 25–40 s, and the recap is not on the live path.

### Live evaluation (`scripts/eval_live.py`, AI_PROMPT_DESIGN §7)
`uv run python scripts/eval_live.py data/matches/sample_seed11.jsonl --json data/eval/sample_seed11.json`
replays a recording through the engine and explains every moment, in order, on Foundry.
Runs on 2026-10-24 (sample_seed11, HLC 2-2 MDU, 30 moments):

| | First run (one Editor call, local agents) | Editor fan-out, prompt agents | Latest (same setup, evaluated below) |
|---|---|---|---|
| Passed / repaired / fallback | 93.3 % / 6.7 % / 0 % | 90.0 % / 6.7 % / 3.3 % | 96.7 % / 3.3 % / 0 % |
| Moment → insight latency | p50 4.2 s · p95 6.7 s | p50 3.6 s · p95 6.0 s | p50 3.1 s · p95 5.7 s |
| Tokens per match | ≈ 115k | ≈ 275k (Editor input ×4; still pennies on the mini model) | ≈ 258k |
| Recap (strong) | passed, 26 s | passed, 29 s, names only | passed, 28 s |

The one fallback in the second run was the fact-checker rejecting a "10" that is not in the fact pack, twice;
the template copy was published instead, as designed. `data/eval/sample_seed11.json` holds the latest run, and
`data/eval/sample_seed11.records.jsonl` one evaluation record per moment (input of the Foundry evaluation).

### Foundry evaluation (`scripts/eval_foundry.py`)
`uv run python scripts/eval_foundry.py data/eval/sample_seed11.records.jsonl` scores that run's published copy
with Foundry's built-in quality evaluators through Agent Framework's `FoundryEvals` (`agent_framework.foundry`):
one `EvalItem` per moment, where the query is the task plus the moment, the context is the moment's MOMENT /
FACTS / ROSTER / RECENT SEQUENCE / SCORE sections (the only evidence the agents get), and the response is the
explanation plus all four persona variants. The judge is the `fast` deployment (gpt-5.4-mini). The run is listed
on the Foundry portal's Evaluations page (`report_url` in the summary), and `data/eval/foundry_sample_seed11.json`
holds the summary. Run on 2026-10-24 over the 30 moments above (scores 1–5, pass ≥ 3):

| Evaluator | Mean | Min | Pass rate |
|---|---|---|---|
| Groundedness (response vs. the moment's facts/context) | 4.53 | 2 | 96.7 % |
| Relevance | 4.60 | 3 | 100 % |
| Coherence | 4.30 | 4 | 100 % |
| Fluency | 3.80 | 3 | 100 % |

29 of 30 moments passed every evaluator; about 330k judge tokens. The one groundedness fail (90+5' player
milestone, score 2) was the judge objecting to interpretation ("keeping the ball under pressure") rather
than to a number: every number had already passed the deterministic fact-checker. A repeat run on the same
records gave 4.57 / 4.70 / 4.37 / 3.83 with two groundedness fails, so read single-moment scores as ±1 noise.
The dashboard's `#quality` view shows these scores via `GET /api/eval/latest`.

## Modules

| File | What |
|---|---|
| `schemas.py` | `EditorOutput`, `TranslationOutput`, `ReviewOutput`, `RecapOutput`, `MomentContext` (shared state) |
| `prompts.py` | §4 system prompts as versioned `PromptSpec`s (`PROMPT_VERSIONS` for traces/evals) |
| `context.py` | user-prompt builders (MOMENT · FACTS · ROSTER · RECENT SEQUENCE · SCORE), team loading/resolution, `MatchContextStore` |
| `factcheck.py` | §5 deterministic checker (see rules below) |
| `denylist.py` | real clubs/competitions, betting terms in en/es/fr/pt/de |
| `templates.py` | §6 fallback: templates per MomentType × persona, filled from facts only |
| `cues.py` | Insight → OverlayCue[] (API_CONTRACT §4) |
| `orchestrator.py` | steps, `EDGES`, `ExplainMomentWorkflow`, `MomentProcessor` |
| `translate.py` | on-demand translation, cached per (insight, language); fact-checked; falls back to English |
| `player_lens.py` | on-demand Player Lens: the moment retold through the followed player, only when they are in `moment.player_ids`; fact-checked, one repair, cached per (insight, player); other languages via the Translator; `None` → the viewer gets the casual variant. Exposed as `AgentService.player_lens(insight_id, player_id, language)` and used by the API for `player_lens` viewers |
| `recap.py` | full-time Match Reporter, cached per (match, persona, language); template fallback |
| `evaluation.py` | per-moment evaluation records (query / context / response) and the Foundry evaluation summary (`scripts/eval_live.py`, `scripts/eval_foundry.py`) |
| `service.py` | `AgentService`: `moments` + `match-events` in, `InsightsEnvelope` out on `insights` |

## Fact-checker rules (`factcheck.py`)
1. **Numbers.** Every numeric token must equal (numerically, after normalising decimal commas) one of: a fact
   value, a number inside a string fact or a fact label (`"2-1"`, `"67'"`, "last 5 min"), the score, or the
   moment minute. Sign is ignored, so a `-12` delta may be written "dropped 12". "11,240" is one number.
   Scorelines get no free pass: each digit must be a fact-pack number. This is stricter than §5.1, so a
   wrong score is rejected.
2. **Fact keys.** Every key in `key_fact_keys` must exist in the fact pack.
3. **Names.** No real club or competition from the denylist (any case; hyphens and NBSPs tolerated). No team
   or player from the fictional league who is not in this match, in any case, including a foreign team's
   first word (names are unique league-wide).
4. **Betting language.** Checked in English and in the copy's own language.

Template fallback copy is itself fact-checked candidate by candidate, so FALLBACK output always passes.
Big-chance templates (and the wp_swing WHY line) use the engine's `whatif_*` facts when present ("Had it gone
in, it would be 0-1 and Riverside Rovers' win probability would be 52% instead of 31%."); the Analyst and
Editor prompts (analyst/1.2.2, editor/1.3.2) allow them only when present, and the user prompt gets a WHAT-IF
section only for moments that carry `whatif_` facts (with it everywhere, the Editor wrote "had it gone in" on
phase changes and passes).
5. **Lengths.** Headline ≤ 90, body ≤ 300, commentary ≤ 160 chars and ≤ 25 words. The editor must return
   exactly analyst, casual, club_fan(home) and club_fan(away).

## Cue mapping
| Moment | Cues (priority, duration, anchor) |
|---|---|
| goal | `lower_third` (5, 8 s, bottom-left) + `wp_swing` (4, 6 s, top-right) |
| wp_swing | `wp_swing` (4) |
| pressure_shift, momentum_run | `pressure_bar` (3, top-left) |
| phase_change, tempo_change* | `pressure_bar` (2) |
| elite_pass, speed_record | `stat_card` (3, bottom-right) |
| player_milestone | `stat_card` (2) |
| big_chance* | `stat_card` (3): xG, then the what-if win % ("Win chance if scored", MODEL_SPEC §3.1) |
| red_card* | `lower_third` (4) + `wp_swing` (3) |
| half_time* / full_time | `recap_card` (4 / 5, bottom-center) |
| players in `moment.player_ids` | `player_tag` (2, top-left), persona `player_lens` |

\* Not specified by the contract; these are our choices. Every cue has one copy per English variant.
`show_at_clock_ms` = moment clock + latency rounded up to the next second. Each cue has at most 4 stats.

## Run
```bash
make agents                     # python -m fulcrum.agents (uses FULCRUM_BUS / FULCRUM_LLM)
FULCRUM_LLM=fake make test      # all agent tests are offline
```
With `FULCRUM_LLM=fake` and no canned output, every moment is a `fallback`. That is expected: the dev
pipeline still shows verified template copy. Env: `FULCRUM_LLM_REVIEW` (0/1), `FULCRUM_AGENTS_EVENTS` (1 =
also consume `match-events` for the recent sequence and score), and `FULCRUM_TEAMS_PATH`.

## Tests (`tests/agents/`)
`test_golden.py` puts every moment type through the full flow. `test_orchestrator.py` covers the
PASSED/REPAIRED/FALLBACK paths, timeouts, the reviewer, crash handling, ordering and concurrency.
`test_factcheck.py` covers the rules, including decimal commas, denylisted names and banned terms per
language. `test_templates.py` renders every template and fact-checks it. `test_cues.py`, `test_translate.py`,
`test_recap.py` and `test_service.py` cover the rest.
