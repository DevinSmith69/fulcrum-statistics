/** API base URL (REST + WebSocket). Set VITE_API_URL to point at the real API or the mock. */
export const API_URL: string =
  (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? "http://localhost:8000";

/** WebSocket URL derived from the API base (http → ws, https → wss). */
export const WS_URL = `${API_URL.replace(/^http/, "ws")}/ws`;

/** Default fixture when the API has no match yet (fictional clubs from data/teams.json). */
export const DEFAULT_FIXTURE = { home_team_id: "NBA", away_team_id: "RVR", seed: 7, speed: 10 };
