import { useEffect, useRef, useState, type ReactNode } from "react";
import { controlStatus, demoRequested, SPEEDS, speedLabel, stepSpeed } from "../lib/director";
import { momentLabel } from "../lib/i18n";
import { useDirector } from "../lib/useDirector";
import type { MatchFeed } from "../lib/useMatchFeed";
import { isTyping } from "./AudioControls";

type Mode = "off" | "shown" | "hidden";

// Broadcast keeps the corners and the bottom centre for overlay cues, so the bar sits under the score bug there.
const PLACEMENT = { "bottom-left": "bottom-4 left-4", "top-center": "top-[4.5rem] left-1/2 -translate-x-1/2" };

/**
 * Demo director bar (Match Studio + Broadcast): new demo match, speed, pause / resume, next moment.
 * Off by default; `?demo=1` or C shows it. C again hides the bar but keeps the shortcuts, for clean recordings:
 * Space pause / resume · ] faster · [ slower · N next moment.
 */
export function DemoPanel({ feed, lang, placement = "bottom-left" }: { feed: MatchFeed; lang: string; placement?: "bottom-left" | "top-center" }) {
  const [mode, setMode] = useState<Mode>(() => (demoRequested(window.location.search) ? "shown" : "off"));
  const d = useDirector(feed.matchId, mode !== "off");
  const ctl = d.control;
  const live = !!ctl && !d.unavailable;
  const dRef = useRef(d);
  dRef.current = d;
  const liveRef = useRef(live);
  liveRef.current = live;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
      if (e.key === "c" || e.key === "C") {
        setMode((m) => (m === "shown" ? "hidden" : "shown"));
        return;
      }
      if (mode === "off" || !liveRef.current) return;
      const dir = dRef.current;
      if (e.key === " ") {
        e.preventDefault(); // no page scroll, no click on a focused button
        dir.togglePause();
      } else if (e.key === "]") dir.setSpeed(stepSpeed(dir.speed, 1));
      else if (e.key === "[") dir.setSpeed(stepSpeed(dir.speed, -1));
      else if (e.key === "n" || e.key === "N") dir.nextMoment();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode]);

  if (mode !== "shown") return null;

  const status = d.unavailable ?? controlStatus(ctl, (t) => momentLabel(lang, t));
  const effective = ctl?.skipping ? ctl.effective_speed : d.speed;
  return (
    <div
      role="toolbar"
      aria-label="Demo controls"
      className={`feed-in fixed z-50 ${PLACEMENT[placement]} flex items-center gap-1 rounded-lg bg-ink-950/92 p-1 text-sm text-ink-100 shadow-2xl shadow-black/60 ring-1 ring-white/10 backdrop-blur`}
    >
      <span className="font-display px-2 text-xs font-bold tracking-[0.22em] text-signal">DIRECTOR</span>
      <BarButton onClick={d.newDemoMatch} disabled={d.busy} title="Start seed 11, Harlow Cross v Marsh Dock United, at the selected speed">
        New demo match
      </BarButton>
      <Divider />
      <div className="flex items-center rounded-md bg-ink-900 p-0.5 ring-1 ring-ink-700" role="group" aria-label="Speed">
        {SPEEDS.map((s) => {
          const on = d.speed === s;
          return (
            <button
              key={s}
              type="button"
              aria-pressed={on}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => d.setSpeed(s)}
              className={`font-display min-w-10 rounded px-2 py-0.5 text-base font-semibold tabular-nums transition-colors ${on ? "bg-signal text-ink-950" : "text-ink-300 hover:text-ink-50"}`}
            >
              {speedLabel(s)}
            </button>
          );
        })}
      </div>
      <BarButton onClick={d.togglePause} disabled={!live || d.busy} title={ctl?.paused ? "Resume (Space)" : "Pause (Space)"} pressed={!!ctl?.paused}>
        <span aria-hidden className="inline-block w-3 text-center">{ctl?.paused ? "▶" : "❚❚"}</span>
        {ctl?.paused ? "Resume" : "Pause"}
      </BarButton>
      <BarButton onClick={d.nextMoment} disabled={!live || d.busy || !!ctl?.skipping} title="Run fast to the next goal, red card or big chance (N)">
        Next moment <span aria-hidden>⏭</span>
      </BarButton>
      <Divider />
      <span className="flex min-w-[11rem] items-center gap-2 px-2 text-xs text-ink-300" aria-live="polite">
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${ctl?.skipping ? "bg-signal motion-safe:animate-pulse" : ctl?.paused || d.unavailable ? "bg-ink-500" : "live-dot bg-signal"}`}
          aria-hidden
        />
        <span className="font-display text-sm font-semibold tabular-nums text-ink-50">{speedLabel(effective)}</span>
        <span className="truncate">{status}</span>
      </span>
      <button
        type="button"
        onClick={() => setMode("hidden")}
        title="Hide (C) — shortcuts stay on"
        aria-label="Hide demo controls"
        className="rounded px-1.5 text-base leading-none text-ink-500 hover:text-ink-50"
      >
        ×
      </button>
    </div>
  );
}

function BarButton({ children, onClick, disabled, title, pressed }: { children: ReactNode; onClick: () => void; disabled?: boolean; title?: string; pressed?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseDown={(e) => e.preventDefault()}
      disabled={disabled}
      title={title}
      aria-pressed={pressed}
      className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 font-medium ring-1 transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${pressed ? "bg-ink-800 text-signal ring-signal/50" : "text-ink-100 ring-ink-700 hover:text-ink-50 hover:ring-ink-500"}`}
    >
      {children}
    </button>
  );
}

const Divider = () => <span className="mx-0.5 h-5 w-px bg-ink-700" aria-hidden />;
