import { useEffect, useRef } from "react";
import { replayCaption, useMomentReplay, useSquadNames } from "../lib/useMomentReplay";
import type { Moment, Team } from "../types";
import { Pitch } from "./Pitch";

const PASSES = 2; // play, loop once, then close

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
 * PitchModel that is re-fed the moment's events one at a time (`useMomentReplay`). Plays twice, then closes.
 */
export function MiniReplay({ moment, home, away, title, onClose }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const close = () => onClose(!!rootRef.current?.contains(document.activeElement));
  const { model, status, step } = useMomentReplay(moment, home, away, PASSES, close);
  const names = useSquadNames(home, away);

  useEffect(() => {
    closeRef.current?.focus({ preventScroll: true });
  }, []);

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
          {replayCaption(status, step.ev, names)}
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
