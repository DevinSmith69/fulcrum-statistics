import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error — plain ESM module without types
import { H1_LEN, minuteLabel as mockMinute } from "../mock/match.mjs";
import { matchClock, minuteLabel } from "../src/lib/format.ts";

const MIN = 60_000;

test("mock labels second-half minutes from the H2 kick-off, not as first-half stoppage", () => {
  const clock = H1_LEN + 42 * MIN + 30_000; // 42.5 min into H2
  assert.equal(mockMinute(2, clock), "88'");
  assert.equal(mockMinute(2, H1_LEN + 46 * MIN + 10_000), "90+2'");
  assert.equal(mockMinute(1, 46 * MIN + 10_000), "45+2'");
});

test("UI scoreboard shows second-half time when the match is joined mid-H2", () => {
  const starts = { "2": H1_LEN };
  const clock = H1_LEN + 42 * MIN + 30_000;
  assert.deepEqual(matchClock(clock, 2, starts), { main: "87:30", added: null });
  assert.equal(minuteLabel(clock, 2, starts), "88'");
});
