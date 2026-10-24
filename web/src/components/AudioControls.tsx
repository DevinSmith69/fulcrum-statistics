import { useEffect } from "react";
import type { SpeechEngine } from "../lib/speech";
import type { AudioPrefs, Commentary } from "../lib/useCommentary";

/**
 * Visually hidden live regions for screen-reader users: key events and commentary lines are announced as they
 * happen (politely), goals and red cards assertively. Always present, whatever the audio toggles say.
 */
export function LiveRegion({ commentary }: { commentary: Commentary }) {
  const polite = commentary.announcements.filter((a) => !a.assertive);
  const assertive = commentary.announcements.filter((a) => a.assertive);
  return (
    <>
      <div className="sr-only" role="log" aria-live="polite" aria-relevant="additions" aria-label="Live match commentary">
        {polite.map((a) => (
          <p key={a.key}>{a.text}</p>
        ))}
      </div>
      <div className="sr-only" aria-live="assertive" aria-relevant="additions" aria-label="Goals and red cards">
        {assertive.map((a) => (
          <p key={a.key}>{a.text}</p>
        ))}
      </div>
    </>
  );
}

/** Whether a key press is going into a text field (shortcuts must ignore it). */
export const isTyping = (t: EventTarget | null) =>
  t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement || (t instanceof HTMLElement && t.isContentEditable);

/** Keyboard shortcuts: V voice on/off, D audio description on/off, S stop talking. */
export function useAudioShortcuts(audio: AudioPrefs, onChange: (p: Partial<AudioPrefs>) => void, stop: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
      const k = e.key.toLowerCase();
      if (k === "v") onChange({ voice: !audio.voice });
      else if (k === "d") onChange({ description: !audio.description });
      else if (k === "s") stop();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [audio, onChange, stop]);
}

const ENGINE_LABEL: Record<SpeechEngine, string> = {
  azure: "Azure AI Speech neural voice",
  browser: "your browser’s built-in voice",
  none: "no speech engine available in this browser",
};

/** Small "speaking" indicator; the pulse only animates when the viewer hasn't asked for reduced motion. */
export function SpeakingDot({ on }: { on: boolean }) {
  return (
    <span
      aria-hidden
      className={`inline-block h-2 w-2 rounded-full ${on ? "bg-signal motion-safe:animate-pulse" : "bg-ink-500"}`}
    />
  );
}

/** Voice / Stop buttons for the broadcast controls. */
export function BroadcastAudioButtons({ audio, onChange, commentary }: { audio: AudioPrefs; onChange: (p: Partial<AudioPrefs>) => void; commentary: Commentary }) {
  const on = audio.voice || audio.description;
  return (
    <>
      <button
        onClick={() => onChange({ voice: !audio.voice })}
        aria-pressed={audio.voice}
        title={`Voice commentary (${ENGINE_LABEL[commentary.engine]})`}
        className={`flex items-center gap-2 rounded-md px-3 py-1.5 text-sm ring-1 ${audio.voice ? "bg-signal font-semibold text-ink-950 ring-signal" : "bg-ink-900/90 text-ink-100 ring-ink-700 hover:text-ink-50"}`}
      >
        Voice <kbd className={audio.voice ? "text-ink-800" : "text-ink-500"}>V</kbd>
      </button>
      {on && (
        <button
          onClick={commentary.stop}
          className="flex items-center gap-2 rounded-md bg-ink-900/90 px-3 py-1.5 text-sm text-ink-100 ring-1 ring-ink-700 hover:text-ink-50"
        >
          <SpeakingDot on={!!commentary.speaking} />
          Stop <kbd className="text-ink-500">S</kbd>
        </button>
      )}
    </>
  );
}

function Toggle({ label, hint, checked, shortcut, onToggle }: { label: string; hint: string; checked: boolean; shortcut: string; onToggle: () => void }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={onToggle}
      className={`flex w-full items-start gap-3 rounded-md p-3 text-left ring-1 ${checked ? "bg-signal/10 ring-signal" : "bg-ink-850 ring-ink-700 hover:ring-ink-500"}`}
    >
      <span aria-hidden className={`mt-0.5 flex h-5 w-9 shrink-0 items-center rounded-full p-0.5 ${checked ? "bg-signal" : "bg-ink-600"}`}>
        <span className={`h-4 w-4 rounded-full bg-ink-950 motion-safe:transition-transform ${checked ? "translate-x-4" : ""}`} />
      </span>
      <span className="min-w-0 flex-1">
        <span className={`block text-sm font-semibold ${checked ? "text-signal" : "text-ink-50"}`}>
          {label} <kbd className="ml-1 text-xs font-normal text-ink-400">{shortcut}</kbd>
        </span>
        <span className="mt-0.5 block text-xs leading-snug text-ink-400">{hint}</span>
      </span>
    </button>
  );
}

/** The "Audio" section of the preferences drawer. */
export function AudioSettings({ audio, onChange, commentary }: { audio: AudioPrefs; onChange: (p: Partial<AudioPrefs>) => void; commentary: Commentary }) {
  const on = audio.voice || audio.description;
  return (
    <div className="space-y-2">
      <Toggle
        label="Voice commentary"
        hint="Speaks each new insight’s commentary line in your language, like a co-commentator."
        checked={audio.voice}
        shortcut="V"
        onToggle={() => onChange({ voice: !audio.voice })}
      />
      <Toggle
        label="Audio description"
        hint="For blind and low-vision fans: describes goals, cards, big chances, substitutions, half and full time and the score as they happen, plus the commentary lines."
        checked={audio.description}
        shortcut="D"
        onToggle={() => onChange({ description: !audio.description })}
      />
      <div className="flex items-center justify-between gap-3 pt-1 text-xs text-ink-400">
        <span className="flex items-center gap-2">
          <SpeakingDot on={!!commentary.speaking} />
          {on ? `Speaking with ${ENGINE_LABEL[commentary.engine]}.` : "Audio is off."}
        </span>
        <button onClick={commentary.stop} disabled={!on} className="rounded-md px-2.5 py-1 text-ink-200 ring-1 ring-ink-700 hover:text-ink-50 disabled:opacity-40">
          Stop <kbd className="ml-1 text-ink-500">S</kbd>
        </button>
      </div>
      {commentary.blocked && on && (
        <p className="text-xs text-signal" role="status">
          Your browser blocked audio until you interact with the page. Click anywhere or press a key to resume.
        </p>
      )}
      <p className="text-xs text-ink-500">
        Shortcuts: <kbd>V</kbd> voice on/off · <kbd>D</kbd> audio description on/off · <kbd>S</kbd> stop talking. Screen readers
        hear the same lines through Match Studio’s live region, even with audio off.
      </p>
    </div>
  );
}
