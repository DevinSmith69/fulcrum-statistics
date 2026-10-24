import assert from "node:assert/strict";
import { test } from "node:test";
import { flaggedInsights, latencyRows, orderedEvaluators, percentile, qualityStats } from "../src/lib/quality.ts";
import type { CheckStatus, Insight, Moment } from "../src/types/generated.ts";

const insight = (id: string, clock: number, status: CheckStatus, latency: number | null, notes: string[] = []): Insight =>
  ({
    insight_id: `i-${id}`, match_id: "m", moment_id: id, clock_ms: clock, check_status: status, check_notes: notes,
    latency_ms: latency, variants: [],
    explanation: { headline: id, why_it_matters: "", tactical_tag: "transition", key_fact_keys: [] },
  }) as unknown as Insight;

const moment = (id: string, clock: number, period = 1): Moment =>
  ({ moment_id: id, match_id: "m", type: "goal", clock_ms: clock, period }) as unknown as Moment;

test("percentile uses nearest rank like scripts/eval_live.py", () => {
  assert.equal(percentile([], 50), null);
  assert.equal(percentile([5, 1, 3, 2, 4], 50), 3);
  assert.equal(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95), 10);
  assert.equal(percentile([7], 95), 7);
});

test("qualityStats counts outcomes and latency percentiles", () => {
  const s = qualityStats([
    insight("a", 1000, "passed", 3000),
    insight("b", 2000, "passed", 4000),
    insight("c", 3000, "repaired", 9000),
    insight("d", 4000, "fallback", null),
  ]);
  assert.equal(s.total, 4);
  assert.deepEqual(s.counts, { passed: 2, repaired: 1, fallback: 1 });
  assert.deepEqual(s.ratesPct, { passed: 50, repaired: 25, fallback: 25 });
  assert.deepEqual(s.latency, { p50: 4000, p95: 9000, max: 9000, n: 3 });
  assert.deepEqual(qualityStats([]).ratesPct, { passed: 0, repaired: 0, fallback: 0 });
});

test("latency rows are in match order, in seconds, with the moment's period", () => {
  const rows = latencyRows(
    [insight("late", 3_000_000, "repaired", 6234), insight("early", 60_000, "passed", 2950), insight("none", 5, "passed", null)],
    [moment("late", 3_000_000, 2), moment("early", 60_000)],
  );
  assert.deepEqual(rows.map((r) => [r.momentId, r.seconds, r.period, r.status]), [
    ["early", 3, 1, "passed"],
    ["late", 6.2, 2, "repaired"],
  ]);
});

test("flagged insights are the repaired and fallback ones, newest first, with their notes", () => {
  const f = flaggedInsights(
    [insight("a", 1, "fallback", 1, ["number 10 not in facts"]), insight("b", 2, "passed", 1), insight("c", 3, "repaired", 1)],
    [moment("a", 1)],
  );
  assert.deepEqual(f.map((x) => x.insight.moment_id), ["c", "a"]);
  assert.equal(f[1].moment?.moment_id, "a");
  assert.deepEqual(f[1].insight.check_notes, ["number 10 not in facts"]);
});

test("evaluators are shown in a fixed order, extras last, missing ones skipped", () => {
  const score = { mean: 4, min: 3, n: 2, pass_rate_pct: 100 };
  const out = orderedEvaluators({
    file: "f", generated_at: "", status: "completed", items: 2,
    evaluators: { fluency: score, extra: score, groundedness: score, relevance: null },
  });
  assert.deepEqual(out.map(([n]) => n), ["groundedness", "fluency", "extra"]);
});
