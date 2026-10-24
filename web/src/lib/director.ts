import type { MomentType } from "../types";

/*
 * Demo director: live match controls (API_CONTRACT §2 "Match controls") for recording the demo.
 * Pure helpers (no API, no DOM), tested in tests/director.test.ts.
 */

/** The speeds the control bar offers (sim-time multipliers). */
export const SPEEDS = [1, 5, 10, 30, 60] as const;

/** Speed for a new demo match when the current one has no controls. */
export const DEFAULT_DEMO_SPEED = 10;

/** The demo fixture: seed 11, Harlow Cross v Marsh Dock United (red card, four goals, late equaliser). */
export const demoFixture = (speed: number) => ({ home_team_id: "HLC", away_team_id: "MDU", seed: 11, speed });

/** `GET/POST /api/matches/{id}/control` response. */
export interface ControlState {
  match_id: string;
  available: boolean;
  speed: number;
  effective_speed: number;
  paused: boolean;
  skipping: boolean;
  skip_min_significance: number | null;
  position_ms: number;
  finished: boolean;
  last_skip: { moment_id: string; type: MomentType; clock_ms: number; significance: number } | null;
}

export type ControlAction =
  | { action: "set_speed"; speed: number }
  | { action: "pause" }
  | { action: "resume" }
  | { action: "next_moment"; min_significance?: number; types?: MomentType[] }
  | { action: "cancel_skip" };

/** The next preset speed up (`dir` = 1) or down (-1) from `current`; stays put at either end. */
export function stepSpeed(current: number, dir: 1 | -1): number {
  if (dir > 0) return SPEEDS.find((s) => s > current) ?? SPEEDS[SPEEDS.length - 1];
  return [...SPEEDS].reverse().find((s) => s < current) ?? SPEEDS[0];
}

/** "10×", "0.5×". */
export const speedLabel = (speed: number) => `${Number.isInteger(speed) ? speed : speed.toFixed(1)}×`;

/** Whether the page was opened with `?demo=1` (the control bar starts visible). */
export function demoRequested(search: string): boolean {
  const v = new URLSearchParams(search).get("demo");
  return v != null && v !== "0" && v !== "false";
}

/** The URL that follows `matchId`: same page and view (hash), `?match=<id>`, keeping `demo=1`. */
export function matchUrl(href: string, matchId: string): string {
  const url = new URL(href);
  url.searchParams.set("match", matchId);
  url.searchParams.set("demo", "1");
  return url.toString();
}

/** How long (match time) "Stopped at <moment>" stays in the status line after a skip. */
const RECENT_SKIP_MS = 60_000;

/** One-line status for the bar: what the match clock is doing right now. */
export function controlStatus(c: ControlState | null, label: (t: MomentType) => string = (t) => t.replace(/_/g, " ")): string {
  if (!c) return "";
  if (c.finished) return "Full time";
  if (c.skipping) return "Finding the next big moment…";
  if (c.paused) return "Paused";
  if (c.last_skip && c.position_ms - c.last_skip.clock_ms < RECENT_SKIP_MS) return `Stopped at ${label(c.last_skip.type)}`;
  return "Live";
}
