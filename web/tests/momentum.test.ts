// Momentum chart helpers. Run with `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { momentumLabel, momentumLimit, momentumSeries, zeroOffsets } from "../src/lib/momentum.ts";

const pt = (clock_ms: number, momentum?: number | null) => ({ clock_ms, seq: clock_ms, home: 0.4, draw: 0.3, away: 0.3, momentum });

test("series skips points without momentum (older APIs) and keeps the last point when thinning", () => {
  assert.deepEqual(momentumSeries([pt(0, null), pt(1, 12.34), pt(2)]), [{ clock: 1, momentum: 12.3 }]);
  const many = Array.from({ length: 1000 }, (_, i) => pt(i, i % 50));
  const rows = momentumSeries(many, 100);
  assert.ok(rows.length <= 101);
  assert.equal(rows[rows.length - 1].clock, 999);
});

test("y-limit is symmetric, rounded up to 20, between 40 and 100", () => {
  assert.equal(momentumLimit([]), 40);
  assert.equal(momentumLimit([{ clock: 0, momentum: -12 }]), 40);
  assert.equal(momentumLimit([{ clock: 0, momentum: 41 }, { clock: 1, momentum: -55 }]), 60);
  assert.equal(momentumLimit([{ clock: 0, momentum: 99 }]), 100);
});

test("label names the side on top, or Even inside ±3", () => {
  assert.equal(momentumLabel(34.4, "HLC", "MDU"), "HLC on top · 34");
  assert.equal(momentumLabel(-20.6, "HLC", "MDU"), "MDU on top · 21");
  assert.equal(momentumLabel(2, "HLC", "MDU"), "Even");
});

test("gradient switches colour where zero falls in each shape's box", () => {
  const rows = (...v: number[]) => v.map((momentum, clock) => ({ clock, momentum }));
  assert.deepEqual(zeroOffsets(rows(30, -10)), { fill: 0.75, line: 0.75 });
  assert.deepEqual(zeroOffsets(rows(10, 30)), { fill: 1, line: 1 }); // all home: fill box runs down to zero
  assert.deepEqual(zeroOffsets(rows(-5, -20)), { fill: 0, line: 0 });
  assert.deepEqual(zeroOffsets([]), { fill: 0.5, line: 0.5 });
});
