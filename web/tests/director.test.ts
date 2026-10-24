// Demo director helpers and the main-pitch replay pick. Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { controlStatus, demoFixture, demoRequested, matchUrl, speedLabel, stepSpeed, type ControlState } from "../src/lib/director.ts";
import { latestReplayable } from "../src/lib/replay.ts";

test("speed steps through the presets and stops at either end", () => {
  assert.equal(stepSpeed(10, 1), 30);
  assert.equal(stepSpeed(10, -1), 5);
  assert.equal(stepSpeed(60, 1), 60);
  assert.equal(stepSpeed(1, -1), 1);
  assert.equal(stepSpeed(20, 1), 30); // an off-preset speed (set via the API) snaps to the next preset
  assert.equal(stepSpeed(20, -1), 10);
  assert.equal(speedLabel(10), "10×");
  assert.equal(speedLabel(0.5), "0.5×");
});

test("?demo=1 turns the bar on; the demo fixture is seed 11 HLC v MDU", () => {
  assert.equal(demoRequested("?demo=1"), true);
  assert.equal(demoRequested("?match=x&demo"), true);
  assert.equal(demoRequested("?demo=0"), false);
  assert.equal(demoRequested(""), false);
  assert.deepEqual(demoFixture(30), { home_team_id: "HLC", away_team_id: "MDU", seed: 11, speed: 30 });
});

test("a new demo match keeps the view (hash) and demo mode", () => {
  const url = new URL(matchUrl("https://fulcrum.example/?match=old&persona=x#/broadcast", "new-id"));
  assert.equal(url.searchParams.get("match"), "new-id");
  assert.equal(url.searchParams.get("demo"), "1");
  assert.equal(url.searchParams.get("persona"), "x");
  assert.equal(url.hash, "#/broadcast");
});

const ctl = (over: Partial<ControlState> = {}): ControlState => ({
  match_id: "m", available: true, speed: 10, effective_speed: 10, paused: false, skipping: false,
  skip_min_significance: null, position_ms: 900_000, finished: false, last_skip: null, ...over,
});

test("status line", () => {
  assert.equal(controlStatus(null), "");
  assert.equal(controlStatus(ctl()), "Live");
  assert.equal(controlStatus(ctl({ paused: true })), "Paused");
  assert.equal(controlStatus(ctl({ skipping: true, paused: true })), "Finding the next big moment…");
  assert.equal(controlStatus(ctl({ finished: true })), "Full time");
  const skip = { moment_id: "x", type: "big_chance" as const, clock_ms: 870_000, significance: 0.68 };
  assert.equal(controlStatus(ctl({ last_skip: skip })), "Stopped at big chance");
  assert.equal(controlStatus(ctl({ last_skip: skip }), () => "Big chance"), "Stopped at Big chance");
  assert.equal(controlStatus(ctl({ last_skip: skip, position_ms: 1_000_000 })), "Live"); // a while ago now
});

test("R replays the newest big moment, else the newest replayable one", () => {
  const m = (type: string, significance: number, id: string) => ({ type, significance, id }) as never as { type: "goal"; significance: number; id: string };
  const moments = [m("goal", 0.98, "g1"), m("phase_change", 0.35, "p"), m("half_time", 0.5, "ht")];
  assert.equal(latestReplayable(moments)?.id, "g1");
  assert.equal(latestReplayable(moments.slice(1))?.id, "p");
  assert.equal(latestReplayable([m("full_time", 1, "ft")]), null);
  assert.equal(latestReplayable([]), null);
});
