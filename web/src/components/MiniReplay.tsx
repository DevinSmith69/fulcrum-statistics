import { useEffect, useMemo, useRef, useState } from "react";
import { fetchEvents } from "../lib/api";
import { PitchModel } from "../lib/pitchModel";
import { appendEvents, HOLD_MS, replayWindow, stepMs } from "../lib/replay";
import type { MatchEvent, Moment, Team } from "../types";
import { Pitch } from "./Pitch";

// One event log per match, shared by every card: the first replay fetches the match, later ones only the tail.
const logs = new Map<string, MatchEvent[]>();

async function matchEvents(matchId: string): Promise<MatchEvent[]> {
  const have = logs.get(matchId) ?? [];
  const since = have.length ? have[have.length - 1].seq : -1;
  const all = appendEvents(have, await fetchEvents(matchId, since));
  logs.set(matchId, all);
  return all;
}

const PASSES = 2; // play, loop once, then close
const LEAD_MS = 500; // show the set-up before the first event

type Status = "loading" | "playing" | "empty" | "error";

interface Props {
  moment: Moment;
  home: Team | null;
  away: Team | null;
  title: string;
  /** `hadFocus`: focus was inside the replay, so the caller should move it back to the Replay button. */
  onClose: (hadFocus: boolean) => void;
}

/**
 * Animated mini pitch of the run of play behind a moment: the existing Pitch renderer driven by a private
 * PitchModel that is re-fed the moment's events one at a time. Plays twice, then closes itself.
 */
export function MiniReplay({ moment, home, away, title, onClose }: Props) {
  const model = useMemo(() => new PitchModel(), []);
  const [status, setStatus] = useState<Status>("loading");
  const [step, setStep] = useState<{ i: number; n: number; ev: MatchEvent | null }>({ i: 0, n: 0, ev: null });
  const rootRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  const close = () => onCloseRef.current(!!rootRef.current?.contains(document.activeElement));

  useEffect(() => {
    closeRef.current?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
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
            timer = window.setTimeout(tick, LEAD_MS);
            return;
          }
          model.apply(win[i]);
          setStep({ i: i + 1, n: win.length, ev: win[i] });
          i++;
          if (i < win.length) timer = window.setTimeout(tick, ms);
          else if (++pass < PASSES) {
            i = -1;
            timer = window.setTimeout(tick, HOLD_MS);
          } else timer = window.setTimeout(close, HOLD_MS);
        };
        setStatus("playing");
        tick();
      })
      .catch(() => !cancelled && setStatus("error"));
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [moment.moment_id, model]);

  const names = useMemo(() => {
    const m = new Map<string, string>();
    for (const p of [...(home?.players ?? []), ...(away?.players ?? [])]) m.set(p.player_id, p.short_name);
    return m;
  }, [home, away]);

  const ev = step.ev;
  const caption =
    status === "loading" ? "Loading the run of play…"
    : status === "error" ? "Couldn't load the events for this moment."
    : status === "empty" ? "No on-ball events to replay for this moment."
    : ev ? `${ev.type.replace(/_/g, " ")}${ev.player_id && names.get(ev.player_id) ? ` · ${names.get(ev.player_id)}` : ""}`
    : "Build-up";

  return (
    <div
      ref={rootRef}
      role="region"
      aria-label={`Replay: ${title}`}
      onKeyDown={(e) => e.key === "Escape" && (e.stopPropagation(), close())}
      className="feed-in mt-2 overflow-hidden rounded-md bg-ink-950 ring-1 ring-ink-700"
    >
      <div className="relative aspect-[111/74] w-full">
        {status === "playing" && <Pitch model={model} highlightIds={moment.player_ids ?? []} label={`Replay of the run of play: ${title}`} />}
        {status !== "playing" && <div className="absolute inset-0 bg-pitch" />}
      </div>
      <div className="flex items-center gap-2 border-t border-ink-700 px-2 py-1.5 text-[11px] text-ink-300">
        <span className="font-semibold uppercase tracking-wide text-signal">Replay</span>
        <span className="min-w-0 flex-1 truncate capitalize" aria-live="polite">
          {caption}
        </span>
        {status === "playing" && step.n > 0 && (
          <span className="h-1 w-14 overflow-hidden rounded-full bg-ink-700" aria-hidden>
            <span className="block h-full bg-signal transition-[width] duration-300" style={{ width: `${(100 * step.i) / step.n}%` }} />
          </span>
        )}
        <button
          ref={closeRef}
          type="button"
          onClick={close}
          aria-label="Close replay"
          className="rounded-sm px-1.5 text-sm leading-none text-ink-300 hover:bg-ink-800 hover:text-ink-50"
        >
          ×
        </button>
      </div>
    </div>
  );
}
