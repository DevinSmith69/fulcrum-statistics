import assert from "node:assert/strict";
import { test } from "node:test";
import { citedFacts, coachCopy, coachFactLine, coachFacts, coachPath, hasCopy, isCoachable, type CoachView } from "../src/lib/coach.ts";
import type { Fact, Moment } from "../src/types/generated.ts";

const F = (key: string, value: number | string, unit: string | null = null, label = key): Fact => ({ key, label, value, unit }) as Fact;
const moment = (type: string, facts: Fact[]) => ({ type, facts }) as unknown as Moment;

const COACH = [
  F("coach_defending_team", "Marsh Dock United", null, "Defending team"),
  F("coach_move_origin", "misplaced pass", null, "How the attacking move started"),
  F("coach_misplaced_pass_expected_completion_pct", 31, "%", "Expected completion of the misplaced pass"),
  F("coach_move_seconds", 46, "s", "Seconds from the start of the move to the shot"),
  F("coach_chance_xg", 0.11, "xG", "Expected goals of the chance conceded"),
];

test("only goals and big chances with coach_ facts are coachable", () => {
  assert.equal(isCoachable(moment("goal", [F("minute", "66'"), ...COACH])), true);
  assert.equal(isCoachable(moment("big_chance", COACH)), true);
  assert.equal(isCoachable(moment("goal", [F("xg", 0.11)])), false);
  assert.equal(isCoachable(moment("red_card", COACH)), false);
  assert.equal(isCoachable(moment("goal", null as unknown as Fact[])), false);
  assert.deepEqual(coachFacts([F("xg", 0.1), ...COACH]).map((f) => f.key), COACH.map((f) => f.key));
});

test("endpoint path encodes ids and the language", () => {
  assert.equal(coachPath("m 1", "i/2", "pt-BR"), "/api/matches/m%201/insights/i%2F2/coach?language=pt-BR");
});

test("fallback bullets read label: value with units", () => {
  assert.deepEqual(COACH.slice(1).map(coachFactLine), [
    "How the attacking move started: misplaced pass",
    "Expected completion of the misplaced pass: 31%",
    "Seconds from the start of the move to the shot: 46 s",
    "Expected goals of the chance conceded: 0.11 xG",
  ]);
});

const view = (over: Partial<CoachView> = {}): CoachView => ({
  match_id: "m", insight_id: "i", moment_id: "mo", moment_type: "goal", defending_team_id: "MDU", language: "en",
  check_status: "passed", latency_ms: 900, takeaway: "When the ball is lost in midfield, the first pass matters.", facts: COACH,
  points: [
    { text: "The move began with a misplaced pass, one completed 31% of the time.", fact_keys: ["coach_move_origin", "coach_misplaced_pass_expected_completion_pct"] },
    { text: "It took 46 seconds to reach the shot.", fact_keys: ["coach_move_seconds", "coach_move_origin"] },
  ],
  ...over,
});

test("'based on' lists the cited facts once, in engine order", () => {
  assert.deepEqual(citedFacts(view()).map((f) => f.key), ["coach_move_origin", "coach_misplaced_pass_expected_completion_pct", "coach_move_seconds"]);
});

test("a fallback view (or one without points) shows the facts instead of copy", () => {
  assert.equal(hasCopy(view()), true);
  assert.equal(hasCopy(view({ check_status: "repaired" })), true);
  assert.equal(hasCopy(view({ check_status: "fallback", points: [], takeaway: null })), false);
  assert.equal(hasCopy(view({ points: [] })), false);
});

test("panel copy is translated, falls back to English and contains no digits", () => {
  assert.equal(coachCopy("es", "button"), "Visión del entrenador");
  assert.equal(coachCopy("de-AT", "button"), "Trainerblick");
  assert.equal(coachCopy("xx", "button"), "Coach's view");
  for (const lang of ["en", "es", "fr", "pt", "de"]) {
    for (const key of ["button", "title", "loading", "basedOn", "fallback", "failed", "retry"]) {
      assert.doesNotMatch(coachCopy(lang, key), /\d/, `${lang}.${key}`);
    }
  }
});
