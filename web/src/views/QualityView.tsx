import { useEffect, useMemo, useState } from "react";
import { CartesianGrid, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import { CheckBadge } from "../components/InsightFeed";
import { fetchEvalLatest } from "../lib/api";
import { clockToMinute, minuteLabel, minuteToClock } from "../lib/format";
import { momentLabel } from "../lib/i18n";
import {
  EVALUATOR_HELP,
  flaggedInsights,
  latencyRows,
  orderedEvaluators,
  qualityStats,
  STATUSES,
  type EvalLatest,
  type FoundryEvalSummary,
  type LatencyRow,
  type LiveEvalReport,
} from "../lib/quality";
import type { MatchFeed } from "../lib/useMatchFeed";
import type { CheckStatus, MomentType } from "../types";

/** Status colours (validated for CVD separation on the dark panel) with a shape each, so colour is never alone. */
const STATUS_STYLE: Record<CheckStatus, { colour: string; label: string; shape: "circle" | "diamond" | "square" }> = {
  passed: { colour: "#3de0ff", label: "Passed", shape: "circle" },
  repaired: { colour: "#f5a524", label: "Repaired", shape: "diamond" },
  fallback: { colour: "#ff5c7a", label: "Fallback", shape: "square" },
};
const TICKS = [0, 15, 30, 45, 60, 75, 90];

const secs = (ms: number | null | undefined) => (ms == null ? "–" : (ms / 1000).toFixed(1));

function Marker({ cx, cy, status }: { cx?: number; cy?: number; status: CheckStatus }) {
  if (cx == null || cy == null) return null;
  const { colour, shape } = STATUS_STYLE[status];
  const ring = { stroke: "#0f1522", strokeWidth: 2 };
  if (shape === "diamond") return <path d={`M${cx} ${cy - 6}L${cx + 6} ${cy}L${cx} ${cy + 6}L${cx - 6} ${cy}Z`} fill={colour} {...ring} />;
  if (shape === "square") return <rect x={cx - 5} y={cy - 5} width={10} height={10} rx={1.5} fill={colour} {...ring} />;
  return <circle cx={cx} cy={cy} r={5} fill={colour} {...ring} />;
}

function LegendKey({ status, count }: { status: CheckStatus; count: number }) {
  return (
    <span className="flex items-center gap-1.5 text-xs text-ink-300">
      <svg width="14" height="14" aria-hidden>
        <Marker cx={7} cy={7} status={status} />
      </svg>
      {STATUS_STYLE[status].label}
      <span className="text-ink-500">{count}</span>
    </span>
  );
}

function Tile({ label, value, unit, sub, status }: { label: string; value: string; unit?: string; sub?: string; status?: CheckStatus }) {
  return (
    <div className="panel px-4 py-3">
      <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-ink-400">
        {status && (
          <svg width="12" height="12" aria-hidden>
            <Marker cx={6} cy={6} status={status} />
          </svg>
        )}
        {label}
      </div>
      <div className="font-display text-4xl font-semibold leading-tight text-ink-50">
        {value}
        {unit && <span className="ml-1 text-lg text-ink-300">{unit}</span>}
      </div>
      {sub && <div className="text-xs text-ink-400">{sub}</div>}
    </div>
  );
}

function LatencyChart({ feed, rows, p95 }: { feed: MatchFeed; rows: LatencyRow[]; p95: number | null }) {
  const { periodStarts, state } = feed;
  const domainMax = Math.max(minuteToClock(90, periodStarts), state?.clock_ms ?? 0, ...rows.map((r) => r.clock));
  const yMax = Math.max(8, Math.ceil(Math.max(0, ...rows.map((r) => r.seconds)) + 1));
  const label = (clock: number) => `${Math.round(clockToMinute(clock, periodStarts))}'`;
  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="#1e2738" vertical={false} />
          <XAxis
            dataKey="clock"
            type="number"
            domain={[0, domainMax]}
            ticks={TICKS.map((m) => minuteToClock(m, periodStarts))}
            tickFormatter={label}
            tick={{ fill: "#7b869a", fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: "#2c3649" }}
          />
          <YAxis
            dataKey="seconds"
            type="number"
            domain={[0, yMax]}
            tickFormatter={(v: number) => `${v}s`}
            tick={{ fill: "#7b869a", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
          />
          {p95 != null && (
            <ReferenceLine
              y={p95 / 1000}
              stroke="#7b869a"
              strokeDasharray="4 4"
              label={{ value: `p95 ${secs(p95)} s`, position: "insideTopRight", fill: "#a7b0c0", fontSize: 11 }}
            />
          )}
          <Tooltip
            cursor={false}
            isAnimationActive={false}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const r = payload[0].payload as LatencyRow;
              return (
                <div className="rounded-md bg-ink-950/95 px-3 py-2 text-xs ring-1 ring-ink-600">
                  <div className="mb-1 text-ink-300">
                    {minuteLabel(r.clock, r.period, periodStarts)} · {momentLabel("en", r.type as MomentType)}
                  </div>
                  <div className="flex items-center gap-2 text-ink-50">
                    <span className="font-semibold">{r.seconds.toFixed(1)} s</span>
                    <CheckBadge status={r.status} />
                  </div>
                </div>
              );
            }}
          />
          {STATUSES.map((s) => (
            <Scatter
              key={s}
              name={STATUS_STYLE[s].label}
              data={rows.filter((r) => r.status === s)}
              isAnimationActive={false}
              shape={(p: { cx?: number; cy?: number }) => <Marker cx={p.cx} cy={p.cy} status={s} />}
            />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

function ScoreBar({ value }: { value: number }) {
  // 1–5 scale: the bar starts at 1 so a 1 reads as empty; the tick marks the pass threshold (3).
  return (
    <div className="relative mt-2 h-1.5 rounded-full bg-ink-700">
      <div className="h-full rounded-full bg-signal" style={{ width: `${Math.max(0, Math.min(1, (value - 1) / 4)) * 100}%` }} />
      <div className="absolute -top-0.5 h-2.5 w-px bg-ink-300" style={{ left: "50%" }} title="Pass threshold (3)" />
    </div>
  );
}

function FoundryPanel({ summary }: { summary: FoundryEvalSummary }) {
  const scores = orderedEvaluators(summary);
  const counts = summary.result_counts;
  return (
    <section className="panel p-5" aria-labelledby="foundry-eval">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="foundry-eval" className="font-display text-2xl font-semibold">
          Foundry evaluation
        </h2>
        {summary.report_url && (
          <a href={summary.report_url} target="_blank" rel="noreferrer" className="text-sm font-medium text-signal hover:underline">
            Open in Foundry ↗
          </a>
        )}
      </div>
      <p className="mt-1 text-sm text-ink-400">
        Built-in evaluators on {summary.items} moments of the offline run, judged by the <code>{summary.judge_deployment || "fast"}</code>{" "}
        deployment. Scores are 1–5; 3 passes.
        {counts ? ` ${counts.passed} of ${counts.total} moments passed every evaluator.` : ""}
      </p>
      {summary.status !== "completed" ? (
        <p className="mt-4 text-sm text-ink-300">
          Run status: {summary.status}
          {summary.error ? ` (${summary.error})` : ""}
        </p>
      ) : (
        <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {scores.map(([name, s]) => (
            <div key={name} className="rounded-md bg-ink-850 px-3 py-2.5 ring-1 ring-ink-700" title={EVALUATOR_HELP[name]}>
              <div className="text-xs font-medium capitalize text-ink-300">{name}</div>
              <div className="font-display text-3xl font-semibold leading-tight text-ink-50">
                {s.mean.toFixed(2)}
                <span className="ml-1 text-sm text-ink-400">/ 5</span>
              </div>
              <ScoreBar value={s.mean} />
              <div className="mt-1.5 text-xs text-ink-400">
                {s.pass_rate_pct}% pass · min {s.min}
              </div>
            </div>
          ))}
        </div>
      )}
      {!!summary.lowest?.length && (
        <div className="mt-5">
          <h3 className="text-sm font-medium text-ink-100">Lowest-scoring moments</h3>
          <ul className="mt-2 space-y-2">
            {summary.lowest.slice(0, 3).map((l) => {
              const [worst, score] = Object.entries(l.scores).sort((a, b) => a[1] - b[1])[0] ?? ["", 0];
              return (
                <li key={l.moment_id} className="rounded-md bg-ink-850 p-3 text-sm ring-1 ring-ink-700">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-display text-sm font-semibold text-ink-50">{l.minute}</span>
                    <span className="text-ink-300">{momentLabel("en", l.type as MomentType)}</span>
                    <span className="ml-auto text-ink-300">
                      <span className="capitalize">{worst}</span> <span className="font-semibold text-ink-50">{score}</span>
                    </span>
                  </div>
                  {l.reasons[worst] && <p className="mt-1.5 text-ink-300">{l.reasons[worst].replace(/^Let's think step by step:\s*/i, "")}</p>}
                </li>
              );
            })}
          </ul>
        </div>
      )}
      <p className="mt-4 text-xs text-ink-500">
        {summary.file} · {new Date(summary.generated_at).toLocaleString()}
        {summary.judge_tokens ? ` · ${Math.round(summary.judge_tokens / 1000)}k judge tokens` : ""}
      </p>
    </section>
  );
}

function LivePanel({ report }: { report: LiveEvalReport }) {
  const rate = (s: CheckStatus) => report.status_rates_pct[s] ?? 0;
  return (
    <section className="panel p-5" aria-labelledby="offline-run">
      <h2 id="offline-run" className="font-display text-2xl font-semibold">
        Offline agent run
      </h2>
      <p className="mt-1 text-sm text-ink-400">
        A recorded match ({report.match_id ?? report.recording}) replayed through the agents on Foundry, one moment at a time.
      </p>
      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
        <div>
          <dt className="text-ink-400">Moments</dt>
          <dd className="font-display text-2xl font-semibold">{report.moments}</dd>
        </div>
        <div>
          <dt className="text-ink-400">Passed / repaired / fallback</dt>
          <dd className="font-display text-2xl font-semibold">
            {rate("passed")} / {rate("repaired")} / {rate("fallback")}
            <span className="ml-1 text-sm text-ink-400">%</span>
          </dd>
        </div>
        <div>
          <dt className="text-ink-400">Latency p50 · p95</dt>
          <dd className="font-display text-2xl font-semibold">
            {secs(report.latency_ms.p50)} · {secs(report.latency_ms.p95)}
            <span className="ml-1 text-sm text-ink-400">s</span>
          </dd>
        </div>
        <div>
          <dt className="text-ink-400">Tokens</dt>
          <dd className="font-display text-2xl font-semibold">{report.tokens_total ? `${Math.round(report.tokens_total / 1000)}k` : "–"}</dd>
        </div>
      </dl>
      {report.recap?.status && (
        <p className="mt-3 text-sm text-ink-300">
          Recap: {report.recap.status}, {Math.round(report.recap.seconds)} s
        </p>
      )}
      <p className="mt-4 text-xs text-ink-500">
        {report.file} · {new Date(report.generated_at).toLocaleString()}
      </p>
    </section>
  );
}

function useEvalLatest() {
  const [data, setData] = useState<EvalLatest | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    fetchEvalLatest()
      .then((d) => live && setData(d))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, []);
  return { data, error };
}

/** Guardrails and observability for the current match: fact-check outcomes, latency, and the offline evals. */
export function QualityView({ feed }: { feed: MatchFeed }) {
  const insights = useMemo(() => Object.values(feed.insights), [feed.insights]);
  const stats = useMemo(() => qualityStats(insights), [insights]);
  const rows = useMemo(() => latencyRows(insights, feed.moments), [insights, feed.moments]);
  const flagged = useMemo(() => flaggedInsights(insights, feed.moments), [insights, feed.moments]);
  const evals = useEvalLatest();
  const fixture = feed.home && feed.away ? `${feed.home.name} v ${feed.away.name}` : "Current match";

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-7xl space-y-6">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h1 className="font-display text-4xl font-semibold">Quality</h1>
            <p className="text-sm text-ink-400">
              {fixture}: every insight is fact-checked against its moment's numbers before it reaches the screen.
            </p>
          </div>
          <span className="text-sm text-ink-400">{stats.total} insights so far</span>
        </div>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          {STATUSES.map((s) => (
            <Tile
              key={s}
              status={s}
              label={STATUS_STYLE[s].label}
              value={stats.total ? `${stats.ratesPct[s]}` : "–"}
              unit={stats.total ? "%" : undefined}
              sub={`${stats.counts[s]} of ${stats.total}`}
            />
          ))}
          <Tile label="Latency p50" value={secs(stats.latency.p50)} unit="s" sub="moment → insight" />
          <Tile label="Latency p95" value={secs(stats.latency.p95)} unit="s" sub={`max ${secs(stats.latency.max)} s`} />
        </div>

        <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <section className="panel p-5" aria-labelledby="latency-chart">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h2 id="latency-chart" className="text-sm font-medium text-ink-100">
                Moment → insight latency over the match
              </h2>
              <div className="flex gap-4">
                {STATUSES.map((s) => (
                  <LegendKey key={s} status={s} count={stats.counts[s]} />
                ))}
              </div>
            </div>
            {rows.length ? (
              <LatencyChart feed={feed} rows={rows} p95={stats.latency.p95} />
            ) : (
              <p className="flex h-64 items-center justify-center text-sm text-ink-400">No insights yet. They appear as moments happen.</p>
            )}
          </section>

          <section className="panel flex max-h-[25rem] flex-col p-5" aria-labelledby="guardrails">
            <h2 id="guardrails" className="text-sm font-medium text-ink-100">
              Where the guardrails stepped in
            </h2>
            <p className="mt-1 text-xs text-ink-400">Repaired: first draft failed the fact-check, the rewrite passed. Fallback: verified template copy.</p>
            {flagged.length ? (
              <ul className="mt-3 space-y-2 overflow-y-auto pr-1">
                {flagged.map(({ insight, moment }) => (
                  <li key={insight.insight_id} className="rounded-md bg-ink-850 p-3 ring-1 ring-ink-700">
                    <div className="flex items-center gap-2 text-xs">
                      <span className="font-display text-sm font-semibold text-ink-50">
                        {moment ? minuteLabel(moment.clock_ms, moment.period, feed.periodStarts) : ""}
                      </span>
                      <span className="text-ink-300">{moment ? momentLabel("en", moment.type) : "Moment"}</span>
                      <span className="ml-auto flex items-center gap-2">
                        <span className="text-ink-400">{secs(insight.latency_ms)} s</span>
                        <CheckBadge status={insight.check_status} />
                      </span>
                    </div>
                    <p className="mt-1.5 text-sm text-ink-100">{insight.explanation.headline}</p>
                    {insight.check_notes?.length ? (
                      <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-xs text-ink-300">
                        {insight.check_notes.map((n, i) => (
                          <li key={i} className="break-words">
                            {n}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="mt-1.5 text-xs text-ink-500">No notes recorded.</p>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-6 text-sm text-ink-400">
                {stats.total ? "Every insight so far passed the fact-check on the first draft." : "Nothing yet."}
              </p>
            )}
          </section>
        </div>

        <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          {evals.data?.foundry ? (
            <FoundryPanel summary={evals.data.foundry} />
          ) : (
            <section className="panel p-5 text-sm text-ink-400">
              <h2 className="font-display text-2xl font-semibold text-ink-50">Foundry evaluation</h2>
              <p className="mt-2">
                {evals.error
                  ? `Couldn't load evaluation reports (${evals.error}).`
                  : evals.data
                    ? "No Foundry evaluation yet. Run scripts/eval_live.py, then scripts/eval_foundry.py."
                    : "Loading…"}
              </p>
            </section>
          )}
          {evals.data?.live && <LivePanel report={evals.data.live} />}
        </div>
      </div>
    </main>
  );
}
