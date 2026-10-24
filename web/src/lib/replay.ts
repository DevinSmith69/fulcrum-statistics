import type { MatchEvent, Moment, MomentType } from "../types";

/*
 * Mini replay: pick the run of play that led to a moment from the match's event log.
 * Pure helpers (no API, no DOM), tested in tests/replay.test.ts.
 */

/** Period boundaries never belong to a replay; summaries have no single run of play to show. */
const NOT_REPLAYABLE = new Set<MomentType>(["half_time", "full_time"]);

export const isReplayable = (m: Pick<Moment, "type">) => !NOT_REPLAYABLE.has(m.type);

/** An event the pitch can draw (it has a location). */
const onPitch = (ev: MatchEvent) => ev.x != null && ev.y != null;

/** Events that restart play: a replay never reaches back past one. */
const RESTART = new Set<MatchEvent["type"]>(["kick_off", "period_start"]);

export interface WindowOptions {
  /** Minimum number of drawable events to show (reaching back before seq_start if needed). */
  min?: number;
  /** Maximum number of drawable events (the latest ones win). */
  max?: number;
}

/**
 * The drawable events of the run of play behind `moment`, oldest first: everything from the moment's
 * `seq_start` (or the start of the possession it ended in, whichever is earlier) up to `seq_end`, padded
 * back to `min` events and capped at the last `max`. Never crosses a kick-off or a period boundary.
 */
export function replayWindow(
  events: readonly MatchEvent[],
  moment: Pick<Moment, "seq_start" | "seq_end" | "period">,
  { min = 5, max = 14 }: WindowOptions = {},
): MatchEvent[] {
  let end = -1;
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].seq <= moment.seq_end) {
      end = i;
      break;
    }
  }
  if (end < 0) return [];
  const last = events.slice(0, end + 1).reverse().find(onPitch);
  const possession = last?.possession_id ?? null;

  const out: MatchEvent[] = [];
  let inPossession = possession != null;
  for (let i = end; i >= 0 && out.length < max; i--) {
    const ev = events[i];
    if (ev.period !== moment.period) break;
    if (inPossession && ev.possession_id !== possession) inPossession = false;
    const wanted = ev.seq >= moment.seq_start || inPossession || out.length < min;
    if (!wanted) break;
    if (onPitch(ev)) out.push(ev);
    if (RESTART.has(ev.type)) break;
  }
  return out.reverse();
}

/** Time per replayed event, so a run of play lasts roughly 6–12 s whatever its length. */
export const stepMs = (n: number) => Math.round(Math.min(1400, Math.max(650, 9000 / Math.max(1, n))));

/** Hold on the final frame before looping / closing. */
export const HOLD_MS = 1500;

/** Append a fetched tail to a cached, seq-ordered event log (de-duplicated on seq). */
export function appendEvents(have: readonly MatchEvent[], fresh: readonly MatchEvent[]): MatchEvent[] {
  const since = have.length ? have[have.length - 1].seq : -1;
  const tail = fresh.filter((e) => e.seq > since).sort((a, b) => a.seq - b.seq);
  return tail.length ? [...have, ...tail] : [...have];
}
