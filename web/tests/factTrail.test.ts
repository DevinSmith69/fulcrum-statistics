// Fact trail matching mirrors src/fulcrum/agents/factcheck.py. Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { buildFactIndex, canonical, describeFact, factSegments, numbersIn, traceCount } from "../src/lib/factTrail.ts";
import type { Fact } from "../src/types/generated.ts";

const facts: Fact[] = [
  { key: "minute", label: "Match minute", value: "67'" },
  { key: "score", label: "Score (Northbank first)", value: "2-1" },
  { key: "xg", label: "Shot xG", value: 0.34, unit: "xG" },
  { key: "wp_home_after", label: "Home win probability after", value: 71, unit: "%" },
  { key: "wp_home_delta", label: "Change in home win probability", value: -22, unit: "pp" },
  { key: "shot_distance_m", label: "Shot distance", value: 0.3, unit: "m" },
  { key: "crowd", label: "Attendance", value: 11240 },
  { key: "late_pressure", label: "Shots in the last 5 min", value: 4 },
];
const index = buildFactIndex(facts, { clockMs: 92 * 60_000 + 10_000, preferKeys: ["xg"] });
const traced = (text: string, lang = "en") =>
  factSegments(text, index, lang)
    .filter((s) => s.ref || s.unmatched)
    .map((s) => [s.text, s.ref?.fact.key ?? null]);

test("tokenises like factcheck.numbers_in", () => {
  assert.deepEqual(numbersIn("xG 0,34 at 67' and 71 %").map((t) => t.token), ["0,34", "67", "71"]);
  assert.deepEqual(numbersIn("90+3'").map((t) => t.value), ["90", "3"]);
  assert.deepEqual(numbersIn("11,240 fans").map((t) => t.value), ["11240"]);
  // "11.240" is a thousands group outside English only
  assert.deepEqual(numbersIn("11.240 Fans", "de").map((t) => t.value), ["11240"]);
  assert.deepEqual(numbersIn("11.240 fans", "en").map((t) => t.value), ["11.24"]);
  assert.equal(canonical("0,30"), "0.3");
  assert.equal(canonical("-22"), "22");
});

test("numbers in English copy link to their facts, with units kept in the highlight", () => {
  assert.deepEqual(traced("A 0.34 xG chance at 67' pushes them to 71% after a 22-point swing."), [
    ["0.34", "xg"],
    ["67'", "minute"],
    ["71%", "wp_home_after"],
    ["22", "wp_home_delta"],
  ]);
});

test("translated copy: decimal comma, spaced percent, trailing zero", () => {
  assert.deepEqual(traced("Ocasión de 0,34 xG en el 67'; ahora 71 % (a 0,30 m).", "es"), [
    ["0,34", "xg"],
    ["67'", "minute"],
    ["71 %", "wp_home_after"],
    ["0,30", "shot_distance_m"],
  ]);
});

test("scoreline, label numbers, stoppage-time minutes and the moment clock", () => {
  assert.deepEqual(traced("2-1 up, 4 shots in the last 5 min, 92' on the clock (90+3')"), [
    ["2", "score"],
    ["1", "score"],
    ["4", "late_pressure"],
    ["5", "late_pressure"],
    ["92'", "clock_ms"],
    ["90", null],
    ["3", null],
  ]);
});

test("unknown numbers are flagged, not linked", () => {
  assert.deepEqual(traced("xG of 0.41"), [["0.41", null]]);
  assert.deepEqual(traceCount(["0.34 then 0.41", "71%"], index), { traced: 2, total: 3 });
});

test("value facts beat label facts; preferred keys win ties", () => {
  const idx = buildFactIndex(
    [
      { key: "a", label: "Passes in 5 min", value: 9 },
      { key: "b", label: "Shots", value: 5 },
      { key: "c", label: "Corners", value: 5 },
    ],
    { preferKeys: ["c"] },
  );
  assert.equal(idx.get("5")?.fact.key, "c");
  assert.equal(idx.get("9")?.fact.key, "a");
  assert.equal(describeFact(facts[2]), "Shot xG · 0.34 xG · key xg");
  assert.equal(describeFact(facts[3]), "Home win probability after · 71% · key wp_home_after");
});
