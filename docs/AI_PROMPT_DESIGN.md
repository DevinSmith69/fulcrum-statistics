# AI_PROMPT_DESIGN — the agent team

**Principle: the engine decides *what* happened and *how much* it mattered; agents decide *how to say it*
to each viewer.** Agents may only use numbers from the moment's fact pack. A deterministic verifier
enforces this, so the system degrades gracefully, never confidently wrong.

## 1. Roles
| Agent | Kind | Input | Output (contract) | When |
|---|---|---|---|---|
| **Moment Analyst** | LLM (fast model) + tools | `Moment` + match context | `Explanation` | every moment |
| **Persona Editor** | LLM (fast model) | `Explanation` + facts + team names | 4 × `InsightVariant` (analyst, casual, club_fan home, club_fan away) | every moment |
| **Fact-Checker** | deterministic validator, plus an LLM reviewer for significance ≥ 0.6 | Explanation + variants + facts + rosters | pass / issues | every moment |
| **Cue Producer** | deterministic | `Insight` + moment | `OverlayCue[]` | every moment |
| **Translator** | LLM (fast model) | variants + target language | `InsightVariant[]` | on demand, cached |
| **Player Lens** | LLM (fast model) | explanation + followed player's live stats | `InsightVariant` (player_lens) | on demand, only when the followed player is in `moment.player_ids` |
| **Match Reporter** | LLM (stronger model) | full-time facts + top-8 insights | `Recap` per persona | full time (lazy per persona/language) |

Multi-agent story for judges: distinct roles, a **shared typed state** (`MomentContext`), explicit handoffs, a
**repair loop** (Fact-Checker → Analyst/Editor with issues), and **graceful fallback** (templates). That is
exactly what the Multi-Agent criteria ask for: "distinct roles, coordinate through shared state and effective
handoffs, recover from failures".

## 2. Orchestration (Microsoft Agent Framework)
Use the **Microsoft Agent Framework** Python package (`agent-framework`) with a **Foundry** project as the
model/agent backend. The API surface moves quickly, so **before writing code, read the installed version's
README and samples** (`uv pip show agent-framework`; github.com/microsoft/agent-framework → python/samples) and
follow them. Do not code from memory.

Target shape:
```
Workflow "explain_moment"
  [Analyst] ──▶ [PersonaEditor] ──▶ [FactChecker] ──pass──▶ [CueProducer] ──▶ publish(insights)
                     ▲                    │fail (attempt 1)
                     └────── issues ──────┘
                                          │fail (attempt 2) ──▶ [TemplateFallback] ──▶ [CueProducer]
```
- Prefer Foundry-hosted agents (the framework's Azure AI / Foundry agent client) so the agents and their traces
  are visible in the Foundry portal. That helps with the "Best use of Foundry" prize and the demo video.
- Enable the framework's OpenTelemetry tracing → Application Insights. Put the trace id on `Insight.agent_trace_id`.
- Concurrency: process moments for a match **in order** (one worker per match), but different matches in parallel.
- **Escape hatch:** if the Workflow API costs more than ~1 hour, orchestrate with plain asyncio behind
  `agents/orchestrator.py`, using Agent Framework agents for the LLM calls. Keep the diagram the same.
- All LLM access goes through `fulcrum.llm.LLMClient.structured(...)`, so `FakeLLM` drives every test.

**Latency budget:** moment → insight on screen in **< 4 s** p50. That is two fast-model calls (Analyst, Editor),
a deterministic check, and the optional reviewer only for big moments. Use structured output / JSON mode with
the pydantic schema. Use temperature 0.4 for the Editor and 0.2 for everything else.

**Cost guardrail:** about 30 moments × 2–3 calls × ~1.5k tokens ≈ 150k tokens per match on a mini model,
which is pennies. Recap uses the stronger deployment.

## 3. Tools for the Moment Analyst
Read-only functions over the engine's read model. Pass them to the agent as framework tools, and expose the same
functions in-process for tests.
- `get_fact_pack(moment_id)` → the facts (the agent also receives them inline; the tool is for re-reads)
- `get_recent_sequence(match_id, n=8)` → the last n events, compact text (`67' NBA-10 pass → NBA-9 (diff 7.8) …`)
- `get_wp_history(match_id, minutes=10)` → WinProbPoint summary
- `get_team_trend(match_id, team_id, minutes=5)` → pressure/control/shots in the window

Any number the agent wants to quote from tool output must also be in the fact pack. If it isn't, the agent must
describe it qualitatively. (Engine owners: when in doubt, add the fact.)

## 4. Prompts (starting points — keep them in `agents/prompts.py`, version them)

### Moment Analyst — system
```
You are the Moment Analyst for a live football studio. A deterministic engine has detected a moment
and quantified it. Your job: explain WHY it matters to the match, not just what happened.
Rules:
- Use ONLY numbers that appear in FACTS, exactly as formatted. Never compute new numbers.
- Name only players/teams listed in ROSTER.
- Pick ONE tactical_tag: control | chaos | pressure_shift | rhythm | individual_brilliance | set_piece | turning_point.
- why_it_matters: 1–2 sentences linking the moment to a change in the match (probability, pressure,
  control, tempo). Cause → effect.
- headline: ≤ 90 chars, punchy, no clichés like "game-changer".
- key_fact_keys: the FACT keys you relied on.
- Never mention odds, betting, prices or markets.
Return JSON matching the schema.
```
User template: `MOMENT {type, minute, significance, trigger}` · `FACTS` (key | label | value | unit) ·
`ROSTER` (team: players) · `RECENT SEQUENCE` · `SCORE`.

### Persona Editor — system
```
Rewrite one explained football moment for four audiences. Same facts, genuinely different experiences.
- analyst: precise and tactical; include at least 2 numbers from FACTS; neutral.
- casual: warm, plain English, no jargon (say "chance quality" not "xG"); at most 1 number; why should I care?
- club_fan (perspective_team_id given): openly partisan for that club. Joy or anguish, but still factual.
  Produce one club_fan variant for EACH team.
- commentary_line: one spoken line of at most 25 words, as a TV co-commentator would say it.
Rules: numbers only from FACTS, exactly as formatted; names only from ROSTER; no odds or betting language.
Return JSON: {"variants":[...4 InsightVariant objects...]}
```
(Define a small wrapper model `EditorOutput(variants: list[InsightVariant])` in `agents/schemas.py`.)

### Translator — system
```
Translate each variant into {language}. Keep tone and persona. Keep all numbers, player and team names
EXACTLY as in the source. Respect max lengths (headline 90, body 300, commentary_line 160). Return JSON.
```
Demo languages: `es`, `fr`, `pt`, `de` (skip RTL scripts to keep the UI simple). The Fact-Checker re-runs on translations.

### Player Lens — system
```
Retell this moment through the eyes of {player}. Use the player's live stats from FACTS
(touches, passes, accuracy, distance, top speed, rating) only if present. If the player was not directly
involved, explain what the moment means for their game. Same number/name rules.
```

### Match Reporter (full-time recap) — system
```
Write a full-time recap for {persona}. Structure: verdict in one sentence → the 3 turning points (use the
supplied moments in order, with minutes) → the standout performer → what the numbers say.
Title ≤ 80 chars, summary ≤ 1200 chars. Facts and names rules as above.
```

## 5. Fact-Checker (deterministic — `agents/factcheck.py`)
Runs on the Explanation and every variant (and every translation). Fail if any of these holds:
1. **Unknown number:** extract numeric tokens (`\d+(?:[.,]\d+)?`, also `67'` and `71%`). Each must match a fact's
   formatted value, the current score digits, or the moment minute. Allowed extras: 1, 2, 3 written as digits
   when they appear in an allow-list phrase (`"1-0"`-style score strings). Tolerance: exact string match after
   normalising decimal commas (for translations).
2. **Unknown fact key** in `key_fact_keys`.
3. **Unknown name:** capitalised tokens that match no roster short_name, full name or team name, from a
   denylist-driven check. Keep it simple: test against a set of all real-club names in `agents/denylist.py`
   (top-flight English clubs + "Premier League"), and require every roster surname mentioned to be in the
   match rosters.
4. **Banned language:** odds, bet, betting, price, market, bookmaker, wager (plus translations of these for the demo languages).
5. **Length** violations (pydantic will also catch these).

Outcome: pass → `PASSED`. Fail → one retry with the issues appended to the prompt ("Fix these issues: …") → pass →
`REPAIRED`. Still failing, an LLM error, or a timeout (> 8 s per call) → `FALLBACK` using templates. Every fallback is
logged with reasons (these are great for the demo: "self-correcting agents").

**LLM reviewer** (significance ≥ 0.6, env `FULCRUM_LLM_REVIEW=1`): "Does any sentence claim something not
supported by FACTS? Return {supported: bool, issues: [str]}". A failure feeds the same repair loop.

## 6. Template fallback (`agents/templates.py`)
One f-string per MomentType and persona, filled from facts only. Example (goal, casual):
`"{scorer} scores! {team} lead {score_after} and their chances of winning jump to {wp_scorer_after}%."`
Templates must always validate against the Fact-Checker; a unit test asserts this for every MomentType.

## 7. Evaluation (cheap, but show it)
`tests/agents/test_golden.py`: 10 golden moments (fixtures) with FakeLLM, asserting the flow. Plus
`scripts/eval_live.py`: run one full match against Foundry, then report the PASSED / REPAIRED / FALLBACK rates,
p50/p95 latency, and tokens. Put the numbers in the README; judges like evidence. If time allows, run
Foundry's built-in evaluators (groundedness) on a sample. That is a strong "Best use of Foundry" talking point.
