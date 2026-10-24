import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fixtureColours, matchClock, minuteLabel, seriesColour } from "../src/lib/format.ts";
import { PitchModel, attacksRight, toAbs } from "../src/lib/pitchModel.ts";
import type { MatchEvent, Team } from "../src/types/generated.ts";

const teams: Team[] = JSON.parse(readFileSync(new URL("../../data/teams.json", import.meta.url), "utf8"));
const [NBA, RVR] = teams;

test("home attacks right in period 1 and left in period 2", () => {
  assert.equal(attacksRight("home", 1), true);
  assert.equal(attacksRight("away", 1), false);
  assert.equal(attacksRight("home", 2), false);
  assert.deepEqual(toAbs("away", 1, 100, 10), [5, 58]);
  assert.deepEqual(toAbs("home", 1, 100, 10), [100, 10]);
});

test("broadcast clock freezes at 45:00 with a stoppage tail", () => {
  assert.deepEqual(matchClock(46 * 60_000 + 5000, 1, {}), { main: "45:00", added: "+01:05" });
  const starts = { "2": 47 * 60_000 };
  assert.deepEqual(matchClock(47 * 60_000 + 61_000, 2, starts), { main: "46:01", added: null });
  assert.equal(minuteLabel(47 * 60_000, 1, starts), "45+2'");
  assert.equal(minuteLabel(47 * 60_000 + 30_000, 2, starts), "46'");
});

test("pitch model applies substitutions and red cards to the line-up", () => {
  const m = new PitchModel();
  m.reset(NBA, RVR);
  const base = { match_id: "m", period: 1, clock_ms: 0 } as const;
  const off = RVR.players[3];
  const on = RVR.players[11];
  m.apply({ ...base, seq: 1, type: "substitution", team_id: RVR.team_id, substitution: { player_off_id: off.player_id, player_on_id: on.player_id } } as MatchEvent);
  assert.ok([...m.lineup.away.values()].some((p) => p.player_id === on.player_id));
  m.apply({ ...base, seq: 2, type: "card", team_id: RVR.team_id, player_id: on.player_id, outcome: "red" } as MatchEvent);
  assert.equal(m.lineup.away.size, 10);
  assert.equal(m.targets().length, 21);
});

test("pitch model puts the ball at the end of a completed pass (absolute coords)", () => {
  const m = new PitchModel();
  m.reset(NBA, RVR);
  m.apply({ match_id: "m", seq: 1, period: 1, clock_ms: 0, type: "pass", team_id: RVR.team_id, player_id: RVR.players[6].player_id, x: 40, y: 30, end_x: 60, end_y: 30, outcome: "success" } as MatchEvent);
  assert.deepEqual(m.ball, [45, 38]);
});

test("series colours are distinct for every fixture of the fictional clubs", () => {
  for (const h of teams) for (const a of teams) {
    if (h === a) continue;
    const c = fixtureColours(h, a);
    assert.notEqual(c.home, c.away, `${h.team_id} v ${a.team_id}`);
  }
  assert.match(seriesColour("#1E3A8A"), /^#[0-9a-f]{6}$/);
});
