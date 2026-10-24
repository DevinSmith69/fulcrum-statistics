import assert from "node:assert/strict";
import { test } from "node:test";
import { pickMatch, requestedMatchId } from "../src/lib/matchPick.ts";

const m = (match_id: string, status: string) => ({ match_id, status }) as never;
const matches = [m("old", "finished"), m("a", "live"), m("b", "live")];

test("?match= wins when the API knows the match", () => {
  assert.equal(pickMatch(matches, "b"), "b");
  assert.equal(pickMatch(matches, "old"), "old");
});

test("unknown or missing ?match= falls back to the first unfinished match", () => {
  assert.equal(pickMatch(matches, "nope"), "a");
  assert.equal(pickMatch(matches, null), "a");
  assert.equal(pickMatch([m("x", "finished"), m("y", "finished")]), "y");
  assert.equal(pickMatch([]), null);
});

test("reads the match id from the query string", () => {
  assert.equal(requestedMatchId("?match=abc&persona=casual"), "abc");
  assert.equal(requestedMatchId(""), null);
});
