import { useState } from "react";
import { overlayCuesUrl } from "../lib/api";
import type { MatchFeed } from "../lib/useMatchFeed";
import type { OverlayCue, ViewerPrefs } from "../types";

/** Syntax-tinted JSON for one cue (keys muted, strings bright, numbers in the accent). */
function JsonView({ cue }: { cue: OverlayCue }) {
  const json = JSON.stringify(cue, null, 2);
  const parts = json.split(/("(?:[^"\\]|\\.)*"(?:\s*:)?|-?\d+(?:\.\d+)?|true|false|null)/g);
  return (
    <pre className="overflow-x-auto font-mono text-[11.5px] leading-[1.55] text-ink-400">
      {parts.map((p, i) => {
        if (/^".*":$/.test(p.replace(/\s/g, ""))) return <span key={i} className="text-ink-300">{p}</span>;
        if (p.startsWith('"')) return <span key={i} className="text-ink-50">{p}</span>;
        if (/^-?\d/.test(p)) return <span key={i} className="text-signal">{p}</span>;
        if (p === "true" || p === "false" || p === "null") return <span key={i} className="text-ink-300">{p}</span>;
        return <span key={i}>{p}</span>;
      })}
    </pre>
  );
}

/** Raw OverlayCue stream — proof that the overlay is machine-readable for a broadcast graphics partner. */
export function CueInspector({ feed, prefs, onClose }: { feed: MatchFeed; prefs: ViewerPrefs; onClose: () => void }) {
  const [open, setOpen] = useState<string | null>(null);
  const cues = [...feed.cues].reverse();
  const url = feed.matchId ? overlayCuesUrl(feed.matchId, prefs) : "";
  const [copied, setCopied] = useState(false);

  return (
    <aside className="flex h-full w-[min(500px,100vw)] flex-col border-l border-ink-700 bg-ink-950/95 backdrop-blur-md" aria-label="Cue inspector">
      <header className="border-b border-ink-700 px-5 py-4">
        <div className="flex items-center justify-between">
          <h2 className="font-display text-2xl font-semibold">Cue inspector</h2>
          <button onClick={onClose} className="rounded-md px-2.5 py-1 text-sm text-ink-300 ring-1 ring-ink-700 hover:text-ink-50">
            Hide <kbd className="ml-1 text-ink-500">I</kbd>
          </button>
        </div>
        <p className="mt-1 text-sm text-ink-300">
          The same <code className="text-ink-100">OverlayCue</code> messages that drive this overlay, as a graphics partner would receive them.
        </p>
        {url && (
          <div className="mt-3 flex items-center gap-2 rounded-md bg-ink-900 px-3 py-2 ring-1 ring-ink-700">
            <span className="rounded-sm bg-signal px-1.5 text-[11px] font-bold text-ink-950">GET</span>
            <code className="min-w-0 flex-1 truncate text-xs text-ink-100">{url.replace(/^https?:\/\/[^/]+/, "")}</code>
            <button
              className="text-xs text-ink-300 hover:text-ink-50"
              onClick={() => {
                navigator.clipboard?.writeText(url).then(() => {
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1500);
                });
              }}
            >
              {copied ? "Copied" : "Copy URL"}
            </button>
          </div>
        )}
        <div className="mt-3 flex gap-4 text-xs text-ink-400">
          <span>
            <span className="font-display text-base font-semibold text-ink-50">{feed.cues.length}</span> cues received
          </span>
          <span>
            persona <span className="text-ink-100">{prefs.persona}</span> · language <span className="text-ink-100">{prefs.language}</span>
          </span>
        </div>
      </header>
      <ol className="scroll-thin flex-1 overflow-y-auto">
        {cues.map((c, i) => {
          const id = c.cue_id ?? String(i);
          const expanded = open === id || (open === null && i === 0);
          return (
            <li key={id} className="feed-in border-b border-ink-800">
              <button className="flex w-full items-center gap-3 px-5 py-2.5 text-left hover:bg-ink-900" onClick={() => setOpen(expanded ? "" : id)} aria-expanded={expanded}>
                <span className="w-24 shrink-0 font-mono text-xs text-signal">{c.template}</span>
                <span className="w-24 shrink-0 font-mono text-xs text-ink-400">{c.anchor}</span>
                <span className="font-mono text-xs text-ink-400">p{c.priority}</span>
                <span className="min-w-0 flex-1 truncate text-sm text-ink-100">{c.headline}</span>
              </button>
              {expanded && (
                <div className="px-5 pb-3">
                  <JsonView cue={c} />
                </div>
              )}
            </li>
          );
        })}
        {!cues.length && <li className="px-5 py-6 text-sm text-ink-400">No cues yet. They arrive a few seconds after each moment, once the agents have explained it.</li>}
      </ol>
    </aside>
  );
}
