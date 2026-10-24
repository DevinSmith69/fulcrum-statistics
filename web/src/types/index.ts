// App-level types. Contract types are GENERATED from ../schemas (npm run types) — never edit those by hand.
export type {
  AskAnswer,
  AskRequest,
  CheckStatus,
  Fact,
  Insight,
  InsightVariant,
  MatchEvent,
  MatchPhase,
  MatchState,
  Moment,
  MomentType,
  OverlayAnchor,
  OverlayCue,
  OverlayStat,
  OverlayTemplate,
  Persona,
  Player,
  PlayerLiveStats,
  Recap,
  ServerMessage,
  Team,
  TeamLiveStats,
  ViewerPrefs,
  WinProbPoint,
  WinProbability,
} from "./generated";

import type { Insight, MatchState, Moment, Team, WinProbPoint } from "./generated";

/** Payload of the WebSocket `hello` message (API_CONTRACT §3). */
export interface HelloPayload {
  match_id: string;
  home: Team;
  away: Team;
  state: MatchState | null;
  winprob: WinProbPoint[];
  moments: Moment[];
  insights: Insight[];
  /** Optional extras (sent by the mock; requested from WS2 in docs/HANDOFFS.md). */
  period_start_clock_ms?: Record<string, number>;
  speed?: number;
  status?: "scheduled" | "live" | "finished";
}

/** Row of GET /api/matches. */
export interface MatchSummary {
  match_id: string;
  home_team_id: string;
  away_team_id: string;
  status: string;
  clock_ms: number;
  score: unknown;
}

export type Language = "en" | "es" | "fr" | "pt" | "de";
export type Side = "home" | "away";
