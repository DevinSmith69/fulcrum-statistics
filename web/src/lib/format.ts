import type { MatchState, Side, Team } from "../types";

const MIN = 60_000;

/** Period start clocks keyed by period ("1", "2"); period 2 starts at 45' + first-half stoppage. */
export type PeriodStarts = Record<string, number>;

const p2Start = (starts: PeriodStarts) => starts["2"] ?? 45 * MIN;

/** Broadcast clock: mm:ss up to 45:00 / 90:00, then frozen with a "+m:ss" stoppage tail. */
export function matchClock(clockMs: number, period: number, starts: PeriodStarts) {
  const elapsed = period >= 2 ? clockMs - p2Start(starts) : clockMs;
  const base = period >= 2 ? 45 * MIN : 0;
  const cap = 45 * MIN;
  const extra = Math.max(0, elapsed - cap);
  return { main: mmss(Math.min(Math.max(0, elapsed), cap) + base), added: extra > 0 ? `+${mmss(extra)}` : null };
}

/** Football minute label, e.g. 67' or 45+2'. */
export function minuteLabel(clockMs: number, period: number, starts: PeriodStarts): string {
  const elapsed = period >= 2 ? clockMs - p2Start(starts) : clockMs;
  const m = Math.max(1, Math.floor((elapsed - 1) / MIN) + 1);
  const base = period >= 2 ? 45 : 0;
  return m > 45 ? `${base + 45}+${m - 45}'` : `${base + m}'`;
}

/** Football minute (as a number) at which a continuous clock value sits, for chart axis labels. */
export function clockToMinute(clockMs: number, starts: PeriodStarts): number {
  const p2 = p2Start(starts);
  return clockMs >= p2 ? 45 + (clockMs - p2) / MIN : clockMs / MIN;
}

/** Inverse of clockToMinute for regulation minutes (H1 ≤ 45, H2 > 45). */
export function minuteToClock(minute: number, starts: PeriodStarts): number {
  return minute <= 45 ? minute * MIN : p2Start(starts) + (minute - 45) * MIN;
}

export function mmss(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export const pct = (v: number | undefined | null) => Math.round((v ?? 0) * 100);

/** Normalise a 0–1 or 0–100 percentage to 0–100 (the contract does not pin the unit). */
export const as100 = (v: number | undefined | null) => ((v ?? 0) <= 1 ? (v ?? 0) * 100 : (v ?? 0));

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(full, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function luminance([r, g, b]: [number, number, number]) {
  const f = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

/** Lighten a colour until it reads on the dark studio background (lines, text, chart series). */
export function readable(hex: string, minLum = 0.18): string {
  let rgb = hexToRgb(hex);
  for (let i = 0; i < 14 && luminance(rgb) < minLum; i++) {
    rgb = rgb.map((c) => Math.round(c + (255 - c) * 0.16)) as [number, number, number];
  }
  return `#${rgb.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

// --- OKLCH helpers: team colours become chart/marker series at a fixed perceptual lightness -------------
const toLin = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const toSrgb = (c: number) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);

function hexToOklch(hex: string): [number, number, number] {
  const [r, g, b] = hexToRgb(hex).map((c) => toLin(c / 255));
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return [L, Math.hypot(A, B), Math.atan2(B, A)];
}

function oklchToRgb(L: number, C: number, h: number): [number, number, number] | null {
  const A = C * Math.cos(h);
  const B = C * Math.sin(h);
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3;
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3;
  const s = (L - 0.0894841775 * A - 1.291485548 * B) ** 3;
  const rgb = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ].map(toSrgb);
  if (rgb.some((c) => c < -0.001 || c > 1.001)) return null;
  return rgb.map((c) => Math.round(Math.min(1, Math.max(0, c)) * 255)) as [number, number, number];
}

/**
 * A team colour as a data series on the dark surface: same hue, OKLab lightness 0.62, chroma ≥ 0.12
 * (gamut-clamped). Validated with the dataviz palette checker for the default fixtures.
 */
export function seriesColour(hex: string): string {
  const [, c0, h] = hexToOklch(hex);
  let C = Math.max(0.12, Math.min(c0, 0.2));
  let rgb = oklchToRgb(0.62, C, h);
  while (!rgb && C > 0.02) rgb = oklchToRgb(0.62, (C -= 0.01), h);
  rgb ??= [150, 150, 150];
  return `#${rgb.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

const hueOf = (hex: string) => hexToOklch(hex)[2];

/**
 * Series colours for a fixture. If the two primaries are too close in hue, the away side switches to its
 * secondary colour so identity never relies on a near-identical pair.
 */
export function fixtureColours(home: Team | null, away: Team | null): { home: string; away: string } {
  const h = seriesColour(home?.primary_colour ?? "#3b82f6");
  if (!away) return { home: h, away: seriesColour("#ef4444") };
  const hh = hueOf(home?.primary_colour ?? "#3b82f6");
  const dist = (hex: string) => {
    const d = Math.abs(hh - hueOf(hex));
    return Math.min(d, 2 * Math.PI - d);
  };
  const chromatic = (hex: string) => hexToOklch(hex)[1] >= 0.08;
  // primary → secondary → primary rotated away from the home hue (last resort, e.g. purple v navy)
  for (const cand of [away.primary_colour, away.secondary_colour]) {
    if (chromatic(cand) && dist(cand) >= 0.9) return { home: h, away: seriesColour(cand) };
  }
  const [, c, ah] = hexToOklch(away.primary_colour);
  const rotated = oklchToRgb(0.62, Math.max(0.12, Math.min(c, 0.18)), ah + Math.sign(ah - hh || 1) * 1.1) ?? [226, 73, 64];
  return { home: h, away: `#${rotated.map((v) => v.toString(16).padStart(2, "0")).join("")}` };
}

/** The team's colour, lifted for legibility on dark surfaces (text accents, small marks). */
export const teamInk = (team: Team | null | undefined) => (team ? seriesColour(team.primary_colour) : "#A5AFBF");

export function sideOf(teamId: string | null | undefined, home: Team | null, away: Team | null): Side | null {
  if (!teamId) return null;
  if (home && teamId === home.team_id) return "home";
  if (away && teamId === away.team_id) return "away";
  return null;
}

export function periodLabel(state: MatchState | null, finished: boolean): string {
  if (finished) return "Full time";
  if (!state) return "Pre-match";
  return state.period === 1 ? "1st half" : state.period === 2 ? "2nd half" : `Period ${state.period}`;
}
