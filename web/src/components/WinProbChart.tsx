import { useMemo } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { clockToMinute, fixtureColours, minuteToClock } from "../lib/format";
import type { MatchFeed } from "../lib/useMatchFeed";

const DRAW = "#a7b0c0";
const TICKS = [0, 15, 30, 45, 60, 75, 90];

interface Row {
  clock: number;
  home: number;
  draw: number;
  away: number;
}

/** Live match outcome probability (home / draw / away). Never presented as odds — this is not a betting product. */
export function WinProbChart({ feed, height = 190, showHeader = true }: { feed: MatchFeed; height?: number; showHeader?: boolean }) {
  const { home, away, winprob, moments, periodStarts, state } = feed;
  const colours = fixtureColours(home, away);

  const data = useMemo<Row[]>(() => {
    // Thin to ≤ 360 points so the SVG stays light during a 90-minute run.
    const step = Math.max(1, Math.ceil(winprob.length / 360));
    const rows = winprob
      .filter((_, i) => i % step === 0 || i === winprob.length - 1)
      .map((p) => ({ clock: p.clock_ms, home: Math.round(p.home * 100), draw: Math.round(p.draw * 100), away: Math.round(p.away * 100) }));
    return rows;
  }, [winprob]);

  const goals = moments.filter((m) => m.type === "goal" || m.type === "red_card");
  const domainMax = Math.max(minuteToClock(90, periodStarts), state?.clock_ms ?? 0);
  const now = state?.win_prob;

  const tickClocks = TICKS.map((m) => minuteToClock(m, periodStarts));
  const label = (clock: number) => `${Math.round(clockToMinute(clock, periodStarts))}'`;

  return (
    <div>
      {showHeader && (
        <div className="mb-2 flex items-end justify-between gap-2">
          <h3 className="text-sm font-medium text-ink-100">Match outcome probability</h3>
          <span className="text-xs text-ink-500">model, not odds</span>
        </div>
      )}
      {/* Legend with current values doubles as the direct labels */}
      <div className="mb-2 grid grid-cols-3 gap-2">
        {[
          { k: "home", name: home?.short_name ?? "Home", c: colours.home, v: now?.home, dash: false },
          { k: "draw", name: "Draw", c: DRAW, v: now?.draw, dash: true },
          { k: "away", name: away?.short_name ?? "Away", c: colours.away, v: now?.away, dash: false },
        ].map((s) => (
          <div key={s.k} className="rounded-md bg-ink-850 px-2.5 py-1.5 ring-1 ring-ink-700">
            <div className="flex items-center gap-1.5 text-xs text-ink-300">
              <svg width="16" height="6" aria-hidden>
                <line x1="0" y1="3" x2="16" y2="3" stroke={s.c} strokeWidth="2" strokeDasharray={s.dash ? "3 3" : undefined} />
              </svg>
              {s.name}
            </div>
            <div className="font-display text-2xl font-semibold leading-tight text-ink-50">
              {s.v != null ? Math.round(s.v * 100) : "–"}
              <span className="ml-0.5 text-sm text-ink-300">%</span>
            </div>
          </div>
        ))}
      </div>
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 6, right: 6, bottom: 0, left: -18 }}>
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
              axisLine={{ stroke: "#2c3649" }}
            />
            <YAxis domain={[0, 100]} ticks={[0, 50, 100]} stroke="#566176" tick={{ fill: "#7b869a", fontSize: 11 }} tickLine={false} axisLine={false} />
            {goals.map((g) => (
              <ReferenceLine key={g.moment_id} x={g.clock_ms} stroke={g.type === "goal" ? "#3de0ff" : "#e5484d"} strokeOpacity={0.55} strokeDasharray="2 3" />
            ))}
            <ReferenceLine x={minuteToClock(45, periodStarts)} stroke="#2c3649" />
            <Tooltip
              cursor={{ stroke: "#7b869a", strokeWidth: 1 }}
              isAnimationActive={false}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const r = payload[0].payload as Row;
                return (
                  <div className="rounded-md bg-ink-950/95 px-3 py-2 text-xs ring-1 ring-ink-600">
                    <div className="mb-1 text-ink-300">{label(r.clock)}</div>
                    <div className="grid grid-cols-[auto_auto] gap-x-3 text-ink-50">
                      <span>{home?.short_name}</span><span className="text-right">{r.home}%</span>
                      <span>Draw</span><span className="text-right">{r.draw}%</span>
                      <span>{away?.short_name}</span><span className="text-right">{r.away}%</span>
                    </div>
                  </div>
                );
              }}
            />
            <Line type="monotone" dataKey="draw" stroke={DRAW} strokeWidth={1.5} strokeDasharray="4 4" dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="home" stroke={colours.home} strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="away" stroke={colours.away} strokeWidth={2} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
