import type { MatchSummary } from "../types";

/**
 * Which existing match to follow. With `?match=<id>`: that match, or `null` while the API doesn't know it yet
 * (e.g. just restarted). Without: the first unfinished match, else the newest one, else `null`.
 */
export function pickMatch(matches: MatchSummary[], requested?: string | null): string | null {
  if (requested) return matches.some((m) => m.match_id === requested) ? requested : null;
  const live = matches.find((m) => m.status !== "finished") ?? matches[matches.length - 1];
  return live ? live.match_id : null;
}

/** The `?match=<id>` query parameter of the current page, if any. */
export function requestedMatchId(search: string = typeof window === "undefined" ? "" : window.location.search) {
  return new URLSearchParams(search).get("match");
}
