import { useId, useMemo } from "react";
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { clockToMinute, fixtureColours, minuteToClock } from "../lib/format";
import { momentumLabel, momentumLimit, momentumSeries, zeroOffsets, type MomentumRow } from "../lib/momentum";
import type { MatchFeed } from "../lib/useMatchFeed";

const TICKS = [0, 15, 30, 45, 60, 75, 90];

/**
 * Momentum through the match: one signed line, filled in the home colour above zero (home on top) and the away
 * colour below. Goals and red cards are marked like on the outcome chart. Empty until the API records momentum.
 */
export function MomentumChart({ feed, height = 110, showHeader = false }: { feed: MatchFeed; height?: number; showHeader?: boolean }) {
  const { home, away, winprob, moments, periodStarts, state } = feed;
  const colours = fixtureColours(home, away);
  const gid = useId().replace(/:/g, "");
  const data = useMemo(() => momentumSeries(winprob), [winprob]);
  const limit = momentumLimit(data);
  const zero = zeroOffsets(data);
  const domainMax = Math.max(minuteToClock(90, periodStarts), state?.clock_ms ?? 0);
  const marks = moments.filter((m) => m.type === "goal" || m.type === "red_card");
  const tickClocks = TICKS.map((m) => minuteToClock(m, periodStarts));
  const label = (clock: number) => `${Math.round(clockToMinute(clock, periodStarts))}'`;
  const hs = home?.short_name ?? "Home";
  const as = away?.short_name ?? "Away";

  return (
    <div>
      {showHeader && (
        <div className="mb-2 flex items-end justify-between gap-2">
          <h3 className="text-sm font-medium text-ink-100">Momentum</h3>
          <span className="text-xs text-ink-500">{momentumLabel(state?.momentum ?? 0, hs, as)}</span>
        </div>
      )}
      {data.length < 2 ? (
        <div className="flex items-center justify-center rounded-md bg-ink-850 text-xs text-ink-500 ring-1 ring-ink-700" style={{ height }}>
          Momentum history builds as the match plays
        </div>
      ) : (
        <div className="relative" style={{ height }} role="img" aria-label={`Momentum through the match. Now: ${momentumLabel(state?.momentum ?? 0, hs, as)}`}>
          {/* Direct labels for the two halves of the chart: which side "up" and "down" mean */}
          <span className="pointer-events-none absolute left-2 top-0.5 z-10 text-[11px] text-ink-300">{hs} on top ↑</span>
          <span className="pointer-events-none absolute bottom-6 left-2 z-10 text-[11px] text-ink-300">{as} on top ↓</span>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 4, right: 6, bottom: 0, left: 42 }} /* = the outcome chart's y-axis gutter, so the minutes line up */>
              <defs>
                {/* Gradients span each shape's own box, so the colour switch sits where zero falls in it */}
                <linearGradient id={`${gid}-fill`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset={0} stopColor={colours.home} stopOpacity={0.45} />
                  <stop offset={zero.fill} stopColor={colours.home} stopOpacity={0.1} />
                  <stop offset={zero.fill} stopColor={colours.away} stopOpacity={0.1} />
                  <stop offset={1} stopColor={colours.away} stopOpacity={0.45} />
                </linearGradient>
                <linearGradient id={`${gid}-line`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset={zero.line} stopColor={colours.home} />
                  <stop offset={zero.line} stopColor={colours.away} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#1e2738" vertical={false} />
              <XAxis
                dataKey="clock"
                type="number"
                domain={[0, domainMax]}
                ticks={tickClocks}
                tickFormatter={label}
                stroke="#566176"
                tick={{ fill: "#7b869a", fontSize: 11 }}
                tickLine={false}
                axisLine={false}
              />
              {/* No numeric ticks: momentum is a relative scale; the tooltip and the bar above give the value */}
              <YAxis domain={[-limit, limit]} hide />
              <ReferenceLine y={0} stroke="#566176" />
              <ReferenceLine x={minuteToClock(45, periodStarts)} stroke="#2c3649" />
              {marks.map((g) => (
                <ReferenceLine key={g.moment_id} x={g.clock_ms} stroke={g.type === "goal" ? "#3de0ff" : "#e5484d"} strokeOpacity={0.55} strokeDasharray="2 3" />
              ))}
              <Tooltip
                cursor={{ stroke: "#7b869a", strokeWidth: 1 }}
                isAnimationActive={false}
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const r = payload[0].payload as MomentumRow;
                  return (
                    <div className="rounded-md bg-ink-950/95 px-3 py-2 text-xs ring-1 ring-ink-600">
                      <div className="mb-0.5 text-ink-300">{label(r.clock)}</div>
                      <div className="text-ink-50">{momentumLabel(r.momentum, hs, as)}</div>
                    </div>
                  );
                }}
              />
              <Area type="monotone" dataKey="momentum" baseValue={0} stroke={`url(#${gid}-line)`} strokeWidth={2} fill={`url(#${gid}-fill)`} dot={false} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
