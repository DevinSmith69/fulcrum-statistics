import type { WinProbPoint } from "../types";

/*
 * Momentum chart helpers (pure, tested in tests/momentum.test.ts). Momentum is the engine's MatchState.momentum
 * (−100 … +100, + = home on top), recorded on every WinProbPoint since contract 2026-10-25.
 */

export interface MomentumRow {
  clock: number;
  momentum: number;
}

/** Points that carry momentum, thinned to at most `maxPoints` (the last point is always kept). */
export function momentumSeries(points: WinProbPoint[], maxPoints = 360): MomentumRow[] {
  const known = points.filter((p) => p.momentum != null);
  const step = Math.max(1, Math.ceil(known.length / maxPoints));
  return known
    .filter((_, i) => i % step === 0 || i === known.length - 1)
    .map((p) => ({ clock: p.clock_ms, momentum: Math.round((p.momentum as number) * 10) / 10 }));
}

/** Symmetric y-limit: the largest swing rounded up to 20, at least 40 so a quiet match doesn't look wild. */
export function momentumLimit(rows: MomentumRow[]): number {
  const peak = rows.reduce((m, r) => Math.max(m, Math.abs(r.momentum)), 0);
  return Math.min(100, Math.max(40, Math.ceil(peak / 20) * 20));
}

/**
 * Where zero falls (0 = top, 1 = bottom) in the bounding box of the filled area (which always includes the zero
 * baseline) and of the line (data min to max). SVG gradients span the shape's box, not the axis.
 */
export function zeroOffsets(rows: MomentumRow[]): { fill: number; line: number } {
  if (!rows.length) return { fill: 0.5, line: 0.5 };
  const hi = Math.max(...rows.map((r) => r.momentum));
  const lo = Math.min(...rows.map((r) => r.momentum));
  const fillHi = Math.max(hi, 0);
  const fillLo = Math.min(lo, 0);
  const fill = fillHi === fillLo ? 0.5 : fillHi / (fillHi - fillLo);
  const line = hi <= 0 ? 0 : lo >= 0 ? 1 : hi / (hi - lo);
  return { fill, line };
}

/** "HLC on top · 34", or "Even" inside ±3 (same wording as the momentum bar). */
export function momentumLabel(value: number, home?: string, away?: string): string {
  if (value > 3) return `${home ?? "Home"} on top · ${Math.round(value)}`;
  if (value < -3) return `${away ?? "Away"} on top · ${Math.round(-value)}`;
  return "Even";
}
