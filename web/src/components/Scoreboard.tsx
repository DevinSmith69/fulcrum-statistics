import { matchClock, periodLabel } from "../lib/format";
import type { MatchFeed } from "../lib/useMatchFeed";
import type { MatchState, Team } from "../types";
import { KitStripe } from "./TeamMark";

function phaseText(state: MatchState | null, home: Team | null, away: Team | null) {
  switch (state?.phase) {
    case "control_home":
      return `${home?.short_name ?? "Home"} in control`;
    case "control_away":
      return `${away?.short_name ?? "Away"} in control`;
    case "chaos":
      return "Chaotic";
    default:
      return "Balanced";
  }
}

function RedCards({ n }: { n: number }) {
  if (!n) return null;
  return (
    <span className="flex gap-0.5" title={`${n} red card${n > 1 ? "s" : ""}`}>
      {Array.from({ length: n }, (_, i) => (
        <span key={i} className="h-3.5 w-2.5 rounded-[2px] bg-[#e5484d]" />
      ))}
    </span>
  );
}

/** Top scoreboard bar: fictional clubs, score, broadcast clock, period and phase. */
export function Scoreboard({ feed, compact = false }: { feed: MatchFeed; compact?: boolean }) {
  const { home, away, state, finished, periodStarts, status } = feed;
  const clock = state ? matchClock(state.clock_ms, state.period, periodStarts) : { main: "00:00", added: null };
  const isHT = !!state && state.period === 1 && feed.recentEvents.at(-1)?.type === "period_end";
  const scoreCls = compact ? "text-4xl" : "text-5xl";
  const nameCls = compact ? "text-xl" : "text-2xl";

  return (
    <div className="flex items-stretch gap-4">
      <div className="flex min-w-0 flex-1 items-center justify-end gap-3">
        <RedCards n={state?.home.red_cards ?? 0} />
        <span className={`font-display ${nameCls} truncate font-semibold tracking-wide text-ink-50`}>
          {compact ? home?.short_name : home?.name}
        </span>
        <KitStripe team={home} className={compact ? "h-9 w-1.5" : "h-11 w-2"} />
      </div>

      <div className="flex items-center gap-1 rounded-md bg-ink-800 px-4 ring-1 ring-ink-700">
        <span className={`font-display ${scoreCls} font-bold leading-none`}>{state?.score_home ?? 0}</span>
        <span className={`font-display ${scoreCls} px-1 leading-none text-ink-500`}>–</span>
        <span className={`font-display ${scoreCls} font-bold leading-none`}>{state?.score_away ?? 0}</span>
      </div>

      <div className="flex min-w-0 flex-1 items-center gap-3">
        <KitStripe team={away} className={compact ? "h-9 w-1.5" : "h-11 w-2"} />
        <span className={`font-display ${nameCls} truncate font-semibold tracking-wide text-ink-50`}>
          {compact ? away?.short_name : away?.name}
        </span>
        <RedCards n={state?.away.red_cards ?? 0} />
      </div>

      <div className="flex shrink-0 items-center gap-4 border-l border-ink-700 pl-4">
        <div className="text-right">
          <div className={`font-display ${compact ? "text-2xl" : "text-3xl"} font-semibold leading-none`}>
            {clock.main}
            {clock.added && <span className="ml-1.5 text-lg text-signal">{clock.added}</span>}
          </div>
          <div className="mt-1 flex items-center justify-end gap-1.5 text-xs text-ink-300">
            {status === "live" && !finished && !isHT && <span className="live-dot h-1.5 w-1.5 rounded-full bg-signal" />}
            {isHT ? "Half-time" : periodLabel(state, finished)}
          </div>
        </div>
        {!compact && (
          <div className="hidden w-36 text-xs text-ink-300 xl:block">
            <div className="text-ink-500">Phase</div>
            <div className="mt-0.5 text-sm font-medium text-ink-100">{phaseText(state, home, away)}</div>
          </div>
        )}
      </div>
    </div>
  );
}
