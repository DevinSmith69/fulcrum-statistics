/**
 * The co-commentator's speech queue (pure logic, unit-tested in tests/speechQueue.test.ts).
 *
 *  - Never talks over itself: one line at a time; the rest wait in a priority queue.
 *  - Goals and red cards (priority >= PREEMPT) pre-empt a lower-priority line that is being spoken.
 *  - Stale lines are dropped: older than ~15 s of match time AND a few seconds of wall time. The wall-time floor
 *    matters when the match runs at 10× (15 s of match time is 1.5 s of real time — any line waiting behind
 *    another would otherwise be dropped).
 *  - De-duplicated by id; the queue is bounded (the lowest-priority, oldest line goes first).
 */

export type SpeechKind = "description" | "commentary";

export interface SpeechItem {
  /** Stable id (event id / moment id) used to de-duplicate. */
  id: string;
  text: string;
  /** Viewer language code (en/es/fr/pt/de). */
  lang: string;
  priority: number;
  kind: SpeechKind;
  /** Match clock (ms) when the line was queued. */
  clockMs: number;
  /** Wall clock (ms, Date.now()) when the line was queued. */
  wallMs: number;
}

export const PRIORITY = { low: 1, normal: 2, high: 3 } as const;
/** Lines at this priority or above interrupt a lower-priority line that is being spoken. */
export const PREEMPT = PRIORITY.high;
export const STALE_MATCH_MS = 15_000;
export const STALE_WALL_MS = 4_000;
export const MAX_QUEUE = 6;
const SEEN_KEEP = 200;

export interface QueueState {
  current: SpeechItem | null;
  queue: SpeechItem[];
  seen: string[];
}

export const emptyQueue = (): QueueState => ({ current: null, queue: [], seen: [] });

/** True when the line is too old to be worth saying. */
export function isStale(item: SpeechItem, nowClockMs: number, nowWallMs: number): boolean {
  return nowClockMs - item.clockMs > STALE_MATCH_MS && nowWallMs - item.wallMs > STALE_WALL_MS;
}

const byPriority = (a: SpeechItem, b: SpeechItem) => b.priority - a.priority || a.wallMs - b.wallMs;

/**
 * Add a line. `preempt` is true when the caller should stop the line being spoken now (the next `takeNext`
 * then returns the new, higher-priority line; the interrupted one is not resumed).
 */
export function enqueue(s: QueueState, item: SpeechItem): { state: QueueState; preempt: boolean } {
  const text = item.text.trim();
  if (!text || s.seen.includes(item.id)) return { state: s, preempt: false };
  const seen = [...s.seen.slice(-(SEEN_KEEP - 1)), item.id];
  let queue = [...s.queue, { ...item, text }].sort(byPriority);
  if (queue.length > MAX_QUEUE) {
    // Drop the least important line (lowest priority, then oldest).
    let drop = 0;
    for (let i = 1; i < queue.length; i++) {
      const q = queue[i];
      const d = queue[drop];
      if (q.priority < d.priority || (q.priority === d.priority && q.wallMs < d.wallMs)) drop = i;
    }
    queue = queue.filter((_, i) => i !== drop);
  }
  const preempt = !!s.current && item.priority >= PREEMPT && item.priority > s.current.priority;
  return { state: { ...s, queue, seen }, preempt };
}

/** Pop the next line to speak (dropping stale ones); it becomes `current`. Null when nothing is due or busy. */
export function takeNext(s: QueueState, nowClockMs: number, nowWallMs: number): { state: QueueState; item: SpeechItem | null } {
  if (s.current) return { state: s, item: null };
  const fresh = s.queue.filter((q) => !isStale(q, nowClockMs, nowWallMs));
  const [item = null, ...rest] = fresh;
  return { state: { ...s, current: item, queue: rest }, item };
}

/** The current line finished (or was interrupted). */
export const finish = (s: QueueState): QueueState => ({ ...s, current: null });

/** Stop control: forget everything queued (ids stay "seen" so nothing replays). */
export const clear = (s: QueueState): QueueState => ({ ...s, current: null, queue: [] });

/** BCP-47 tag for each viewer language (Portuguese copy in the overlay is Brazilian: "Gol"). */
export const LANG_TAG: Record<string, string> = { en: "en-GB", es: "es-ES", fr: "fr-FR", pt: "pt-BR", de: "de-DE" };

export interface VoiceLike {
  lang: string;
  name: string;
  localService?: boolean;
}

/** Best browser voice for a language: exact locale, then same language; natural/neural voices preferred. */
export function pickVoice<V extends VoiceLike>(voices: readonly V[], lang: string): V | null {
  const tag = (LANG_TAG[lang] ?? lang).toLowerCase();
  const base = tag.split("-")[0];
  const norm = (v: V) => v.lang.toLowerCase().replace("_", "-");
  const score = (v: V) => {
    const l = norm(v);
    const match = l === tag ? 2 : l.split("-")[0] === base ? 1 : 0;
    if (!match) return 0;
    return match * 10 + (/natural|neural|online|premium|enhanced/i.test(v.name) ? 2 : 0) + (v.localService ? 0 : 1);
  };
  let best: V | null = null;
  let bestScore = 0;
  for (const v of voices) {
    const sc = score(v);
    if (sc > bestScore) {
      best = v;
      bestScore = sc;
    }
  }
  return best;
}
