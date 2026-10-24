import { useEffect, useMemo, useRef, useState } from "react";
import { fetchTeams } from "../lib/api";
import { LANGUAGES, PERSONAS } from "../lib/i18n";
import type { Persona, Player, Team, ViewerPrefs } from "../types";
import { KitStripe } from "./TeamMark";

let teamsCache: Promise<Team[]> | null = null;

/** All fictional clubs, fetched once per page. */
export function useTeams() {
  const [teams, setTeams] = useState<Team[]>([]);
  useEffect(() => {
    teamsCache ??= fetchTeams().catch(() => {
      teamsCache = null;
      return [];
    });
    teamsCache.then(setTeams);
  }, []);
  return teams;
}

interface Props {
  open: boolean;
  onClose: () => void;
  prefs: ViewerPrefs;
  onChange: (patch: Partial<ViewerPrefs>) => void;
  home: Team | null;
  away: Team | null;
  title?: string;
  /** Optional "Audio" section (voice commentary, audio description); browser-local, not sent to the server. */
  audioSettings?: React.ReactNode;
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-ink-700 py-5 first:border-t-0 first:pt-0">
      <h3 className="text-sm font-semibold text-ink-50">{title}</h3>
      {hint && <p className="mt-0.5 text-xs text-ink-400">{hint}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

/** Viewer preferences: persona, language, favourite club and a followed player. Changes apply live. */
export function PrefsDrawer({ open, onClose, prefs, onChange, home, away, title = "Viewer preferences", audioSettings }: Props) {
  const teams = useTeams();
  const [query, setQuery] = useState("");
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    panelRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const roster = useMemo(() => {
    const rows: { p: Player; team: Team }[] = [];
    for (const team of [home, away]) if (team) for (const p of team.players) rows.push({ p, team });
    const q = query.trim().toLowerCase();
    return q
      ? rows.filter(({ p, team }) => `${p.name} ${p.short_name} ${p.shirt_number} ${p.position} ${team.short_name}`.toLowerCase().includes(q))
      : rows;
  }, [home, away, query]);

  const followed = roster.find((r) => r.p.player_id === prefs.followed_player_id) ??
    [home, away].flatMap((t) => (t ? t.players.map((p) => ({ p, team: t })) : [])).find((r) => r.p.player_id === prefs.followed_player_id);

  if (!open) return null;
  // Only the two clubs in this match: a club_fan variant exists for each of them, not for other clubs.
  const clubs = home && away ? [home, away] : teams;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={title}>
      <button className="absolute inset-0 bg-ink-950/70" aria-label="Close preferences" onClick={onClose} />
      <div ref={panelRef} tabIndex={-1} className="drawer-in relative flex h-full w-[min(440px,100vw)] flex-col border-l border-ink-700 bg-ink-900 outline-none">
        <header className="flex items-center justify-between border-b border-ink-700 px-6 py-4">
          <div>
            <h2 className="font-display text-2xl font-semibold">{title}</h2>
            <p className="text-xs text-ink-400">Insights are rewritten for you on the server. Changes apply immediately.</p>
          </div>
          <button onClick={onClose} className="rounded-md px-2.5 py-1.5 text-sm text-ink-300 ring-1 ring-ink-700 hover:bg-ink-800 hover:text-ink-50">
            Done
          </button>
        </header>
        <div className="scroll-thin flex-1 overflow-y-auto px-6 py-5">
          <Section title="Persona" hint="Who the explanation is written for">
            <div className="grid grid-cols-2 gap-2">
              {PERSONAS.map((p) => {
                const active = prefs.persona === p.id;
                return (
                  <button
                    key={p.id}
                    onClick={() => onChange({ persona: p.id as Persona })}
                    aria-pressed={active}
                    className={`rounded-md p-3 text-left ring-1 transition-colors ${active ? "bg-signal/10 ring-signal" : "bg-ink-850 ring-ink-700 hover:ring-ink-500"}`}
                  >
                    <div className={`text-sm font-semibold ${active ? "text-signal" : "text-ink-50"}`}>{p.label}</div>
                    <div className="mt-0.5 text-xs leading-snug text-ink-400">{p.blurb}</div>
                  </button>
                );
              })}
            </div>
            {prefs.persona === "club_fan" && !prefs.favourite_team_id && <p className="mt-2 text-xs text-signal">Pick your club below.</p>}
            {prefs.persona === "player_lens" && !prefs.followed_player_id && <p className="mt-2 text-xs text-signal">Pick a player to follow below.</p>}
          </Section>

          <Section title="Language">
            <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Language">
              {LANGUAGES.map((l) => (
                <button
                  key={l.code}
                  role="radio"
                  aria-checked={prefs.language === l.code}
                  onClick={() => onChange({ language: l.code })}
                  className={`rounded-md px-3 py-1.5 text-sm ring-1 ${prefs.language === l.code ? "bg-signal font-semibold text-ink-950 ring-signal" : "bg-ink-850 text-ink-100 ring-ink-700 hover:ring-ink-500"}`}
                >
                  {l.label}
                </button>
              ))}
            </div>
          </Section>

          {audioSettings && (
            <Section title="Audio" hint="Spoken in the language above. Stays on this device.">
              {audioSettings}
            </Section>
          )}

          <Section title="Favourite club" hint="Used by the Club fan persona">
            <div className="grid grid-cols-2 gap-2">
              {clubs.map((t) => {
                const active = prefs.favourite_team_id === t.team_id;
                const playing = t.team_id === home?.team_id || t.team_id === away?.team_id;
                return (
                  <button
                    key={t.team_id}
                    onClick={() => onChange({ favourite_team_id: active ? null : t.team_id })}
                    aria-pressed={active}
                    className={`flex items-center gap-2.5 rounded-md p-2.5 text-left ring-1 ${active ? "bg-signal/10 ring-signal" : "bg-ink-850 ring-ink-700 hover:ring-ink-500"}`}
                  >
                    <KitStripe team={t} className="h-7 w-1.5" />
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium text-ink-50">{t.name}</span>
                      <span className="text-xs text-ink-400">{playing ? "In this match" : "Not playing"}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </Section>

          <Section title="Follow a player" hint="Their touches get a player tag on the overlay">
            {followed && (
              <div className="mb-2 flex items-center justify-between rounded-md bg-signal/10 px-3 py-2 ring-1 ring-signal">
                <span className="text-sm">
                  <span className="font-display mr-2 font-bold text-signal">{followed.p.shirt_number}</span>
                  {followed.p.name} <span className="text-ink-400">· {followed.team.short_name}</span>
                </span>
                <button className="text-xs text-ink-300 hover:text-ink-50" onClick={() => onChange({ followed_player_id: null })}>
                  Unfollow
                </button>
              </div>
            )}
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search name, number or position"
              aria-label="Search players"
              className="w-full rounded-md bg-ink-850 px-3 py-2 text-sm text-ink-50 ring-1 ring-ink-700 placeholder:text-ink-500 focus:outline-none focus:ring-signal"
            />
            <ul className="scroll-thin mt-2 max-h-72 overflow-y-auto rounded-md ring-1 ring-ink-700">
              {roster.map(({ p, team }, i) => {
                const active = prefs.followed_player_id === p.player_id;
                const sub = team.players.indexOf(p) >= 11;
                return (
                  <li key={p.player_id}>
                    <button
                      onClick={() => onChange({ followed_player_id: active ? null : p.player_id })}
                      aria-pressed={active}
                      className={`flex w-full items-center gap-3 px-3 py-2 text-left text-sm ${i ? "border-t border-ink-800" : ""} ${active ? "bg-signal/10" : "hover:bg-ink-800"}`}
                    >
                      <KitStripe team={team} className="h-5 w-1" />
                      <span className="font-display w-6 text-right text-base font-semibold text-ink-300">{p.shirt_number}</span>
                      <span className={`flex-1 truncate ${active ? "text-signal" : "text-ink-50"}`}>{p.name}</span>
                      <span className="text-xs text-ink-500">
                        {p.position}
                        {sub ? " · sub" : ""}
                      </span>
                    </button>
                  </li>
                );
              })}
              {!roster.length && <li className="px-3 py-4 text-sm text-ink-400">No players match “{query}”.</li>}
            </ul>
          </Section>
        </div>
      </div>
    </div>
  );
}
