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

- **Timeouts:** 6 s per LLM call, 15 s per run (`OrchestratorConfig`). A timeout skips the repair and goes
  straight to fallback, because the latency budget is already spent.
- **Ordering:** `MomentProcessor` runs one worker per match, so moments are explained in order, and different
  matches run in parallel.
- **LLM reviewer:** optional (`FULCRUM_LLM_REVIEW=1`, significance ≥ 0.6). Its issues feed the same repair
  loop. If the reviewer fails, the insight is not blocked.

### Mapping to Microsoft Agent Framework (WS3b)
Each step in `orchestrator.py` is one executor (`Step.name` is the executor id). `MomentContext`
(`schemas.py`) is the shared state. `EDGES` is the list of `WorkflowBuilder.add_edge(source, target,
condition)` calls, evaluated in order with the first match winning. Routing lives only in `EDGES`. To port,
wrap each step's `run(ctx)` in an executor, build the graph from `EDGES`, and swap the LLM calls for Foundry
agents behind `LLMClient`.

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
| `recap.py` | full-time Match Reporter, cached per (match, persona, language); template fallback |
| `service.py` | `AgentService`: `moments` + `match-events` in, `InsightsEnvelope` out on `insights` |

## Fact-checker rules (`factcheck.py`)
1. **Numbers.** Every numeric token must equal (numerically, after normalising decimal commas) one of: a fact
   value, a number inside a string fact or a fact label (`"2-1"`, `"67'"`, "last 5 min"), the score, or the
   moment minute. Sign is ignored, so a `-12` delta may be written "dropped 12". Small scorelines (`1-0`,
   digits 0–3) are allowed as phrases.
2. **Fact keys.** Every key in `key_fact_keys` must exist in the fact pack.
3. **Names.** No real club or competition from the denylist. No team or player from the fictional league who
   is not in this match (names are unique league-wide).
4. **Betting language.** Checked in English and in the copy's own language.
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
| big_chance* | `stat_card` (3) |
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
