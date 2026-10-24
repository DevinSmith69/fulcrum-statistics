import assert from "node:assert/strict";
import { test } from "node:test";
import { whatIfLabel, whatIfMarkers } from "../src/lib/whatIf.ts";
import type { Fact, Moment } from "../src/types/generated.ts";

const F = (key: string, value: number | string): Fact => ({ key, label: key, value });
const moment = (type: string, team_id: string | null, facts: Fact[], clock_ms = 540_000): Moment =>
  ({ moment_id: `${type}-${clock_ms}`, match_id: "m", type, team_id, clock_ms, period: 1, facts }) as unknown as Moment;
const WHATIF = [F("whatif_win_pct_now", 31), F("whatif_goal_win_pct", 54), F("whatif_goal_score", "0-1")];

test("a big chance with what-if facts becomes a marker on the shooting team's line", () => {
  const [m] = whatIfMarkers([moment("big_chance", "RVR", WHATIF)], "NBA", "RVR");
  assert.deepEqual(m, { momentId: "big_chance-540000", clock: 540_000, side: "away", now: 31, ifScored: 54, score: "0-1" });
  assert.equal(whatIfMarkers([moment("big_chance", "NBA", WHATIF)], "NBA", "RVR")[0].side, "home");
});

test("other moment types, missing facts or unknown teams give no marker", () => {
  assert.deepEqual(whatIfMarkers([moment("goal", "RVR", WHATIF)], "NBA", "RVR"), []);
  assert.deepEqual(whatIfMarkers([moment("big_chance", "RVR", [F("xg", 0.42)])], "NBA", "RVR"), []);
  assert.deepEqual(whatIfMarkers([moment("big_chance", "XYZ", WHATIF)], "NBA", "RVR"), []);
  assert.deepEqual(whatIfMarkers([moment("big_chance", null, WHATIF)], "NBA", "RVR"), []);
  assert.deepEqual(whatIfMarkers([moment("big_chance", "RVR", [F("whatif_win_pct_now", "31"), F("whatif_goal_win_pct", 54)])], "NBA", "RVR"), []);
});

test("tooltip reads as a counterfactual", () => {
  const [m] = whatIfMarkers([moment("big_chance", "RVR", WHATIF)], "NBA", "RVR");
  assert.equal(whatIfLabel(m, "Riverside"), "Riverside if it had gone in: 54%, 0-1 (now 31%)");
  assert.equal(whatIfLabel({ ...m, score: null }, null), "If it had gone in: 54% (now 31%)");
});
