import type { InsightVariant, MatchState, OverlayCue, Team } from "../types";

/** Producer console: pure helpers (tested in tests/producer.test.ts). No DOM, no config import. */

export const MAX_REQUEST = 200;
/** Auto-approve choices in seconds (0 = off). */
export const AUTO_APPROVE_OPTIONS = [0, 10, 20, 30, 60] as const;
export const PRODUCER_KEY_STORAGE = "fulcrum.producerKey";

/** "4 s", "59 s", "1:05" — compact age of a held item. */
export function formatAge(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s} s`;
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Urgency of a held item for its age badge: fresh, waiting (≥ 15 s), stale (≥ 45 s). */
export function ageTone(seconds: number): "fresh" | "waiting" | "stale" {
  return seconds >= 45 ? "stale" : seconds >= 15 ? "waiting" : "fresh";
}

/** Identity of a variant slot, as the API matches edits: persona + perspective + language. */
export const slotKey = (v: Pick<InsightVariant, "persona" | "perspective_team_id" | "perspective_player_id" | "language">) =>
  [v.persona, v.perspective_team_id ?? "", v.perspective_player_id ?? "", v.language ?? "en"].join("|");

/** "Analyst", "Casual", "Fan · Northbridge Athletic". */
export function variantLabel(v: Pick<InsightVariant, "persona" | "perspective_team_id">, home: Team | null, away: Team | null): string {
  if (v.persona === "club_fan") {
    const team = [home, away].find((t) => t?.team_id === v.perspective_team_id);
    return `Fan · ${team?.name ?? v.perspective_team_id ?? "club"}`;
  }
  if (v.persona === "player_lens") return "Player lens";
  return v.persona.charAt(0).toUpperCase() + v.persona.slice(1);
}

/** Variant order on a held card: analyst, casual, home fan, away fan, then anything else. */
export function orderVariants<T extends Pick<InsightVariant, "persona" | "perspective_team_id">>(variants: readonly T[], homeId?: string | null): T[] {
  const rank = (v: T) =>
    v.persona === "analyst" ? 0 : v.persona === "casual" ? 1 : v.persona === "club_fan" ? (v.perspective_team_id === homeId ? 2 : 3) : 4;
  return [...variants].sort((a, b) => rank(a) - rank(b));
}

/** Only the variants the producer actually changed (trimmed); unchanged ones are not sent. */
export function changedVariants(original: readonly InsightVariant[], draft: Record<string, Partial<InsightVariant>>): InsightVariant[] {
  const out: InsightVariant[] = [];
  for (const v of original) {
    const d = draft[slotKey(v)];
    if (!d) continue;
    const next = {
      ...v,
      headline: (d.headline ?? v.headline).trim(),
      body: (d.body ?? v.body).trim(),
      commentary_line: (d.commentary_line ?? v.commentary_line).trim(),
    };
    if (next.headline !== v.headline || next.body !== v.body || next.commentary_line !== v.commentary_line) out.push(next);
  }
  return out;
}

/** The request as it will be sent (whitespace collapsed), or null if empty or too long. */
export function cleanRequest(raw: string): string | null {
  const q = raw.replace(/\s+/g, " ").trim();
  return q.length >= 2 && q.length <= MAX_REQUEST ? q : null;
}

/** The match's busiest passer (most completed passes) from live state, else the home side's number 9 slot. */
export function standoutPlayer(state: MatchState | null, home: Team | null, away: Team | null): string | null {
  const names = new Map<string, string>();
  for (const t of [home, away]) for (const p of t?.players ?? []) names.set(p.player_id, p.short_name);
  const ranked = Object.values(state?.players ?? {})
    .filter((p) => names.has(p.player_id))
    .sort((a, b) => (b.passes_completed ?? 0) - (a.passes_completed ?? 0) || a.player_id.localeCompare(b.player_id));
  if (ranked.length && (ranked[0].passes_completed ?? 0) > 0) return names.get(ranked[0].player_id) ?? null;
  return home?.players[9]?.short_name ?? home?.players[0]?.short_name ?? null;
}

/** The team behind (or the away side when level): the natural subject of a "comeback" lower third. */
export function underdog(state: MatchState | null, home: Team | null, away: Team | null): Team | null {
  if (!state) return away ?? home;
  if (state.score_home < state.score_away) return home;
  return away ?? home;
}

/** Four suggestion chips built from this match: a player stat card, pressure bar, a team lower third, win probability. */
export function graphicSuggestions(state: MatchState | null, home: Team | null, away: Team | null): string[] {
  const player = standoutPlayer(state, home, away);
  const team = underdog(state, home, away);
  return [
    player ? `Stat card on ${player}'s passing` : "Stat card on the top passer",
    "Pressure bar now",
    team ? `Lower third: ${team.name}'s fightback` : "Lower third: the story so far",
    "Win probability graphic",
  ];
}

/** Template name as shown in the preview header. */
export const TEMPLATE_LABEL: Record<string, string> = {
  lower_third: "Lower third",
  stat_card: "Stat card",
  wp_swing: "Win probability",
  pressure_bar: "Pressure bar",
  player_tag: "Player tag",
  recap_card: "Recap card",
};

/** A previewed cue held on screen in the preview pane: long duration so its timer bar doesn't run out. */
export const previewCue = (cue: OverlayCue): OverlayCue => ({ ...cue, duration_ms: 30_000 });

/** A failed producer call → message for the console ("<status> <detail>" from the API helper). */
export function producerErrorMessage(message: string): string {
  if (message.startsWith("401")) return "Producer key required. Enter the key configured on the API.";
  if (message.startsWith("503")) return "The agents aren't running in the API, so the graphics desk is offline.";
  if (message.startsWith("429")) return "Too many requests. Wait a few seconds.";
  if (message.startsWith("404")) return "That item is gone (already released or killed).";
  return `Couldn't reach the API (${message}).`;
}
