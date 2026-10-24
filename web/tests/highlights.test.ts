// "Your highlights" reel: playlist, running score, copy and the step state machine. Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  buildReel,
  initialReel,
  pickReelMoments,
  reelReducer,
  runningScores,
  shortLine,
  speechBudgetMs,
  stepCapMs,
  REPLAY_BUDGET_MS,
  type ReelAction,
  type ReelState,
} from "../src/lib/highlights.ts";
import type { Insight, Moment, MomentType, Recap } from "../src/types/generated.ts";

const WP = { home: 0.4, draw: 0.3, away: 0.3 };
let n = 0;
const mo = (type: MomentType, seq: number, significance: number, extra: Partial<Moment> = {}): Moment => ({
  moment_id: `m${++n}`, match_id: "x", type, significance, period: seq < 500 ? 1 : 2, clock_ms: seq * 6000,
  seq_start: seq - 3, seq_end: seq, team_id: "NBA", player_ids: [], wp_before: WP, wp_after: WP, facts: [], trigger: "t",
  ...extra,
});

function match() {
  n = 0;
  return [
    mo("big_chance", 100, 0.7), // m1
    mo("goal", 200, 0.9, { facts: [{ key: "score_after", label: "Score", value: "1-0" }] }), // m2
    mo("player_milestone", 200, 0.6), // m3 — same run as the goal
    mo("pressure_shift", 300, 0.3), // m4
    mo("half_time", 450, 0.5), // m5
    mo("wp_swing", 600, 0.55), // m6
    mo("red_card", 700, 0.8, { team_id: "RVR" }), // m7
    mo("goal", 800, 0.95, { team_id: "RVR" }), // m8 — no score fact: counted from team_id
    mo("speed_record", 850, 0.2), // m9
    mo("elite_pass", 860, 0.4), // m10
    mo("tempo_change", 870, 0.35), // m11
    mo("full_time", 900, 1), // m12
  ];
}

const recap = (ids: string[]): Recap => ({ match_id: "x", persona: "casual", summary: "s", key_moment_ids: ids, check_status: "passed" });
const ids = (ms: Moment[]) => ms.map((m) => m.moment_id);

test("recap key moments plus every goal and red card, in match order, one per run of play", () => {
  const ms = match();
  // m3 (brace milestone) shares the goal's run; m5 is half-time; "nope" is unknown.
  assert.deepEqual(ids(pickReelMoments(ms, recap(["m6", "m3", "m5", "m1", "nope"]))), ["m1", "m2", "m6", "m7", "m8"]);
});

test("a long recap list is cut to the most significant key moments, never cutting goals or reds", () => {
  const ms = match();
  const all = ms.map((m) => m.moment_id!);
  assert.deepEqual(ids(pickReelMoments(ms, recap(all), 4)), ["m1", "m2", "m7", "m8"]);
  // Only key moments fill the free places (m10 is more significant than m11 but the recap did not pick it).
  assert.deepEqual(ids(pickReelMoments(ms, recap(["m11", "m9"]), 5)), ["m2", "m7", "m8", "m9", "m11"]);
});

test("missing recap (or unknown ids) falls back to the top moments by significance, goals and reds always in", () => {
  const ms = match();
  const fb = ids(pickReelMoments(ms, null));
  assert.deepEqual(fb, ["m1", "m2", "m6", "m7", "m8", "m10"]);
  assert.deepEqual(ids(pickReelMoments(ms, recap(["zzz"]))), fb);
  // Goals and reds survive even when they alone exceed the cap.
  assert.deepEqual(ids(pickReelMoments(ms, null, 2)), ["m2", "m7", "m8"]);
});

test("the playlist is deterministic whatever order the moments arrived in", () => {
  const ms = match();
  assert.deepEqual(ids(pickReelMoments([...ms].reverse(), null)), ids(pickReelMoments(ms, null)));
});

test("running score uses the score_after fact, else the scoring team", () => {
  const s = runningScores(match(), "NBA");
  assert.deepEqual(s.get("m1"), { before: { home: 0, away: 0 }, after: { home: 0, away: 0 } });
  assert.deepEqual(s.get("m2"), { before: { home: 0, away: 0 }, after: { home: 1, away: 0 } });
  assert.deepEqual(s.get("m8"), { before: { home: 1, away: 0 }, after: { home: 1, away: 1 } });
  assert.deepEqual(s.get("m12")?.after, { home: 1, away: 1 });
});

test("copy comes from the viewer's insight; without one, the template label and no speech", () => {
  const ms = match();
  const ins: Insight = {
    match_id: "x", moment_id: "m2", clock_ms: 0, check_status: "passed",
    explanation: { why_it_matters: "w", numbers_used: [] } as unknown as Insight["explanation"],
    variants: [{ persona: "casual", headline: "Okafor breaks the deadlock", body: "A 0.31 xG chance. The rest is detail.", commentary_line: "Okafor scores!" }],
  };
  const reel = buildReel({ moments: ms, recap: null, insights: { m2: ins }, homeTeamId: "NBA", label: (m) => `T:${m.type}` });
  const goal = reel.find((r) => r.moment.moment_id === "m2")!;
  assert.equal(goal.headline, "Okafor breaks the deadlock");
  assert.equal(goal.line, "A 0.31 xG chance.");
  assert.equal(goal.speech, "Okafor scores!");
  assert.deepEqual(goal.scoreAfter, { home: 1, away: 0 });
  const chance = reel.find((r) => r.moment.moment_id === "m1")!;
  assert.deepEqual([chance.headline, chance.line, chance.speech, chance.fromInsight], ["T:big_chance", null, null, false]);
});

test("shortLine keeps the first sentence and trims long ones on a word boundary", () => {
  assert.equal(shortLine("One. Two."), "One.");
  assert.equal(shortLine("  "), null);
  const long = shortLine("word ".repeat(60), 40)!;
  assert.ok(long.length <= 40 && long.endsWith("…") && !long.includes("  "));
});

test("the safety timeout covers the replay and the spoken line", () => {
  assert.equal(stepCapMs({ speech: null }, false), REPLAY_BUDGET_MS);
  const long = "word ".repeat(60);
  assert.ok(stepCapMs({ speech: long }, false) > speechBudgetMs(long));
  assert.equal(stepCapMs({ speech: long }, true), REPLAY_BUDGET_MS);
});

const run = (s: ReelState, ...as: ReelAction[]) => as.reduce(reelReducer, s);

test("intro → moments → outro; a moment advances only when replay AND speech are done", () => {
  let s = run(initialReel(2), { type: "intro_done", step: 0 });
  assert.deepEqual([s.phase, s.index], ["moment", 0]);
  s = run(s, { type: "replay_done", step: s.step });
  assert.deepEqual([s.phase, s.index], ["moment", 0]);
  s = run(s, { type: "speech_done", step: s.step });
  assert.deepEqual([s.phase, s.index], ["moment", 1]);
  s = run(s, { type: "speech_done", step: s.step }, { type: "replay_done", step: s.step });
  assert.equal(s.phase, "outro");
  assert.equal(run(s, { type: "next" }).phase, "outro");
});

test("late completions from an earlier step are ignored", () => {
  const s = run(initialReel(3), { type: "intro_done", step: 0 });
  const stale = s.step - 1;
  assert.deepEqual(run(s, { type: "replay_done", step: stale }, { type: "speech_done", step: stale }, { type: "timeout", step: stale }), s);
});

test("pause holds a finished moment; resume moves on; the timeout does not fire while paused", () => {
  let s = run(initialReel(2), { type: "intro_done", step: 0 }, { type: "toggle_pause" });
  const step = s.step;
  s = run(s, { type: "replay_done", step }, { type: "speech_done", step }, { type: "timeout", step });
  assert.deepEqual([s.phase, s.index, s.paused], ["moment", 0, true]);
  s = run(s, { type: "toggle_pause" });
  assert.deepEqual([s.phase, s.index, s.paused], ["moment", 1, false]);
});

test("pause and resume mid-moment keep the step (the replay is not restarted)", () => {
  const s = run(initialReel(2), { type: "intro_done", step: 0 });
  const t = run(s, { type: "toggle_pause" }, { type: "toggle_pause" });
  assert.equal(t.step, s.step);
  assert.equal(t.paused, false);
});

test("next / prev skip around; prev from the outro returns to the last moment", () => {
  let s = run(initialReel(3), { type: "next" }, { type: "next" });
  assert.deepEqual([s.phase, s.index], ["moment", 1]);
  s = run(s, { type: "prev" }, { type: "prev" });
  assert.equal(s.phase, "intro");
  s = run(s, { type: "next" }, { type: "next" }, { type: "next" }, { type: "next" });
  assert.equal(s.phase, "outro");
  s = run(s, { type: "prev" });
  assert.deepEqual([s.phase, s.index], ["moment", 2]);
  s = run(s, { type: "restart" });
  assert.deepEqual([s.phase, s.index, s.paused], ["intro", 0, false]);
});

test("an empty reel goes straight from the intro to the outro", () => {
  assert.equal(run(initialReel(0), { type: "intro_done", step: 0 }).phase, "outro");
});

test("timeout forces the next moment when speech never reports back", () => {
  let s = run(initialReel(2), { type: "intro_done", step: 0 });
  s = run(s, { type: "replay_done", step: s.step }, { type: "timeout", step: s.step });
  assert.deepEqual([s.phase, s.index], ["moment", 1]);
});
