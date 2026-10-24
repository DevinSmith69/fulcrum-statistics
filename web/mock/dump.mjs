// Dev helper: dump every payload the mock would send, as JSONL {"model", "data"}, for contract checks.
// Usage: node mock/dump.mjs > out.jsonl   (then validate with the pydantic models, see web/README.md)
import { readFileSync } from "node:fs";
import { buildMatch } from "./match.mjs";
import { buildCues, buildInsight, buildRecap, pickVariant, PERSONAS, LANGUAGES } from "./text.mjs";

const teams = JSON.parse(readFileSync(new URL("../../data/teams.json", import.meta.url), "utf8"));
const [home, away] = [teams[0], teams[1]];
const m = buildMatch({ home, away, seed: 7, matchId: "dump" });
const all = [...home.players, ...away.players];
const ctx = {
  home, away,
  teamName: (id) => (id === home.team_id ? home.name : id === away.team_id ? away.name : null),
  opponentId: (id) => (id === home.team_id ? away.team_id : id === away.team_id ? home.team_id : null),
  opponentName: (id) => (id === home.team_id ? away.name : id === away.team_id ? home.name : null),
  player: (id) => all.find((p) => p.player_id === id) ?? null,
};
const out = (model, data) => process.stdout.write(JSON.stringify({ model, data }) + "\n");
const MODEL = { event: "MatchEvent", state: "MatchState", winprob: "WinProbPoint", moment: "Moment" };
for (const it of m.items) out(MODEL[it.kind], it.data);
let n = 0;
for (const mo of m.moments) {
  const ins = buildInsight(mo, ctx, { id: (n++).toString(16).padStart(32, "0"), latencyMs: 2000, status: "passed" });
  out("Insight", ins);
  for (const persona of PERSONAS) for (const language of LANGUAGES) {
    const prefs = { persona, language, favourite_team_id: away.team_id, followed_player_id: "NBA-10" };
    const v = pickVariant(ins, mo, prefs, ctx);
    out("InsightVariant", v);
    for (const c of buildCues(mo, ins, v, ctx, { showAtClockMs: mo.clock_ms + 30000, syncSeq: mo.seq_end, newId: () => "c" })) out("OverlayCue", c);
    if (mo.type === "full_time") out("Recap", buildRecap(mo, m.moments, ctx, prefs));
  }
}
