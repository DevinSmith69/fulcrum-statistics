// Quality dashboard maths: fact-check outcome rates and moment → insight latency from the live feed, plus the
// shapes of the offline evaluation reports served by GET /api/eval/latest (docs/API_CONTRACT.md §2).
import type { CheckStatus, Insight, Moment, MomentType } from "../types";

export const STATUSES: CheckStatus[] = ["passed", "repaired", "fallback"];

/** Nearest-rank percentile (q in 0..100), the same rule as scripts/eval_live.py; null for no values. */
export function percentile(values: number[], q: number): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.round((q / 100) * sorted.length) - 1))];
}

export interface QualityStats {
  total: number;
  counts: Record<CheckStatus, number>;
  ratesPct: Record<CheckStatus, number>;
  latency: { p50: number | null; p95: number | null; max: number | null; n: number };
}

export function qualityStats(insights: Insight[]): QualityStats {
  const counts: Record<CheckStatus, number> = { passed: 0, repaired: 0, fallback: 0 };
  for (const i of insights) counts[i.check_status] = (counts[i.check_status] ?? 0) + 1;
  const total = insights.length;
  const ratesPct = Object.fromEntries(
    STATUSES.map((s) => [s, total ? Math.round((1000 * counts[s]) / total) / 10 : 0]),
  ) as Record<CheckStatus, number>;
  const lat = insights.map((i) => i.latency_ms).filter((v): v is number => typeof v === "number");
  return {
    total,
    counts,
    ratesPct,
    latency: { p50: percentile(lat, 50), p95: percentile(lat, 95), max: lat.length ? Math.max(...lat) : null, n: lat.length },
  };
}

export interface LatencyRow {
  momentId: string;
  clock: number;
  period: number;
  seconds: number;
  status: CheckStatus;
  type: MomentType | string;
}

/** One point per explained moment with a latency, in match order (x = the moment's match clock). */
export function latencyRows(insights: Insight[], moments: Moment[]): LatencyRow[] {
  const byId = new Map(moments.map((m) => [m.moment_id, m]));
  return insights
    .filter((i) => typeof i.latency_ms === "number")
    .map((i) => {
      const m = byId.get(i.moment_id);
      return {
        momentId: i.moment_id,
        clock: i.clock_ms,
        period: m?.period ?? 1,
        seconds: Math.round((i.latency_ms as number) / 100) / 10,
        status: i.check_status,
        type: m?.type ?? "moment",
      };
    })
    .sort((a, b) => a.clock - b.clock);
}

export interface Flagged {
  insight: Insight;
  moment: Moment | undefined;
}

/** Repaired and fallback insights (newest first): the moments where the guardrails stepped in. */
export function flaggedInsights(insights: Insight[], moments: Moment[]): Flagged[] {
  const byId = new Map(moments.map((m) => [m.moment_id, m]));
  return insights
    .filter((i) => i.check_status !== "passed")
    .sort((a, b) => b.clock_ms - a.clock_ms)
    .map((insight) => ({ insight, moment: byId.get(insight.moment_id) }));
}

// ---------------------------------------------------------------- offline evaluation reports

export interface EvaluatorScore {
  mean: number;
  min: number;
  n: number;
  pass_rate_pct: number;
}

export interface FoundryEvalSummary {
  file: string;
  generated_at: string;
  eval_name?: string;
  status: string;
  error?: string | null;
  report_url?: string | null;
  judge_deployment?: string;
  items: number;
  judge_tokens?: number;
  result_counts?: { passed: number; failed: number; errored: number; total: number } | null;
  evaluators: Record<string, EvaluatorScore | null>;
  by_check_status?: Record<string, Record<string, number | null>>;
  lowest?: {
    moment_id: string;
    type: string;
    minute: string;
    check_status: string;
    scores: Record<string, number>;
    reasons: Record<string, string>;
  }[];
}

export interface LiveEvalReport {
  file: string;
  generated_at: string;
  recording?: string;
  match_id?: string;
  moments: number;
  status_counts: Partial<Record<CheckStatus, number>>;
  status_rates_pct: Partial<Record<CheckStatus, number>>;
  latency_ms: { p50: number; p95: number; mean: number };
  tokens_total?: number;
  recap?: { seconds: number; status: string | null; title: string | null };
}

export interface EvalLatest {
  live: LiveEvalReport | null;
  foundry: FoundryEvalSummary | null;
}

/** Evaluators in display order, then any others the summary has. */
export const EVALUATOR_ORDER = ["groundedness", "relevance", "coherence", "fluency"];

export const EVALUATOR_HELP: Record<string, string> = {
  groundedness: "Is every claim supported by the moment's facts and context?",
  relevance: "Does the copy answer what was asked about this moment?",
  coherence: "Does it read as a logical, well-ordered explanation?",
  fluency: "Grammar, word choice and readability.",
};

export function orderedEvaluators(summary: FoundryEvalSummary): [string, EvaluatorScore][] {
  const names = [...EVALUATOR_ORDER.filter((e) => e in summary.evaluators), ...Object.keys(summary.evaluators).filter((e) => !EVALUATOR_ORDER.includes(e))];
  return names.flatMap((n) => (summary.evaluators[n] ? [[n, summary.evaluators[n] as EvaluatorScore] as [string, EvaluatorScore]] : []));
}
