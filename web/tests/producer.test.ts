import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ageTone,
  changedVariants,
  cleanRequest,
  formatAge,
  graphicSuggestions,
  MAX_REQUEST,
  orderVariants,
  previewCue,
  producerErrorMessage,
  slotKey,
  standoutPlayer,
  underdog,
  variantLabel,
} from "../src/lib/producer.ts";
import type { InsightVariant, MatchState, OverlayCue, Team } from "../src/types/index.ts";

const player = (id: string, short: string) => ({ player_id: id, name: `X ${short}`, short_name: short, shirt_number: 1, position: "MF", role: "CM" });
const team = (id: string, name: string, shorts: string[]): Team =>
  ({ team_id: id, name, short_name: id, players: shorts.map((s, i) => player(`${id}-${String(i + 1).padStart(2, "0")}`, s)) }) as unknown as Team;
const HOME = team("NBA", "Northbridge Athletic", ["A", "B", "C", "D", "E", "F", "G", "H", "I", "Brennick"]);
const AWAY = team("RVR", "Riverside Rovers", ["Venn", "Orrell"]);

const v = (persona: string, extra: Partial<InsightVariant> = {}): InsightVariant =>
  ({ persona, language: "en", headline: `${persona} h`, body: `${persona} b.`, commentary_line: "c", ...extra }) as InsightVariant;

const state = (players: Record<string, number>, score: [number, number] = [0, 0]) =>
  ({
    score_home: score[0],
    score_away: score[1],
    players: Object.fromEntries(Object.entries(players).map(([id, passes]) => [id, { player_id: id, passes_completed: passes }])),
  }) as unknown as MatchState;

test("ages are compact and get more urgent", () => {
  assert.equal(formatAge(4.7), "4 s");
  assert.equal(formatAge(65), "1:05");
  assert.equal(formatAge(-3), "0 s");
  assert.deepEqual([ageTone(3), ageTone(20), ageTone(50)], ["fresh", "waiting", "stale"]);
});

test("variant slots, labels and order", () => {
  const fanAway = v("club_fan", { perspective_team_id: "RVR" });
  const fanHome = v("club_fan", { perspective_team_id: "NBA" });
  assert.equal(slotKey(fanAway), "club_fan|RVR||en");
  assert.equal(variantLabel(fanHome, HOME, AWAY), "Fan · Northbridge Athletic");
  assert.equal(variantLabel(v("analyst"), HOME, AWAY), "Analyst");
  const ordered = orderVariants([fanAway, v("casual"), fanHome, v("analyst")], "NBA");
  assert.deepEqual(ordered.map((x) => `${x.persona}${x.perspective_team_id ?? ""}`), ["analyst", "casual", "club_fanNBA", "club_fanRVR"]);
});

test("only changed variants are sent, trimmed", () => {
  const original = [v("analyst"), v("casual")];
  const draft = { [slotKey(original[1])]: { headline: "  New casual h  " }, [slotKey(original[0])]: { body: "analyst b." } };
  const changed = changedVariants(original, draft);
  assert.equal(changed.length, 1);
  assert.equal(changed[0].persona, "casual");
  assert.equal(changed[0].headline, "New casual h");
  assert.equal(changed[0].body, "casual b.");
});

test("requests are cleaned and bounded", () => {
  assert.equal(cleanRequest("  pressure   bar\nnow "), "pressure bar now");
  assert.equal(cleanRequest(" "), null);
  assert.equal(cleanRequest("x".repeat(MAX_REQUEST + 1)), null);
});

test("suggestions use this match: busiest passer, the team behind, then fixed chips", () => {
  const s = state({ "NBA-10": 12, "RVR-02": 30 }, [1, 0]);
  assert.equal(standoutPlayer(s, HOME, AWAY), "Orrell");
  assert.equal(underdog(s, HOME, AWAY)?.name, "Riverside Rovers");
  assert.equal(underdog(state({}, [0, 2]), HOME, AWAY)?.name, "Northbridge Athletic");
  const chips = graphicSuggestions(s, HOME, AWAY);
  assert.equal(chips.length, 4);
  assert.deepEqual(chips, [
    "Stat card on Orrell's passing",
    "Pressure bar now",
    "Lower third: Riverside Rovers's fightback",
    "Win probability graphic",
  ]);
  // before kick-off: no passes yet, fall back to the home number-ten slot
  assert.equal(standoutPlayer(state({}), HOME, AWAY), "Brennick");
  assert.equal(graphicSuggestions(null, null, null)[0], "Stat card on the top passer");
});

test("preview keeps the cue on screen and errors read well", () => {
  const cue = { duration_ms: 8000, headline: "h" } as OverlayCue;
  assert.equal(previewCue(cue).duration_ms, 30000);
  assert.equal(cue.duration_ms, 8000);
  assert.match(producerErrorMessage("401 producer key required"), /Producer key required/);
  assert.match(producerErrorMessage("503 no agents"), /graphics desk is offline/);
  assert.match(producerErrorMessage("Failed to fetch"), /Couldn't reach the API/);
});
