import { useEffect, useRef, useState } from "react";
import { BroadcastAudioButtons } from "../components/AudioControls";
import { CueInspector } from "../components/CueInspector";
import { OverlayLayer } from "../components/OverlayLayer";
import { StagedPitch } from "../components/PitchReplay";
import { KitStripe } from "../components/TeamMark";
import { matchClock } from "../lib/format";
import type { AudioPrefs, Commentary } from "../lib/useCommentary";
import type { MatchFeed } from "../lib/useMatchFeed";
import type { ViewerPrefs } from "../types";

/** Compact score bug in the top-left corner of the broadcast picture. */
export function ScoreBug({ feed }: { feed: MatchFeed }) {
  const s = feed.state;
  const clock = s ? matchClock(s.clock_ms, s.period, feed.periodStarts) : { main: "00:00", added: null };
  return (
    <div className="flex items-stretch overflow-hidden rounded-md bg-ink-950/92 text-ink-50 ring-1 ring-white/10">
      <div className="flex items-center gap-2 px-3 py-1.5">
        <KitStripe team={feed.home} className="h-6 w-1" />
        <span className="font-display text-xl font-semibold">{feed.home?.short_name}</span>
        <span className="font-display px-2 text-2xl font-bold">
          {s?.score_home ?? 0}–{s?.score_away ?? 0}
        </span>
        <span className="font-display text-xl font-semibold">{feed.away?.short_name}</span>
        <KitStripe team={feed.away} className="h-6 w-1" />
      </div>
      <div className="font-display flex items-center bg-ink-800 px-3 text-xl font-semibold">
        {feed.finished ? "FT" : clock.main}
        {clock.added && !feed.finished && <span className="ml-1 text-base text-signal">{clock.added}</span>}
      </div>
    </div>
  );
}

interface Props {
  feed: MatchFeed;
  prefs: ViewerPrefs;
  audio: AudioPrefs;
  onAudioChange: (p: Partial<AudioPrefs>) => void;
  commentary: Commentary;
  /** Opens the highlights reel (set once the match is over). */
  onPlayHighlights?: () => void;
}

/** Broadcast mode: pitch and overlays only, full screen, with a toggleable cue inspector (key I) and voice (V). */
export function BroadcastView({ feed, prefs, audio, onAudioChange, commentary, onPlayHighlights }: Props) {
  const [inspector, setInspector] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "i" || e.key === "I") setInspector((v) => !v);
      if (e.key === "f" || e.key === "F") toggleFullscreen();
      if (e.key === "Escape" && !document.fullscreenElement) window.location.hash = "#/";
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => undefined);
    else rootRef.current?.requestFullscreen().catch(() => undefined);
  };

  return (
    <div ref={rootRef} className="group fixed inset-0 z-40 flex bg-black">
      <div className="relative min-w-0 flex-1">
        <StagedPitch feed={feed} followedPlayerId={prefs.followed_player_id} overlay={<OverlayLayer feed={feed} />} />
        <div className="absolute left-1/2 top-5 -translate-x-1/2">
          <ScoreBug feed={feed} />
        </div>
        <div className="absolute bottom-4 right-4 flex gap-2 opacity-0 transition-opacity duration-300 focus-within:opacity-100 group-hover:opacity-100">
          <a href="#/" className="rounded-md bg-ink-900/90 px-3 py-1.5 text-sm text-ink-100 ring-1 ring-ink-700 hover:text-ink-50">
            Exit <kbd className="ml-1 text-ink-500">Esc</kbd>
          </a>
          {onPlayHighlights && (
            <button onClick={onPlayHighlights} className="rounded-md bg-signal px-3 py-1.5 text-sm font-semibold text-ink-950 ring-1 ring-signal">
              Play your highlights <kbd className="ml-1 text-ink-800">H</kbd>
            </button>
          )}
          <BroadcastAudioButtons audio={audio} onChange={onAudioChange} commentary={commentary} />
          <button onClick={toggleFullscreen} className="rounded-md bg-ink-900/90 px-3 py-1.5 text-sm text-ink-100 ring-1 ring-ink-700 hover:text-ink-50">
            Full screen <kbd className="ml-1 text-ink-500">F</kbd>
          </button>
          <button
            onClick={() => setInspector((v) => !v)}
            aria-pressed={inspector}
            className={`rounded-md px-3 py-1.5 text-sm ring-1 ${inspector ? "bg-signal font-semibold text-ink-950 ring-signal" : "bg-ink-900/90 text-ink-100 ring-ink-700 hover:text-ink-50"}`}
          >
            Cue inspector <kbd className={`ml-1 ${inspector ? "text-ink-800" : "text-ink-500"}`}>I</kbd>
          </button>
        </div>
      </div>
      {inspector && <CueInspector feed={feed} prefs={prefs} onClose={() => setInspector(false)} />}
    </div>
  );
}
