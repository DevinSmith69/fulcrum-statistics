import { API_URL, DEFAULT_FIXTURE } from "./config";
import type { MatchEvent, MatchSummary, Team, ViewerPrefs } from "../types";
import { pickMatch, requestedMatchId } from "./matchPick";

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? body.title ?? detail;
    } catch {
      /* not problem+json */
    }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

/** All fictional clubs (data/teams.json via the API). */
export const fetchTeams = () => getJson<Team[]>("/api/teams");

/** The match to follow: `?match=<id>` if it exists, else the first unfinished match, else the newest one,
 * else start the default fixture. */
export async function resolveMatchId(): Promise<string> {
  const matches = await getJson<MatchSummary[]>("/api/matches");
  const picked = pickMatch(matches, requestedMatchId());
  if (picked) return picked;
  const created = await getJson<{ match_id: string }>("/api/matches", {
    method: "POST",
    body: JSON.stringify(DEFAULT_FIXTURE),
  });
  return created.match_id;
}

/** Start a fresh match (used by the "Restart match" control). */
export const startMatch = () =>
  getJson<{ match_id: string }>("/api/matches", { method: "POST", body: JSON.stringify(DEFAULT_FIXTURE) });

/** Events since a seq — used once after `hello` to rebuild line-ups and the ball position. */
export const fetchEvents = (matchId: string, sinceSeq = -1) =>
  getJson<MatchEvent[]>(`/api/matches/${encodeURIComponent(matchId)}/events?since_seq=${sinceSeq}`);

/** URL of the machine-readable overlay cue feed for a graphics partner (shown in the cue inspector). */
export function overlayCuesUrl(matchId: string, prefs: ViewerPrefs) {
  const q = new URLSearchParams({ persona: prefs.persona ?? "casual", language: prefs.language ?? "en" });
  return `${API_URL}/api/matches/${encodeURIComponent(matchId)}/overlay-cues?${q}`;
}
