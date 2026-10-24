import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { describeEvent, momentPriority } from "./describe";
import { SpeechPlayer, type SpeechEngine } from "./speech";
import { clear, emptyQueue, enqueue, finish, takeNext, type SpeechItem, type SpeechKind } from "./speechQueue";
import type { MatchFeed } from "./useMatchFeed";

/** Browser-local audio settings (not viewer prefs: they never go to the server). */
export interface AudioPrefs {
  /** Speak each new insight's commentary line, like a co-commentator. */
  voice: boolean;
  /** Audio description: speak key events (goals, cards, chances, subs, half/full time, score) plus commentary. */
  description: boolean;
}

const AUDIO_KEY = "fulcrum.audio";
const AUDIO_DEFAULT: AudioPrefs = { voice: false, description: false };

/** Voice / audio-description toggles, persisted in localStorage like the viewer prefs. */
export function useAudioPrefs() {
  const [audio, setAudio] = useState<AudioPrefs>(() => {
    try {
      const raw = localStorage.getItem(AUDIO_KEY);
      if (raw) return { ...AUDIO_DEFAULT, ...JSON.parse(raw) };
    } catch {
      /* storage unavailable */
    }
    return AUDIO_DEFAULT;
  });
  useEffect(() => {
    try {
      localStorage.setItem(AUDIO_KEY, JSON.stringify(audio));
    } catch {
      /* storage unavailable */
    }
  }, [audio]);
  const update = useCallback((patch: Partial<AudioPrefs>) => setAudio((a) => ({ ...a, ...patch })), []);
  return [audio, update] as const;
}

/** A line for the screen-reader live region. */
export interface Announcement {
  key: number;
  text: string;
  assertive: boolean;
}

export interface Commentary {
  engine: SpeechEngine;
  /** The line being spoken now. */
  speaking: SpeechItem | null;
  /** True after the browser refused to play audio before a user gesture. */
  blocked: boolean;
  /** Recent lines for the aria-live region (always produced, whether or not audio is on). */
  announcements: Announcement[];
  /** Stop the current line and drop everything queued. */
  stop: () => void;
}

const ANNOUNCE_KEEP = 5;

/**
 * Drives voice commentary and audio description from the live feed: new insights (not the catch-up snapshot) and
 * new key events go through one speech queue (see speechQueue.ts) and into the live-region announcements.
 */
export function useCommentary(feed: MatchFeed, lang: string, audio: AudioPrefs): Commentary {
  const player = useMemo(() => new SpeechPlayer(), []);
  const [engine, setEngine] = useState<SpeechEngine>("azure");
  const [speaking, setSpeaking] = useState<SpeechItem | null>(null);
  const [blocked, setBlocked] = useState(false);
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const q = useRef(emptyQueue());
  const busy = useRef(false);
  const clockRef = useRef(0);
  clockRef.current = feed.state?.clock_ms ?? clockRef.current;
  const audioRef = useRef(audio);
  audioRef.current = audio;
  const langRef = useRef(lang);
  langRef.current = lang;
  const announceKey = useRef(0);

  useEffect(() => {
    player.onBlocked = () => setBlocked(true);
    player.probe().then(setEngine);
    return () => player.stop();
  }, [player]);

  // Any key press or click is a user gesture: the browser will let audio play again.
  useEffect(() => {
    if (!blocked) return;
    const clearBlocked = () => setBlocked(false);
    window.addEventListener("pointerdown", clearBlocked, { once: true });
    window.addEventListener("keydown", clearBlocked, { once: true });
    return () => {
      window.removeEventListener("pointerdown", clearBlocked);
      window.removeEventListener("keydown", clearBlocked);
    };
  }, [blocked]);

  const pump = useCallback(() => {
    if (busy.current) return;
    const { state, item } = takeNext(q.current, clockRef.current, Date.now());
    q.current = state;
    if (!item) return;
    busy.current = true;
    setSpeaking(item);
    player.speak(item.text, item.lang).finally(() => {
      busy.current = false;
      q.current = finish(q.current);
      setSpeaking(null);
      setEngine(player.engine);
      pump();
    });
  }, [player]);

  const say = useCallback(
    (id: string, text: string, priority: number, kind: SpeechKind, clockMs: number) => {
      const a = audioRef.current;
      const wanted = kind === "description" ? a.description : a.voice || a.description;
      if (!wanted) return;
      const item: SpeechItem = { id, text, lang: langRef.current, priority, kind, clockMs, wallMs: Date.now() };
      const r = enqueue(q.current, item);
      q.current = r.state;
      if (r.preempt) player.stop(); // the finally() above pumps the higher-priority line next
      else pump();
    },
    [player, pump],
  );

  const announce = useCallback((text: string, assertive: boolean) => {
    const key = ++announceKey.current;
    setAnnouncements((list) => [...list.slice(-(ANNOUNCE_KEEP - 1)), { key, text, assertive }]);
  }, []);

  const stop = useCallback(() => {
    q.current = clear(q.current);
    player.stop();
  }, [player]);

  // Turning both toggles off silences everything at once.
  const anyOn = audio.voice || audio.description;
  useEffect(() => {
    if (!anyOn) stop();
  }, [anyOn, stop]);
  useEffect(() => {
    if (!audio.description) q.current = { ...q.current, queue: q.current.queue.filter((i) => i.kind !== "description") };
  }, [audio.description]);

  // ---- insights: speak only the ones that arrive live (after the hello snapshot of each epoch)
  const seenEpoch = useRef(-1);
  const seenMoments = useRef(new Set<string>());
  useEffect(() => {
    const ids = Object.keys(feed.insights);
    if (seenEpoch.current !== feed.epoch) {
      seenEpoch.current = feed.epoch;
      seenMoments.current = new Set(ids);
      return;
    }
    for (const id of ids) {
      if (seenMoments.current.has(id)) continue;
      seenMoments.current.add(id);
      const insight = feed.insights[id];
      const line = insight.variants[0]?.commentary_line?.trim();
      if (!line) continue;
      const moment = feed.moments.find((m) => m.moment_id === id);
      const priority = momentPriority(moment?.type);
      announce(line, false);
      say(`insight:${id}`, line, priority, "commentary", clockRef.current);
    }
  }, [feed.insights, feed.epoch, feed.moments, announce, say]);

  // ---- events: describe key events as they happen
  const lastSeq = useRef<{ match: string | null; seq: number }>({ match: null, seq: -1 });
  useEffect(() => {
    if (lastSeq.current.match !== feed.matchId) lastSeq.current = { match: feed.matchId, seq: -1 };
    for (const ev of feed.recentEvents) {
      if (ev.seq <= lastSeq.current.seq) continue;
      lastSeq.current.seq = ev.seq;
      const d = describeEvent(ev, { home: feed.home, away: feed.away, lang: langRef.current });
      if (!d) continue;
      announce(d.text, d.assertive);
      say(`event:${d.id}`, d.text, d.priority, "description", ev.clock_ms);
    }
  }, [feed.recentEvents, feed.matchId, feed.home, feed.away, announce, say]);

  return { engine, speaking, blocked, announcements, stop };
}
