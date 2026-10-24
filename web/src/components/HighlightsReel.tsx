import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { minuteLabel } from "../lib/format";
import {
  buildReel,
  EMPTY_REPLAY_MS,
  initialReel,
  INTRO_MS,
  reelReducer,
  stepCapMs,
  type ReelItem,
  type Score,
} from "../lib/highlights";
import { LANGUAGES, momentLabel, personaLabel } from "../lib/i18n";
import { grabKeys } from "../lib/keyGate";
import { SpeechPlayer } from "../lib/speech";
import type { Commentary } from "../lib/useCommentary";
import type { MatchFeed } from "../lib/useMatchFeed";
import { replayCaption, useMomentReplay, useSquadNames } from "../lib/useMomentReplay";
import type { Team, ViewerPrefs } from "../types";
import { isTyping } from "./AudioControls";
import { Pitch } from "./Pitch";
import { KitStripe } from "./TeamMark";

/*
 * "Your highlights": a ~60 s full-screen reel of the match's key moments in the viewer's persona and language.
 * Each moment replays its run of play on the big pitch (useMomentReplay, the same renderer as the live pitch) under
 * a lower third with the insight headline, while the voice reads the insight's commentary line. The playlist,
 * timing and step machine live in lib/highlights.ts.
 */

const MUTE_KEY = "fulcrum.reel.muted";
const SPEECH_DELAY_MS = 450;

function useReelMute() {
  const [muted, setMuted] = useState(() => {
    try {
      return localStorage.getItem(MUTE_KEY) === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(MUTE_KEY, muted ? "1" : "0");
    } catch {
      /* storage unavailable */
    }
  }, [muted]);
  return [muted, setMuted] as const;
}

interface Props {
  feed: MatchFeed;
  prefs: ViewerPrefs;
  commentary: Commentary;
  onClose: () => void;
}

export function HighlightsReel({ feed, prefs, commentary, onClose }: Props) {
  const lang = prefs.language ?? "en";
  const { home, away } = feed;
  // Built once when the reel opens, so it plays the same way through even if late insights arrive.
  const [items] = useState<ReelItem[]>(() =>
    buildReel({
      moments: feed.moments,
      recap: feed.recap,
      insights: feed.insights,
      homeTeamId: home?.team_id ?? null,
      label: (m) => {
        const team = m.team_id === home?.team_id ? home : m.team_id === away?.team_id ? away : null;
        return `${momentLabel(lang, m.type)}${team ? ` · ${team.short_name}` : ""}`;
      },
    }),
  );
  const [s, dispatch] = useReducer(reelReducer, items.length, initialReel);
  const [muted, setMuted] = useReelMute();
  const [scoredStep, setScoredStep] = useState(-1);
  const rootRef = useRef<HTMLDivElement>(null);
  const player = useMemo(() => new SpeechPlayer(), []);
  const item = s.phase === "moment" ? items[s.index] : null;

  // The live co-commentator stays quiet while the reel talks.
  const { hold } = commentary;
  useEffect(() => {
    hold(true);
    player.probe();
    if ("speechSynthesis" in window) window.speechSynthesis.getVoices(); // warm the voice list for the fallback
    return () => {
      hold(false);
      player.stop();
    };
  }, [hold, player]);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => undefined);
    else rootRef.current?.requestFullscreen().catch(() => undefined);
  }, []);

  const close = useCallback(() => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => undefined);
    onClose();
  }, [onClose]);

  // Keys go to the reel first (see keyGate): every other shortcut on the page is off while it plays.
  useEffect(
    () =>
      grabKeys((e) => {
        if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return false;
        switch (e.key) {
          case " ":
            e.preventDefault();
            if (!e.repeat) dispatch({ type: "toggle_pause" });
            return true;
          case "ArrowRight":
            e.preventDefault();
            dispatch({ type: "next" });
            return true;
          case "ArrowLeft":
            e.preventDefault();
            dispatch({ type: "prev" });
            return true;
          case "Escape":
            close();
            return true;
        }
        if (e.repeat) return e.key.length === 1;
        const k = e.key.toLowerCase();
        if (k === "m") setMuted((v) => !v);
        else if (k === "f") toggleFullscreen();
        else if (k === "h") dispatch({ type: "restart" });
        // Swallow other printable keys (V, R, I, C, N, …) so nothing changes behind the reel.
        return e.key.length === 1;
      }),
    [close, setMuted, toggleFullscreen],
  );

  // Intro card timer.
  useEffect(() => {
    if (s.phase !== "intro" || s.paused) return;
    const step = s.step;
    const t = window.setTimeout(() => dispatch({ type: "intro_done", step }), INTRO_MS);
    return () => window.clearTimeout(t);
  }, [s.phase, s.paused, s.step]);

  // Safety net: a moment never waits longer than its cap for the replay or the voice.
  useEffect(() => {
    if (!item || s.paused) return;
    const step = s.step;
    const t = window.setTimeout(() => dispatch({ type: "timeout", step }), stepCapMs(item, muted));
    return () => window.clearTimeout(t);
  }, [item, s.paused, s.step, muted]);

  // The spoken line: starts with the replay; pausing stops it and resuming says it again from the top.
  useEffect(() => {
    if (!item || s.paused || s.speechDone) return;
    const step = s.step;
    if (muted || !item.speech) {
      dispatch({ type: "speech_done", step });
      return;
    }
    let live = true;
    // A short beat lets the lower third land first, and keeps a cancel() immediately followed by speak() (which
    // Chrome's speechSynthesis can drop silently) out of quick re-renders.
    const t = window.setTimeout(() => {
      player.speak(item.speech!, item.lang ?? lang).then(() => live && dispatch({ type: "speech_done", step }));
    }, SPEECH_DELAY_MS);
    return () => {
      live = false;
      window.clearTimeout(t);
      player.stop();
    };
  }, [item, s.paused, s.speechDone, s.step, muted, player, lang]);

  const onReplayDone = useCallback((step: number) => dispatch({ type: "replay_done", step }), []);
  const onReplayEnd = useCallback((step: number) => setScoredStep(step), []);

  const finalScore: Score = feed.state
    ? { home: feed.state.score_home ?? 0, away: feed.state.score_away ?? 0 }
    : (items[items.length - 1]?.scoreAfter ?? { home: 0, away: 0 });
  const score = item && (scoredStep === s.step || s.replayDone) ? item.scoreAfter : item?.scoreBefore;
  const persona = personaLabel(prefs.persona ?? undefined);
  const language = LANGUAGES.find((l) => l.code === lang)?.label ?? lang.toUpperCase();
  const title = `Your highlights · ${home?.name ?? "Home"} v ${away?.name ?? "Away"} · ${persona} · ${language}`;

  return (
    <div ref={rootRef} role="dialog" aria-modal="true" aria-label={title} className="group fixed inset-0 z-[70] overflow-hidden bg-black text-ink-50">
      {item ? (
        <ReelReplay
          key={`replay-${s.step}`}
          item={item}
          step={s.step}
          feed={feed}
          paused={s.paused}
          followedPlayerId={prefs.followed_player_id}
          onDone={onReplayDone}
          onEnd={onReplayEnd}
        />
      ) : (
        <div className="absolute inset-0 bg-pitch">
          <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_0%,rgba(0,0,0,0.75)_75%)]" />
        </div>
      )}

      {item && score && (
        <div className="absolute left-1/2 top-5 -translate-x-1/2">
          <ReelScoreBug home={home} away={away} score={score} clock={minuteLabel(item.moment.clock_ms, item.moment.period, feed.periodStarts)} />
        </div>
      )}

      {s.phase === "intro" && <IntroCard key={`intro-${s.step}`} home={home} away={away} persona={persona} language={language} count={items.length} paused={s.paused} hasRecap={!!feed.recap} />}
      {item && <LowerThird key={`lower-${s.step}`} item={item} lang={lang} feed={feed} index={s.index} n={items.length} />}
      {s.phase === "outro" && <OutroCard feed={feed} score={finalScore} onRestart={() => dispatch({ type: "restart" })} onClose={close} />}

      {s.paused && s.phase !== "outro" && (
        <div className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-md bg-ink-950/85 px-5 py-2 ring-1 ring-white/10">
          <span className="font-display text-2xl font-bold tracking-[0.3em] text-ink-50">PAUSED</span>
        </div>
      )}

      {/* Progress: one segment per moment, at the very bottom edge. */}
      <div className="absolute inset-x-0 bottom-0 flex h-1 gap-1 px-1" aria-hidden>
        {items.map((it, i) => {
          const done = s.phase === "outro" || (s.phase === "moment" && i < s.index);
          const now = s.phase === "moment" && i === s.index;
          return <span key={it.moment.moment_id} className={`flex-1 rounded-full ${done ? "bg-signal" : now ? "bg-signal/60" : "bg-white/15"}`} />;
        })}
      </div>

      {/* Controls: only on hover / focus, so a screen recording stays clean. */}
      <div
        role="toolbar"
        aria-label="Highlights controls"
        className="absolute right-4 top-4 flex gap-1.5 opacity-0 transition-opacity duration-300 focus-within:opacity-100 group-hover:opacity-100"
      >
        <CtlButton onClick={() => dispatch({ type: "prev" })} label="Previous" kbd="←" />
        <CtlButton onClick={() => dispatch({ type: "toggle_pause" })} label={s.paused ? "Play" : "Pause"} kbd="Space" disabled={s.phase === "outro"} />
        <CtlButton onClick={() => dispatch({ type: "next" })} label="Next" kbd="→" disabled={s.phase === "outro"} />
        <CtlButton onClick={() => setMuted((v) => !v)} label={muted ? "Sound off" : "Sound on"} kbd="M" pressed={!muted} />
        <CtlButton onClick={toggleFullscreen} label="Full screen" kbd="F" />
        <CtlButton onClick={close} label="Close" kbd="Esc" />
      </div>
    </div>
  );
}

function CtlButton({ onClick, label, kbd, disabled, pressed }: { onClick: () => void; label: string; kbd: string; disabled?: boolean; pressed?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={pressed}
      className="rounded-md bg-ink-900/90 px-3 py-1.5 text-sm text-ink-100 ring-1 ring-ink-700 hover:text-ink-50 disabled:opacity-40"
    >
      {label} <kbd className="ml-1 text-ink-500">{kbd}</kbd>
    </button>
  );
}

function ReelScoreBug({ home, away, score, clock }: { home: Team | null; away: Team | null; score: Score; clock: string }) {
  return (
    <div className="flex items-stretch overflow-hidden rounded-md bg-ink-950/92 ring-1 ring-white/10">
      <span className="font-display flex items-center bg-signal px-2.5 text-sm font-bold tracking-[0.22em] text-ink-950">HIGHLIGHTS</span>
      <div className="flex items-center gap-2 px-3 py-1.5">
        <KitStripe team={home} className="h-6 w-1" />
        <span className="font-display text-xl font-semibold">{home?.short_name}</span>
        <span key={`${score.home}-${score.away}`} className="font-display feed-in px-2 text-2xl font-bold tabular-nums">
          {score.home}–{score.away}
        </span>
        <span className="font-display text-xl font-semibold">{away?.short_name}</span>
        <KitStripe team={away} className="h-6 w-1" />
      </div>
      <div className="font-display flex min-w-[3.5rem] items-center justify-center bg-ink-800 px-3 text-xl font-semibold">{clock}</div>
    </div>
  );
}

interface ReplayProps {
  item: ReelItem;
  step: number;
  feed: MatchFeed;
  paused: boolean;
  followedPlayerId?: string | null;
  onDone: (step: number) => void;
  /** The run reached its last event (the score bug flips to the score after a goal). */
  onEnd: (step: number) => void;
}

/** One moment's run of play on the big pitch, played once (pausable); reports done after the hold. */
function ReelReplay({ item, step, feed, paused, followedPlayerId, onDone, onEnd }: ReplayProps) {
  const { moment } = item;
  const done = useCallback(() => onDone(step), [onDone, step]);
  const replay = useMomentReplay(moment, feed.home, feed.away, 1, done, paused);
  const names = useSquadNames(feed.home, feed.away);
  const { status, step: at } = replay;

  useEffect(() => {
    if (status !== "empty" && status !== "error") return;
    const t = window.setTimeout(done, EMPTY_REPLAY_MS);
    return () => window.clearTimeout(t);
  }, [status, done]);

  const ended = status === "playing" && at.n > 0 && at.i >= at.n;
  useEffect(() => {
    if (ended) onEnd(step);
  }, [ended, onEnd, step]);

  return (
    <div className="absolute inset-0">
      {status === "playing" ? (
        <Pitch model={replay.model} followedPlayerId={followedPlayerId} highlightIds={moment.player_ids ?? []} label={`Highlight: ${item.headline}`} />
      ) : (
        <div className="absolute inset-0 bg-pitch" />
      )}
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_top,rgba(0,0,0,0.7)_0%,transparent_38%)]" aria-hidden />
      <div className="absolute bottom-5 right-5 flex items-center gap-2 rounded-md bg-ink-950/80 px-3 py-1 text-xs text-ink-300 ring-1 ring-white/10">
        <span className="max-w-[16rem] truncate capitalize">{replayCaption(status, at.ev, names)}</span>
        {status === "playing" && at.n > 0 && (
          <span className="h-1 w-16 shrink-0 overflow-hidden rounded-full bg-ink-700" aria-hidden>
            <span className="block h-full bg-signal transition-[width] duration-300" style={{ width: `${(100 * at.i) / at.n}%` }} />
          </span>
        )}
      </div>
    </div>
  );
}

function LowerThird({ item, lang, feed, index, n }: { item: ReelItem; lang: string; feed: MatchFeed; index: number; n: number }) {
  const m = item.moment;
  const team = m.team_id === feed.home?.team_id ? feed.home : m.team_id === feed.away?.team_id ? feed.away : null;
  return (
    <div className="cue-enter absolute bottom-8 left-6 right-6 max-w-[760px] sm:left-8" aria-live="polite">
      <div className="flex items-stretch overflow-hidden rounded-t-md">
        <span className="font-display flex items-center bg-signal px-3 py-1 text-2xl font-bold text-ink-950 tabular-nums">
          {minuteLabel(m.clock_ms, m.period, feed.periodStarts)}
        </span>
        <span className="flex items-center gap-2 bg-ink-950/92 px-3 text-sm font-semibold uppercase tracking-[0.18em] text-ink-100">
          <KitStripe team={team} className="h-4 w-1" />
          {momentLabel(lang, m.type)}
          <span className="font-normal tracking-normal text-ink-500">
            {index + 1}/{n}
          </span>
        </span>
      </div>
      <div className="rounded-b-md rounded-tr-md bg-ink-950/92 px-5 py-3 ring-1 ring-white/10">
        <p className="font-display text-3xl font-semibold leading-tight text-ink-50 sm:text-4xl">{item.headline}</p>
        {item.line && <p className="mt-1 text-lg leading-snug text-ink-300">{item.line}</p>}
      </div>
    </div>
  );
}

function IntroCard(p: { home: Team | null; away: Team | null; persona: string; language: string; count: number; paused: boolean; hasRecap: boolean }) {
  return (
    <div className="absolute inset-0 flex items-center justify-center p-6">
      <div className="cue-enter w-full max-w-6xl text-center">
        <div className="font-display text-lg font-bold tracking-[0.35em] text-signal">YOUR HIGHLIGHTS</div>
        <div className="mt-4 flex flex-wrap items-center justify-center gap-x-5 gap-y-2">
          <span className="flex items-center gap-3">
            <KitStripe team={p.home} className="h-12 w-2" />
            <span className="font-display whitespace-nowrap text-5xl font-semibold sm:text-6xl">{p.home?.name}</span>
          </span>
          <span className="font-display text-3xl text-ink-400">v</span>
          <span className="flex items-center gap-3">
            <span className="font-display whitespace-nowrap text-5xl font-semibold sm:text-6xl">{p.away?.name}</span>
            <KitStripe team={p.away} className="h-12 w-2" />
          </span>
        </div>
        <div className="mt-5 text-xl text-ink-300">
          {p.persona} · {p.language} · {p.count} {p.count === 1 ? "moment" : "moments"}
          {!p.hasRecap && " · top moments by significance"}
        </div>
        <div className="mx-auto mt-6 h-1 w-48 overflow-hidden rounded-full bg-white/10">
          <span
            className="cue-timer block h-full bg-signal"
            style={{ animationDuration: `${INTRO_MS}ms`, animationPlayState: p.paused ? "paused" : "running", animationDirection: "reverse" }}
          />
        </div>
      </div>
    </div>
  );
}

function OutroCard({ feed, score, onRestart, onClose }: { feed: MatchFeed; score: Score; onRestart: () => void; onClose: () => void }) {
  const { recap, home, away } = feed;
  const potm = recap?.player_of_the_match_id
    ? [...(home?.players ?? []), ...(away?.players ?? [])].find((p) => p.player_id === recap.player_of_the_match_id)
    : undefined;
  const potmTeam = potm && home?.players.includes(potm) ? home : away;
  const post = recap?.social_posts?.[0];
  const restartRef = useRef<HTMLButtonElement>(null);
  useEffect(() => restartRef.current?.focus({ preventScroll: true }), []);

  return (
    <div className="absolute inset-0 flex items-center justify-center bg-black/55 p-6">
      <div className="cue-enter w-full max-w-5xl">
        <div className="font-display text-center text-lg font-bold tracking-[0.35em] text-signal">FULL TIME</div>
        <div className="mt-3 flex flex-wrap items-center justify-center gap-x-6 gap-y-2">
          <span className="flex items-center gap-3">
            <KitStripe team={home} className="h-12 w-2" />
            <span className="font-display text-4xl font-semibold sm:text-5xl">{home?.name}</span>
          </span>
          <span className="font-display text-7xl font-bold leading-none tabular-nums">
            {score.home}–{score.away}
          </span>
          <span className="flex items-center gap-3">
            <span className="font-display text-4xl font-semibold sm:text-5xl">{away?.name}</span>
            <KitStripe team={away} className="h-12 w-2" />
          </span>
        </div>

        {(potm || post) && (
          <div className="mt-8 flex flex-wrap justify-center gap-4">
            {potm && (
              <div className="flex items-center gap-4 rounded-md bg-ink-950/85 p-4 ring-1 ring-white/10">
                <span
                  className="font-display flex h-14 w-14 shrink-0 items-center justify-center rounded-md text-3xl font-bold"
                  style={{ background: potmTeam?.primary_colour, color: potmTeam?.secondary_colour }}
                >
                  {potm.shirt_number}
                </span>
                <div>
                  <div className="text-xs uppercase tracking-[0.18em] text-ink-400">Player of the match</div>
                  <div className="font-display text-2xl font-semibold leading-tight">{potm.name}</div>
                  <div className="text-sm text-ink-300">{potmTeam?.name}</div>
                </div>
              </div>
            )}
            {post && (
              <blockquote className="min-w-[280px] max-w-xl flex-1 rounded-md bg-ink-950/85 p-4 ring-1 ring-white/10">
                <div className="text-xs uppercase tracking-[0.18em] text-ink-400">Share it</div>
                <p className="mt-1 text-lg leading-snug text-ink-100">{post}</p>
              </blockquote>
            )}
          </div>
        )}
        {!recap && <p className="mt-6 text-center text-ink-300">The reporter is still writing your recap; this reel used the top moments by significance.</p>}

        <div className="mt-8 flex justify-center gap-2">
          <button ref={restartRef} type="button" onClick={onRestart} className="rounded-md bg-signal px-4 py-2 text-sm font-semibold text-ink-950">
            Watch again <kbd className="ml-1 text-ink-800">H</kbd>
          </button>
          <button type="button" onClick={onClose} className="rounded-md bg-ink-900/90 px-4 py-2 text-sm text-ink-100 ring-1 ring-ink-700 hover:text-ink-50">
            Close <kbd className="ml-1 text-ink-500">Esc</kbd>
          </button>
        </div>
      </div>
    </div>
  );
}
