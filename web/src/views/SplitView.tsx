import { InsightFeed } from "../components/InsightFeed";
import { OverlayLayer } from "../components/OverlayLayer";
import { Pitch } from "../components/Pitch";
import { useTeams } from "../components/PrefsDrawer";
import { Scoreboard } from "../components/Scoreboard";
import { StatusPill } from "../components/Header";
import { LANGUAGES, PERSONAS } from "../lib/i18n";
import { useMatchFeed } from "../lib/useMatchFeed";
import { usePrefs } from "../lib/usePrefs";
import type { Persona, ViewerPrefs } from "../types";

function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { id: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="flex rounded-md bg-ink-850 p-0.5 ring-1 ring-ink-700">
      {options.map((o) => (
        <button
          key={o.id}
          role="radio"
          aria-checked={value === o.id}
          onClick={() => onChange(o.id)}
          className={`rounded-[5px] px-2.5 py-1 text-sm transition-colors ${value === o.id ? "bg-signal font-semibold text-ink-950" : "text-ink-300 hover:text-ink-50"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** One side of the split: its own WebSocket subscription and its own viewer prefs. */
function Pane({ storageKey, initial }: { storageKey: string; initial: Partial<ViewerPrefs> }) {
  const [prefs, setPrefs] = usePrefs(storageKey, initial);
  const feed = useMatchFeed(prefs);
  const teams = useTeams();
  const players = [...(feed.home?.players ?? []), ...(feed.away?.players ?? [])];
  const select = "rounded-md bg-ink-850 px-2 py-1.5 text-sm text-ink-100 ring-1 ring-ink-700 focus:outline-none focus:ring-signal";

  return (
    <section className="flex min-h-0 min-w-0 flex-col gap-3" aria-label={`Viewer: ${prefs.persona}, ${prefs.language}`}>
      <div className="panel flex flex-wrap items-center gap-2 p-2.5">
        <Segmented
          label="Persona"
          value={(prefs.persona ?? "casual") as Persona}
          options={PERSONAS.map((p) => ({ id: p.id as Persona, label: p.label }))}
          onChange={(persona) => setPrefs({ persona })}
        />
        <Segmented
          label="Language"
          value={prefs.language ?? "en"}
          options={LANGUAGES.map((l) => ({ id: l.code, label: l.code.toUpperCase() }))}
          onChange={(language) => setPrefs({ language })}
        />
        {prefs.persona === "club_fan" && (
          <select aria-label="Favourite club" className={select} value={prefs.favourite_team_id ?? ""} onChange={(e) => setPrefs({ favourite_team_id: e.target.value || null })}>
            <option value="">Pick a club</option>
            {(teams.length ? teams : [feed.home, feed.away].filter(Boolean)).map((t) => (
              <option key={t!.team_id} value={t!.team_id}>
                {t!.name}
              </option>
            ))}
          </select>
        )}
        {prefs.persona === "player_lens" && (
          <select aria-label="Followed player" className={select} value={prefs.followed_player_id ?? ""} onChange={(e) => setPrefs({ followed_player_id: e.target.value || null })}>
            <option value="">Pick a player</option>
            {players.map((p) => (
              <option key={p.player_id} value={p.player_id}>
                {p.shirt_number} {p.name} ({p.player_id.split("-")[0]})
              </option>
            ))}
          </select>
        )}
        <span className="ml-auto">
          <StatusPill status={feed.status} error={feed.error} />
        </span>
      </div>
      <div className="panel px-4 py-2.5">
        <Scoreboard feed={feed} compact />
      </div>
      <div className="panel relative aspect-[111/74] w-full overflow-hidden">
        <Pitch model={feed.pitch} followedPlayerId={prefs.followed_player_id} />
        <OverlayLayer feed={feed} size="md" />
      </div>
      <div className="panel scroll-thin min-h-0 flex-1 overflow-y-auto p-3">
        <InsightFeed feed={feed} lang={prefs.language ?? "en"} limit={8} dense />
      </div>
    </section>
  );
}

/** Split view: the same match for two different viewers, side by side — two independent subscriptions. */
export function SplitView() {
  return (
    <main className="grid min-h-0 flex-1 grid-cols-1 gap-4 p-4 xl:grid-cols-2">
      <Pane storageKey="split-left" initial={{ persona: "analyst", language: "en" }} />
      <Pane storageKey="split-right" initial={{ persona: "casual", language: "es" }} />
    </main>
  );
}
