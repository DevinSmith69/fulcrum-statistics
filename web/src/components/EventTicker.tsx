import { minuteLabel } from "../lib/format";
import type { MatchFeed } from "../lib/useMatchFeed";
import type { MatchEvent } from "../types";

const VERB: Partial<Record<MatchEvent["type"], string>> = {
  pass: "pass", carry: "carries", dribble: "takes on", shot: "shoots", goal: "GOAL", save: "saves", tackle: "tackles",
  interception: "intercepts", ball_recovery: "recovers", clearance: "clears", foul: "fouls", card: "booked", corner: "corner",
  free_kick: "free kick", throw_in: "throw-in", goal_kick: "goal kick", offside: "offside", substitution: "on",
  kick_off: "kick-off",
};

/** One-line "what just happened" strip under the pitch, fed by the raw event stream. */
export function EventTicker({ feed }: { feed: MatchFeed }) {
  const players = new Map([...(feed.home?.players ?? []), ...(feed.away?.players ?? [])].map((p) => [p.player_id, p]));
  const evs = feed.recentEvents.filter((e) => VERB[e.type] && e.type !== "pressure").slice(-4).reverse();
  return (
    <div className="flex h-9 items-center gap-5 overflow-hidden px-4 text-sm">
      {evs.map((e, i) => {
        const p = e.player_id ? players.get(e.player_id) : null;
        const rec = e.pass?.recipient_id ? players.get(e.pass.recipient_id) : null;
        let what = VERB[e.type]!;
        if (e.type === "pass") what = rec ? `→ ${rec.short_name}` : "pass intercepted";
        if (e.type === "shot") what = `shoots · ${e.shot?.xg.toFixed(2)} xG · ${(e.outcome ?? "").replace("_", " ")}`;
        if (e.type === "card") what = e.card?.colour === "yellow" ? "yellow card" : "sent off";
        return (
          <span key={e.seq} className={`flex shrink-0 items-center gap-2 ${i ? "text-ink-500" : "text-ink-100"}`}>
            <span className="font-display text-ink-400">{minuteLabel(e.clock_ms, e.period, feed.periodStarts)}</span>
            {p && <span className="font-medium">{p.short_name}</span>}
            <span>{what}</span>
          </span>
        );
      })}
    </div>
  );
}
