import { API_URL } from "./config";
import { PRODUCER_KEY_STORAGE } from "./producer";
import type { GraphicProposal, InsightVariant, OverlayCue, ProducerDecision, ProducerSettings, ProducerState } from "../types";

/** Producer console REST calls (API_CONTRACT §2 "Producer console"). Errors throw "<status> <detail>". */

export class ProducerApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly issues: string[] = [],
  ) {
    super(message);
  }
}

export function producerKey(): string {
  try {
    return window.localStorage.getItem(PRODUCER_KEY_STORAGE) ?? "";
  } catch {
    return "";
  }
}

export function setProducerKey(key: string) {
  try {
    window.localStorage.setItem(PRODUCER_KEY_STORAGE, key);
  } catch {
    /* private mode: the key lasts for this page only */
  }
}

async function call<T>(matchId: string, path: string, body?: unknown): Promise<T> {
  const key = producerKey();
  const res = await fetch(`${API_URL}/api/matches/${encodeURIComponent(matchId)}/producer${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "content-type": "application/json", ...(key ? { "x-producer-key": key } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    let issues: string[] = [];
    try {
      const problem = await res.json();
      detail = problem.detail ?? problem.title ?? detail;
      issues = Array.isArray(problem.issues) ? problem.issues : [];
    } catch {
      /* not problem+json */
    }
    throw new ProducerApiError(`${res.status} ${typeof detail === "string" ? detail : JSON.stringify(detail)}`, res.status, issues);
  }
  return res.json() as Promise<T>;
}

export const fetchProducer = (matchId: string) => call<ProducerState>(matchId, "");
export const postProducerSettings = (matchId: string, body: ProducerSettings) => call<ProducerState>(matchId, "/settings", body);
export const approveHeld = (matchId: string, insightId: string) =>
  call<ProducerDecision>(matchId, `/held/${encodeURIComponent(insightId)}/approve`, {});
export const killHeld = (matchId: string, insightId: string) => call<ProducerDecision>(matchId, `/held/${encodeURIComponent(insightId)}/kill`, {});
/** Throws ProducerApiError(422, issues) when the edit fails the fact-check. */
export const editHeld = (matchId: string, insightId: string, variants: InsightVariant[]) =>
  call<ProducerDecision>(matchId, `/held/${encodeURIComponent(insightId)}/edit`, { variants });
export const requestGraphic = (matchId: string, request: string) => call<GraphicProposal>(matchId, "/graphics", { request });
export const putOnAir = (matchId: string, proposalId: string) =>
  call<OverlayCue>(matchId, `/graphics/${encodeURIComponent(proposalId)}/air`, {});
