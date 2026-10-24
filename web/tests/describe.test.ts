// Audio description: deterministic, localised lines for key events (no LLM).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { describeEvent, momentPriority } from "../src/lib/describe.ts";
import { PRIORITY } from "../src/lib/speechQueue.ts";
import type { MatchEvent, Team } from "../src/types/generated.ts";

const teams: Team[] = JSON.parse(readFileSync(new URL("../../data/teams.json", import.meta.url), "utf8"));
const home = teams.find((t) => t.team_id === "NBA")!;
const away = teams.find((t) => t.team_id === "RVR")!;
const name = (id: string) => [...home.players, ...away.players].find((p) => p.player_id === id)!.short_name;

let seq = 0;
const ev = (e: Partial<MatchEvent>): MatchEvent => ({ match_id: "m", seq: seq++, period: 1, clock_ms: 0, type: "pass", ...e }) as MatchEvent;
const say = (e: MatchEvent, lang = "en") => describeEvent(e, { home, away, lang });

const goal = ev({ type: "goal", team_id: "RVR", player_id: "RVR-10", outcome: "goal", score_home: 0, score_away: 1, event_id: "g1" });

test("a goal names scorer, club and score, assertively and at top priority", () => {
  const d = say(goal)!;
  assert.equal(d.text, `Goal! ${name("RVR-10")} scores for ${away.name}. ${home.name} 0, ${away.name} 1.`);
  assert.equal(d.assertive, true);
  assert.equal(d.priority, PRIORITY.high);
  assert.equal(d.id, "g1");
});

test("the same goal in every language", () => {
  const lines = ["es", "fr", "pt", "de"].map((l) => say(goal, l)!.text);
  assert.match(lines[0], /^¡Gol! Marca /);
  assert.match(lines[1], /^But ! /);
  assert.match(lines[2], /^Gol! .* marca para /);
  assert.match(lines[3], /^Tor! .* trifft für /);
  for (const l of lines) assert.ok(l.includes(`${home.name} 0, ${away.name} 1`));
  assert.equal(say(goal, "xx")!.text, say(goal, "en")!.text, "unknown language falls back to English");
});

test("cards: yellow is polite, red (and second yellow) is assertive", () => {
  const y = say(ev({ type: "card", team_id: "NBA", player_id: "NBA-04", card: { colour: "yellow" } }))!;
  assert.equal(y.text, `Yellow card for ${name("NBA-04")}, ${home.name}.`);
  assert.equal(y.assertive, false);
  const r = say(ev({ type: "card", team_id: "NBA", player_id: "NBA-04", card: { colour: "second_yellow" } }), "de")!;
  assert.equal(r.text, `Rote Karte! Platzverweis für ${name("NBA-04")}, ${home.name}.`);
  assert.equal(r.priority, PRIORITY.high);
});

test("big chances that miss are described; ordinary shots and scored shots are not", () => {
  const shot = (outcome: MatchEvent["outcome"], big: boolean) =>
    ev({ type: "shot", team_id: "NBA", player_id: "NBA-09", outcome, shot: { distance_m: 9, angle_rad: 0.6, body_part: "head", xg: 0.4, shot_speed_kmh: 70, on_target: true, big_chance: big } });
  assert.equal(say(shot("saved", true))!.text, `Big chance for ${home.name}! ${name("NBA-09")}'s shot is saved.`);
  assert.match(say(shot("woodwork", true), "fr")!.text, /touche le poteau\.$/);
  assert.equal(say(shot("saved", false)), null);
  assert.equal(say(shot("goal", true)), null, "the goal event describes it");
});

test("substitutions, half time, second half and full time", () => {
  const sub = say(ev({ type: "substitution", team_id: "NBA", substitution: { player_off_id: "NBA-06", player_on_id: "NBA-15" } }), "es")!;
  assert.equal(sub.text, `Cambio en ${home.name}: entra ${name("NBA-15")} por ${name("NBA-06")}.`);
  assert.equal(sub.priority, PRIORITY.low);
  assert.equal(say(ev({ type: "period_end", period: 1, score_home: 1, score_away: 1 }))!.text, `Half-time. ${home.name} 1, ${away.name} 1.`);
  assert.equal(say(ev({ type: "period_end", period: 2 })), null, "full time is announced by match_end");
  assert.equal(say(ev({ type: "period_start", period: 2, score_home: 1, score_away: 1 }), "pt")!.text, `Começa o segundo tempo. ${home.name} 1, ${away.name} 1.`);
  assert.equal(say(ev({ type: "period_start", period: 1 }))!.text, `Kick-off. ${home.name} against ${away.name}.`);
  assert.equal(say(ev({ type: "match_end", period: 2, score_home: 2, score_away: 1 }), "de")!.text, `Abpfiff. ${home.name} 2, ${away.name} 1.`);
  assert.equal(say(ev({ type: "pass" })), null);
});

test("insight priority follows the moment type and matches the queue's priorities", () => {
  assert.equal(momentPriority("goal"), PRIORITY.high);
  assert.equal(momentPriority("red_card"), PRIORITY.high);
  assert.equal(momentPriority("big_chance"), PRIORITY.normal);
  assert.equal(momentPriority("tempo_change"), PRIORITY.low);
  assert.equal(momentPriority(undefined), PRIORITY.low);
});
