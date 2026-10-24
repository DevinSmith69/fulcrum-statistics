// Dev helper: print a summary of the mock match (event counts, stats, moments) for tuning.
// Usage: node mock/inspect.mjs [seed]
import { readFileSync } from "node:fs";
import { buildMatch } from "./match.mjs";

const teams = JSON.parse(readFileSync(new URL("../../data/teams.json", import.meta.url), "utf8"));
const seed = Number(process.argv[2] ?? 7);
const t0 = Date.now();
const m = buildMatch({ home: teams[0], away: teams[1], seed, matchId: "inspect" });
console.log(`built in ${Date.now() - t0} ms`);
const events = m.items.filter((i) => i.kind === "event").map((i) => i.data);
const counts = {};
for (const e of events) counts[e.type] = (counts[e.type] ?? 0) + 1;
console.log("events", events.length, counts);
const s = m.finalState;
for (const k of ["home", "away"]) {
  const t = s[k];
  console.log(k, {
    goals: t.goals, xg: t.xg, shots: t.shots, sot: t.shots_on_target, passes: t.passes, acc: t.pass_accuracy,
    poss: t.possession_pct, corners: t.corners, fouls: t.fouls, yc: t.yellow_cards, rc: t.red_cards,
  });
}
console.log("states", m.items.filter((i) => i.kind === "state").length);
console.log("moments", m.moments.length);
for (const mo of m.moments) {
  const f = Object.fromEntries(mo.facts.map((x) => [x.key, x.value]));
  const extra = mo.facts.slice(4).map((x) => `${x.key}=${x.value}`).join(" ");
  console.log(String(f.minute).padStart(6), mo.type.padEnd(16), mo.significance.toFixed(2), (mo.team_id ?? "-").padEnd(4), extra.slice(0, 160));
}
