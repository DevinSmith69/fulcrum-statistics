// Run with `npm test` (node:test + type stripping; no browser needed).
import assert from "node:assert/strict";
import { test } from "node:test";
import { emptyStage, mergeCueLog, offer, tick, EXIT_MS } from "../src/lib/cueStage.ts";
import type { OverlayCue } from "../src/types/generated.ts";

const cue = (id: string, anchor: OverlayCue["anchor"], priority: number, duration = 6000): OverlayCue => ({
  cue_id: id, match_id: "m", template: "stat_card", anchor, priority, sync_clock_ms: 0, sync_seq: 0,
  show_at_clock_ms: 0, duration_ms: duration, persona: "casual", headline: id,
});

test("a cue on a free anchor shows immediately", () => {
  const s = offer(emptyStage(), cue("a", "top_left", 3), 0);
  assert.equal(s.active.top_left?.cue.cue_id, "a");
});

test("higher priority pre-empts the cue on the same anchor", () => {
  let s = offer(emptyStage(), cue("low", "bottom_center", 3), 0);
  s = offer(s, cue("goal", "bottom_center", 5), 100);
  assert.equal(s.active.bottom_center?.cue.cue_id, "goal");
  assert.deepEqual(s.leaving.map((l) => l.cue.cue_id), ["low"]);
});

test("equal priority queues and shows after the current cue expires", () => {
  let s = offer(emptyStage(), cue("first", "top_right", 4, 1000), 0);
  s = offer(s, cue("second", "top_right", 4, 1000), 10);
  assert.equal(s.active.top_right?.cue.cue_id, "first");
  assert.equal(s.queue.top_right?.length, 1);
  s = tick(s, 1001);
  assert.equal(s.active.top_right?.cue.cue_id, "second");
});

test("different anchors never block each other", () => {
  let s = offer(emptyStage(), cue("a", "top_left", 2), 0);
  s = offer(s, cue("b", "bottom_right", 2), 0);
  assert.ok(s.active.top_left && s.active.bottom_right);
});

test("leaving cues are cleaned up after the exit animation", () => {
  let s = offer(emptyStage(), cue("a", "top_left", 2, 1000), 0);
  s = tick(s, 1000);
  assert.equal(s.leaving.length, 1);
  s = tick(s, 1000 + EXIT_MS + 1);
  assert.equal(s.leaving.length, 0);
  assert.equal(s.active.top_left, undefined);
});

test("inspector log: history + live, one entry per cue (live wins), oldest first", () => {
  const a = { ...cue("a", "lower_third", 1), show_at_clock_ms: 2000 };
  const b = { ...cue("b", "top_left", 1), show_at_clock_ms: 1000 };
  const liveA = { ...a, headline: "live" };
  const c = { ...cue("c", "top_left", 1), show_at_clock_ms: 3000 };
  const log = mergeCueLog([a, b], [liveA, c]);
  assert.deepEqual(log.map((x) => x.cue_id), ["b", "a", "c"]);
  assert.equal(log[1].headline, "live");
});
