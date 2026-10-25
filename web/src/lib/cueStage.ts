import { useEffect, useRef, useState } from "react";
import type { OverlayAnchor, OverlayCue } from "../types";

/**
 * Overlay cue scheduling per API_CONTRACT §4:
 *  - never two cues on the same anchor at once;
 *  - a higher-priority cue pre-empts the one showing; equal (or lower) priority waits in that anchor's queue;
 *  - a cue becomes eligible once the match clock reaches its `show_at_clock_ms`.
 * `duration_ms` is applied in wall-clock time so copy stays readable when the match runs at 10× speed.
 */
export interface Slot {
  cue: OverlayCue;
  until: number;
}

export interface Stage {
  active: Partial<Record<OverlayAnchor, Slot>>;
  queue: Partial<Record<OverlayAnchor, OverlayCue[]>>;
  leaving: { cue: OverlayCue; anchor: OverlayAnchor; until: number }[];
}

export const EXIT_MS = 450;
/** A queued cue older than this (wall ms) is dropped instead of shown late. */
const QUEUE_TTL_MS = 12_000;

export const emptyStage = (): Stage => ({ active: {}, queue: {}, leaving: [] });

const anchorOf = (c: OverlayCue): OverlayAnchor => c.anchor ?? "bottom_left";

/** Offer a cue whose show time has arrived. Pure: returns a new Stage. */
export function offer(stage: Stage, cue: OverlayCue, now: number): Stage {
  const anchor = anchorOf(cue);
  const cur = stage.active[anchor];
  if (!cur) return { ...stage, active: { ...stage.active, [anchor]: { cue, until: now + cue.duration_ms } } };
  if (cue.priority > cur.cue.priority) {
    return {
      ...stage,
      active: { ...stage.active, [anchor]: { cue, until: now + cue.duration_ms } },
      leaving: [...stage.leaving, { cue: cur.cue, anchor, until: now + EXIT_MS }],
    };
  }
  const q = [...(stage.queue[anchor] ?? []), { ...cue, _queuedAt: now } as OverlayCue];
  q.sort((a, b) => b.priority - a.priority);
  return { ...stage, queue: { ...stage.queue, [anchor]: q } };
}

/** Expire finished cues and promote queued ones. Pure. */
export function tick(stage: Stage, now: number): Stage {
  let changed = false;
  const active = { ...stage.active };
  const queue = { ...stage.queue };
  let leaving = stage.leaving.filter((l) => l.until > now);
  if (leaving.length !== stage.leaving.length) changed = true;
  for (const anchor of Object.keys(active) as OverlayAnchor[]) {
    const slot = active[anchor];
    if (slot && slot.until <= now) {
      leaving = [...leaving, { cue: slot.cue, anchor, until: now + EXIT_MS }];
      delete active[anchor];
      changed = true;
    }
  }
  for (const anchor of Object.keys(queue) as OverlayAnchor[]) {
    const fresh = (queue[anchor] ?? []).filter((c) => now - ((c as OverlayCue & { _queuedAt?: number })._queuedAt ?? now) < QUEUE_TTL_MS);
    if (fresh.length !== (queue[anchor] ?? []).length) changed = true;
    if (!active[anchor] && fresh.length) {
      const [next, ...rest] = fresh;
      active[anchor] = { cue: next, until: now + next.duration_ms };
      queue[anchor] = rest;
      changed = true;
    } else queue[anchor] = fresh;
  }
  return changed ? { active, queue, leaving } : stage;
}

/**
 * React binding: feeds new cues (by cue_id) into the stage as the match clock reaches their show time.
 * `clockMs` is the latest known match clock.
 */
export function useCueStage(cues: OverlayCue[], clockMs: number, filter?: (c: OverlayCue) => boolean) {
  const [stage, setStage] = useState<Stage>(emptyStage);
  const seen = useRef(new Set<string>());
  const pending = useRef<OverlayCue[]>([]);
  const clock = useRef(clockMs);
  clock.current = clockMs;
  const filterRef = useRef(filter);
  filterRef.current = filter;

  const primed = useRef(false);

  useEffect(() => {
    // A view mounted mid-match starts clean: cues already in the log are history, not news.
    const skipAll = !primed.current;
    primed.current = true;
    for (const c of cues) {
      const id = c.cue_id ?? `${c.moment_id}-${c.template}-${c.anchor}`;
      if (seen.current.has(id)) continue;
      seen.current.add(id);
      if (skipAll || (filterRef.current && !filterRef.current(c))) continue;
      pending.current.push(c);
    }
  }, [cues]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      const now = performance.now();
      setStage((s0) => {
        let s = s0;
        const due = pending.current.filter((c) => c.show_at_clock_ms <= clock.current);
        if (due.length) {
          pending.current = pending.current.filter((c) => !due.includes(c));
          for (const c of due.sort((a, b) => b.priority - a.priority)) s = offer(s, c, now);
        }
        return tick(s, now);
      });
    }, 100);
    return () => window.clearInterval(timer);
  }, []);

  return stage;
}

/**
 * The cue inspector's log: cues fetched from `/overlay-cues` (everything before the viewer arrived) plus the
 * live ones, one entry per cue (the live copy wins), oldest first.
 */
export function mergeCueLog(history: OverlayCue[], live: OverlayCue[]): OverlayCue[] {
  const key = (c: OverlayCue) => c.cue_id ?? `${c.insight_id}|${c.template}|${c.anchor}|${c.show_at_clock_ms}`;
  const byKey = new Map<string, OverlayCue>();
  for (const c of [...history, ...live]) byKey.set(key(c), c);
  return [...byKey.values()].sort((a, b) => a.show_at_clock_ms - b.show_at_clock_ms);
}
