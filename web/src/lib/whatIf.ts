// What-if ghost markers for the win-probability chart (MODEL_SPEC §3.1). Pure, so it runs under `npm test`.
// The engine computes the counterfactual and ships it as moment facts; the chart only draws it.
import type { Moment } from "../types/generated";

export interface WhatIfMarker {
  momentId: string;
  clock: number;
  /** Which chart line the marker belongs to. */
  side: "home" | "away";
  /** Shooting team's win % after the miss (where the dashed tick starts). */
  now: number;
  /** Shooting team's win % had it gone in (where the tick ends). */
  ifScored: number;
  /** Hypothetical scoreline, e.g. "2-1", when the engine supplied it. */
  score: string | null;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** One marker per big-chance moment that carries what-if facts and a team in this fixture. */
export function whatIfMarkers(moments: readonly Moment[], homeId: string | null | undefined, awayId: string | null | undefined): WhatIfMarker[] {
  const out: WhatIfMarker[] = [];
  for (const m of moments) {
    if (m.type !== "big_chance" || !m.team_id) continue;
    const side = m.team_id === homeId ? "home" : m.team_id === awayId ? "away" : null;
    if (!side) continue;
    const facts = new Map((m.facts ?? []).map((f) => [f.key, f.value]));
    const now = num(facts.get("whatif_win_pct_now"));
    const ifScored = num(facts.get("whatif_goal_win_pct"));
    if (now == null || ifScored == null) continue;
    const score = facts.get("whatif_goal_score");
    out.push({
      momentId: m.moment_id ?? `${m.clock_ms}`,
      clock: m.clock_ms,
      side,
      now,
      ifScored,
      score: typeof score === "string" ? score : null,
    });
  }
  return out;
}

/** Tooltip text, e.g. "Riverside if it had gone in: 64%, 1-2 (now 31%)". */
export function whatIfLabel(marker: WhatIfMarker, teamName: string | null | undefined): string {
  const who = teamName ? `${teamName} if it had gone in` : "If it had gone in";
  const score = marker.score ? `, ${marker.score}` : "";
  return `${who}: ${marker.ifScored}%${score} (now ${marker.now}%)`;
}
