import { LANGUAGES, personaLabel } from "../lib/i18n";
import type { ConnStatus } from "../lib/useMatchFeed";
import type { ViewerPrefs } from "../types";

export type Route = "studio" | "split" | "broadcast" | "recap";

const NAV: { id: Route; label: string }[] = [
  { id: "studio", label: "Match Studio" },
  { id: "split", label: "Split view" },
  { id: "broadcast", label: "Broadcast" },
  { id: "recap", label: "Recap" },
];

export function Logo({ className = "" }: { className?: string }) {
  return (
    <span className={`flex items-center gap-2 ${className}`}>
      <svg viewBox="0 0 32 32" className="h-6 w-6" aria-hidden>
        <path d="M4 23h24" stroke="#3de0ff" strokeWidth="3" strokeLinecap="round" />
        <path d="M16 22 10 10h12z" fill="#3de0ff" />
      </svg>
      <span className="font-display text-2xl font-bold tracking-wide text-ink-50">Fulcrum</span>
    </span>
  );
}

export function StatusPill({ status, error }: { status: ConnStatus; error: string | null }) {
  const live = status === "live";
  return (
    <span title={error ?? undefined} className="flex items-center gap-2 rounded-full px-2.5 py-1 text-xs text-ink-300 ring-1 ring-ink-700">
      <span className={`h-1.5 w-1.5 rounded-full ${live ? "live-dot bg-signal" : status === "offline" ? "bg-ink-500" : "bg-ink-300"}`} />
      {live ? "Live feed" : status === "connecting" ? "Connecting" : status === "reconnecting" ? "Reconnecting" : "API offline"}
    </span>
  );
}

interface Props {
  route: Route;
  status?: ConnStatus;
  error?: string | null;
  prefs?: ViewerPrefs;
  onOpenPrefs?: () => void;
}

export function Header({ route, status, error, prefs, onOpenPrefs }: Props) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-8 border-b border-ink-700 bg-ink-950 px-6">
      <Logo />
      <nav className="flex h-full items-stretch gap-1" aria-label="Views">
        {NAV.map((n) => (
          <a
            key={n.id}
            href={`#/${n.id === "studio" ? "" : n.id}`}
            aria-current={route === n.id ? "page" : undefined}
            className={`flex items-center border-b-2 px-3 text-sm font-medium transition-colors ${route === n.id ? "border-signal text-ink-50" : "border-transparent text-ink-400 hover:text-ink-100"}`}
          >
            {n.label}
          </a>
        ))}
      </nav>
      <div className="ml-auto flex items-center gap-3">
        {status && <StatusPill status={status} error={error ?? null} />}
        {prefs && onOpenPrefs && (
          <button
            onClick={onOpenPrefs}
            className="flex items-center gap-2 rounded-md bg-ink-850 px-3 py-1.5 text-sm ring-1 ring-ink-700 hover:ring-ink-500"
          >
            <span className="text-ink-400">Viewing as</span>
            <span className="font-semibold text-ink-50">{personaLabel(prefs.persona)}</span>
            <span className="rounded-sm bg-ink-700 px-1.5 text-xs font-semibold uppercase text-ink-100">
              {LANGUAGES.find((l) => l.code === prefs.language)?.code ?? "en"}
            </span>
          </button>
        )}
      </div>
    </header>
  );
}
