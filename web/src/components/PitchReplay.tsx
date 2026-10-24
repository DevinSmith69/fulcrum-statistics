import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { minuteLabel } from "../lib/format";
import { momentLabel } from "../lib/i18n";
import { latestReplayable } from "../lib/replay";
import type { MatchFeed } from "../lib/useMatchFeed";
import { replayCaption, useMomentReplay, useSquadNames } from "../lib/useMomentReplay";
import type { Moment } from "../types";
import { isTyping } from "./AudioControls";
import { Pitch } from "./Pitch";

/*
 * Replay on the main pitch: an insight card's Replay (or the R key) temporarily swaps the big pitch to the
 * moment's run of play, with a REPLAY badge and the moment caption, then returns to live. Escape exits.
 */

export interface StageItem {
  moment: Moment;
  title: string;
  headline: string | null;
}

interface ReplayStage {
  active: StageItem | null;
  /** Whether the main pitch is on screen and big enough (otherwise cards use their mini replay). */
  roomy: () => boolean;
  play: (moment: Moment) => void;
  stop: () => void;
}

const StageContext = createContext<ReplayStage | null>(null);

/** The main-pitch replay stage of the current view, or null (no main pitch: split view, recap). */
export const useReplayStage = () => useContext(StageContext);

/** Title and headline shown on the badge for `m`. */
export function stageItem(feed: MatchFeed, m: Moment, lang: string): StageItem {
  const headline = (m.moment_id && feed.insights[m.moment_id]?.variants[0]?.headline) || null;
  return { moment: m, title: `${momentLabel(lang, m.type)} ${minuteLabel(m.clock_ms, m.period, feed.periodStarts)}`, headline };
}

interface ProviderProps {
  feed: MatchFeed;
  lang: string;
  /** Media query the viewport must match for card replays to use the main pitch (always, when omitted). */
  roomyQuery?: string;
  children: ReactNode;
}

/** Holds the main-pitch replay for one view; R replays the newest big moment, Escape returns to live. */
export function ReplayStageProvider({ feed, lang, roomyQuery, children }: ProviderProps) {
  const [active, setActive] = useState<StageItem | null>(null);
  const feedRef = useRef(feed);
  feedRef.current = feed;
  const activeRef = useRef(active);
  activeRef.current = active;

  const play = useCallback((m: Moment) => setActive(stageItem(feedRef.current, m, lang)), [lang]);
  const stop = useCallback(() => setActive(null), []);
  const roomy = useCallback(() => !roomyQuery || window.matchMedia(roomyQuery).matches, [roomyQuery]);

  useEffect(() => {
    // Capture phase, so Escape leaves the replay before the broadcast view treats it as "exit broadcast".
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Escape" && activeRef.current) {
        e.stopImmediatePropagation();
        setActive(null);
      } else if ((e.key === "r" || e.key === "R") && !e.repeat) {
        if (activeRef.current) return setActive(null);
        const m = latestReplayable(feedRef.current.moments);
        if (m) setActive(stageItem(feedRef.current, m, lang));
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [lang]);

  // A new match (or a reconnect to another one) ends any replay of the old one.
  useEffect(() => setActive(null), [feed.matchId]);

  const value = useMemo(() => ({ active, roomy, play, stop }), [active, roomy, play, stop]);
  return <StageContext.Provider value={value}>{children}</StageContext.Provider>;
}

interface StagedPitchProps {
  feed: MatchFeed;
  followedPlayerId?: string | null;
  /** Live-only layers (overlay cues), hidden while a replay is on. */
  overlay?: ReactNode;
}

/** The main pitch: live, or the active replay on top (the live pitch stays mounted, so it resumes smoothly). */
export function StagedPitch({ feed, followedPlayerId, overlay }: StagedPitchProps) {
  const stage = useReplayStage();
  const active = stage?.active ?? null;
  return (
    <>
      <div className={`absolute inset-0 ${active ? "invisible" : ""}`} aria-hidden={active ? true : undefined}>
        <Pitch model={feed.pitch} followedPlayerId={followedPlayerId} />
        {overlay}
      </div>
      {active && stage && <PitchReplay key={active.moment.moment_id} item={active} feed={feed} followedPlayerId={followedPlayerId} onDone={stage.stop} />}
    </>
  );
}

const EMPTY_RETURN_MS = 2500;

function PitchReplay({ item, feed, followedPlayerId, onDone }: { item: StageItem; feed: MatchFeed; followedPlayerId?: string | null; onDone: () => void }) {
  const { moment, title, headline } = item;
  const { model, status, step } = useMomentReplay(moment, feed.home, feed.away, 1, onDone);
  const names = useSquadNames(feed.home, feed.away);

  useEffect(() => {
    if (status !== "empty" && status !== "error") return;
    const t = window.setTimeout(onDone, EMPTY_RETURN_MS);
    return () => window.clearTimeout(t);
  }, [status, onDone]);

  return (
    <div className="absolute inset-0" role="region" aria-label={`Replay: ${title}`}>
      {status === "playing" ? (
        <Pitch model={model} followedPlayerId={followedPlayerId} highlightIds={moment.player_ids ?? []} label={`Replay of the run of play: ${title}`} />
      ) : (
        <div className="absolute inset-0 bg-pitch" />
      )}
      <div className="pointer-events-none absolute inset-0 ring-2 ring-inset ring-signal/45" aria-hidden />
      <div className="replay-in absolute left-4 top-4 flex max-w-[min(560px,calc(100%-2rem))] items-stretch overflow-hidden rounded-md bg-ink-950/92 ring-1 ring-white/10">
        <span className="font-display flex items-center bg-signal px-3 text-lg font-bold tracking-[0.22em] text-ink-950">REPLAY</span>
        <div className="min-w-0 px-3 py-1.5">
          <div className="font-display truncate text-lg font-semibold leading-tight text-ink-50">{title}</div>
          {headline && <div className="truncate text-sm leading-snug text-ink-300">{headline}</div>}
        </div>
      </div>
      {/* Bottom centre: the live cues are hidden during a replay, and the corners hold the demo / view controls. */}
      <div className="absolute bottom-4 left-1/2 flex max-w-[calc(100%-2rem)] -translate-x-1/2 items-center gap-2">
        <div className="flex min-w-0 items-center gap-3 rounded-md bg-ink-950/88 px-3 py-1.5 text-sm text-ink-100 ring-1 ring-white/10">
          <span className="min-w-[9rem] truncate capitalize" aria-live="polite">
            {replayCaption(status, step.ev, names)}
          </span>
          {status === "playing" && step.n > 0 && (
            <span className="h-1 w-24 shrink-0 overflow-hidden rounded-full bg-ink-700" aria-hidden>
              <span className="block h-full bg-signal transition-[width] duration-300" style={{ width: `${(100 * step.i) / step.n}%` }} />
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onDone}
          className="shrink-0 rounded-md bg-ink-950/88 px-3 py-1.5 text-sm text-ink-100 ring-1 ring-white/10 hover:text-ink-50"
        >
          Back to live <kbd className="ml-1 text-ink-500">Esc</kbd>
        </button>
      </div>
    </div>
  );
}
