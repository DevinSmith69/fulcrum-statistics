// Mini replay window selection. Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { appendEvents, isReplayable, replayWindow, stepMs } from "../src/lib/replay.ts";
import type { MatchEvent } from "../src/types/generated.ts";

let seq = 0;
const ev = (type: MatchEvent["type"], possession: number, extra: Partial<MatchEvent> = {}): MatchEvent => ({
  match_id: "m", seq: seq++, period: 1, clock_ms: seq * 3000, type, team_id: "NBA", player_id: "p",
  possession_id: possession, x: 50, y: 30, ...extra,
});

function log() {
  seq = 0;
  return [
    ev("period_start", 0, { x: null, y: null }), // 0
    ev("kick_off", 1), // 1
    ev("pass", 1), // 2
    ev("pass", 1), // 3
    ev("interception", 2), // 4
    ev("pass", 2), // 5
    ev("pass", 2), // 6
    ev("carry", 2), // 7
    ev("pass", 2), // 8
    ev("shot", 2), // 9
    ev("goal", 2, { x: null, y: null }), // 10
    ev("kick_off", 3), // 11
  ];
}

test("a goal replays the whole possession that produced it, not the restart after", () => {
  const w = replayWindow(log(), { seq_start: 9, seq_end: 10, period: 1 }, { min: 3 });
  assert.deepEqual(w.map((e) => e.seq), [4, 5, 6, 7, 8, 9]);
});

test("short runs are padded back to the minimum, never past a kick-off", () => {
  const w = replayWindow(log(), { seq_start: 5, seq_end: 6, period: 1 }, { min: 10 });
  assert.deepEqual(w.map((e) => e.seq), [1, 2, 3, 4, 5, 6]);
});

test("long runs keep the latest events; other periods are excluded", () => {
  const events = log();
  assert.deepEqual(replayWindow(events, { seq_start: 0, seq_end: 9, period: 1 }, { max: 3 }).map((e) => e.seq), [7, 8, 9]);
  assert.deepEqual(replayWindow(events, { seq_start: 0, seq_end: 9, period: 2 }), []);
  assert.deepEqual(replayWindow([], { seq_start: 0, seq_end: 9, period: 1 }), []);
});

test("timing keeps a replay in the 6–12 s range", () => {
  for (const n of [3, 5, 8, 14]) {
    const total = n * stepMs(n);
    assert.ok(total >= 1950 && total <= 12_000, `${n} events → ${total} ms`);
  }
  assert.ok(5 * stepMs(5) >= 6000);
  assert.equal(isReplayable({ type: "full_time" }), false);
  assert.equal(isReplayable({ type: "goal" }), true);
});

test("appending a fetched tail de-duplicates on seq", () => {
  const events = log();
  const merged = appendEvents(events.slice(0, 5), events.slice(3));
  assert.deepEqual(merged.map((e) => e.seq), events.map((e) => e.seq));
});
