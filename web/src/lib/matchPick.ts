import type { MatchSummary } from "../types";

/**
 * Which existing match to follow: the one named in `?match=<id>` if the API knows it, else the first unfinished
 * match, else the newest one. `null` means there is none and the caller starts the default fixture.
 */
export function pickMatch(matches: MatchSummary[], requested?: string | null): string | null {
  if (requested && matches.some((m) => m.match_id === requested)) return requested;
  const live = matches.find((m) => m.status !== "finished") ?? matches[matches.length - 1];
  return live ? live.match_id : null;
}

/** The `?match=<id>` query parameter of the current page, if any. */
export function requestedMatchId(search: string = typeof window === "undefined" ? "" : window.location.search) {
  return new URLSearchParams(search).get("match");
}
