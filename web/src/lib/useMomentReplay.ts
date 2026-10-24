import { useEffect, useMemo, useRef, useState } from "react";
import { fetchEvents } from "./api";
import { PitchModel } from "./pitchModel";
import { appendEvents, HOLD_MS, replayWindow, stepMs } from "./replay";
import type { MatchEvent, Moment, Team } from "../types";

// One event log per match, shared by every replay: the first fetches the match, later ones only the tail.
const logs = new Map<string, MatchEvent[]>();

async function matchEvents(matchId: string): Promise<MatchEvent[]> {
  const have = logs.get(matchId) ?? [];
  const since = have.length ? have[have.length - 1].seq : -1;
  const all = appendEvents(have, await fetchEvents(matchId, since));
  logs.set(matchId, all);
  return all;
}

const LEAD_MS = 500; // show the set-up before the first event

export type ReplayStatus = "loading" | "playing" | "empty" | "error";

export interface ReplayStep {
  /** Events shown so far (1-based once playing). */
  i: number;
  n: number;
  ev: MatchEvent | null;
}

/**
 * Plays the run of play behind `moment` on a private PitchModel (the same renderer as the live pitch): line-ups
 * as they were just before the run, then one event per `stepMs`. Plays `passes` times, holds, then `onDone`.
 * Used by the mini replay in an insight card, the full-size replay on the main pitch and the highlights reel.
 * `paused` (highlights reel) holds the run on its current event; it carries on from there when unpaused.
 */
export function useMomentReplay(moment: Moment, home: Team | null, away: Team | null, passes: number, onDone: () => void, paused = false) {
  const model = useMemo(() => new PitchModel(), []);
  const [status, setStatus] = useState<ReplayStatus>("loading");
  const [step, setStep] = useState<ReplayStep>({ i: 0, n: 0, ev: null });
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  // The next tick, parked while paused.
  const parked = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (paused || !parked.current) return;
    const fn = parked.current;
    parked.current = null;
    fn();
  }, [paused]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const later = (fn: () => void, ms: number) => {
      timer = window.setTimeout(() => {
        if (pausedRef.current) parked.current = fn;
        else fn();
      }, ms);
    };
    setStatus("loading");
    matchEvents(moment.match_id)
      .then((all) => {
        if (cancelled) return;
        const win = replayWindow(all, moment);
        if (!win.length || !home || !away) {
          setStatus("empty");
          return;
        }
        const before = all.filter((e) => e.seq < win[0].seq);
        const ms = stepMs(win.length);
        let pass = 0;
        let i = -1;
        const tick = () => {
          if (cancelled) return;
          if (i < 0) {
            // Line-ups (subs, red cards) and the ball as they were just before the run starts.
            model.reset(home, away);
            for (const e of before) model.apply(e, false);
            setStep({ i: 0, n: win.length, ev: null });
            i = 0;
            later(tick, LEAD_MS);
            return;
          }
          model.apply(win[i]);
          setStep({ i: i + 1, n: win.length, ev: win[i] });
          i++;
          if (i < win.length) later(tick, ms);
          else if (++pass < passes) {
            i = -1;
            later(tick, HOLD_MS);
          } else later(() => onDoneRef.current(), HOLD_MS);
        };
        setStatus("playing");
        tick();
      })
      .catch(() => !cancelled && setStatus("error"));
    return () => {
      cancelled = true;
      parked.current = null;
      window.clearTimeout(timer);
    };
  }, [moment.moment_id, model]);

  return { model, status, step };
}

/** Short caption for the replayed event ("pass · Okafor"), or a status line. */
export function replayCaption(status: ReplayStatus, ev: MatchEvent | null, names: Map<string, string>): string {
  if (status === "loading") return "Loading the run of play…";
  if (status === "error") return "Couldn't load the events for this moment.";
  if (status === "empty") return "No on-ball events to replay for this moment.";
  if (!ev) return "Build-up";
  const who = ev.player_id ? names.get(ev.player_id) : undefined;
  return `${ev.type.replace(/_/g, " ")}${who ? ` · ${who}` : ""}`;
}

/** player_id → short name for both squads. */
export function useSquadNames(home: Team | null, away: Team | null) {
  return useMemo(() => {
    const m = new Map<string, string>();
    for (const p of [...(home?.players ?? []), ...(away?.players ?? [])]) m.set(p.player_id, p.short_name);
    return m;
  }, [home, away]);
}
