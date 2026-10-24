import type { Insight, Moment, MomentType, Recap } from "../types";

/*
 * "Your highlights": a one-click reel of the match's key moments at full time, in the viewer's persona and language.
 * Pure logic (no DOM, no React): which moments make the reel, what each one shows and says, the running score,
 * timing budgets and the step state machine. Tested in tests/highlights.test.ts; the UI is HighlightsReel.tsx.
 */

/** Moments that are always in the reel, whatever the recap picked. */
export const ALWAYS_IN: ReadonlySet<MomentType> = new Set<MomentType>(["goal", "red_card"]);
/** Period boundaries have no run of play to replay. */
const NEVER_IN: ReadonlySet<MomentType> = new Set<MomentType>(["half_time", "full_time"]);
/** Moments in the reel (~60 s); goals and red cards may push it past this, nothing else does. */
export const FALLBACK_MAX = 6;

export interface Score {
  home: number;
  away: number;
}

export interface ReelItem {
  moment: Moment;
  /** Lower-third headline: the viewer's insight headline, or a template label when the insight is missing. */
  headline: string;
  /** One short line from the insight body (null without an insight). */
  line: string | null;
  /** What the voice says: the insight's commentary_line (null without one, so the step does not wait on speech). */
  speech: string | null;
  /** Language of the insight copy (it can briefly lag a language switch); null = the viewer's language. */
  lang: string | null;
  /** True when the copy came from a (fact-checked) insight rather than the template label. */
  fromInsight: boolean;
  scoreBefore: Score;
  scoreAfter: Score;
}

export interface ReelSource {
  moments: readonly Moment[];
  recap: Recap | null;
  insights: Readonly<Record<string, Insight>>;
  homeTeamId: string | null;
  /** Template label for a moment without an insight, e.g. "Goal · NBA". */
  label: (m: Moment) => string;
}

const matchOrder = (a: Moment, b: Moment) => a.seq_end - b.seq_end || a.clock_ms - b.clock_ms || a.seq_start - b.seq_start;
const rank = (m: Moment) => (ALWAYS_IN.has(m.type) ? 1 : 0);

/**
 * One moment per run of play: a goal and the brace milestone it triggered end on the same event, so only the
 * stronger one (goal / red card first, then significance) is kept.
 */
function onePerRun(ms: readonly Moment[]): Moment[] {
  const best = new Map<number, Moment>();
  for (const m of ms) {
    const have = best.get(m.seq_end);
    if (!have || rank(m) > rank(have) || (rank(m) === rank(have) && m.significance > have.significance)) best.set(m.seq_end, m);
  }
  return [...best.values()];
}

/**
 * The reel's moments in match order: every goal and red card, then the recap's key moments (most significant
 * first) up to `max` in total, so the reel stays around a minute. Without a recap (or when none of its ids are
 * known) the other places go to the most significant moments of the match. Goals and reds are never cut.
 * Half-time / full-time and moments without an id are never in it.
 */
export function pickReelMoments(moments: readonly Moment[], recap: Recap | null, max = FALLBACK_MAX): Moment[] {
  const usable = onePerRun(moments.filter((m) => m.moment_id && !NEVER_IN.has(m.type)));
  const always = usable.filter((m) => ALWAYS_IN.has(m.type));
  const keyIds = new Set(recap?.key_moment_ids ?? []);
  const key = usable.filter((m) => keyIds.has(m.moment_id!));
  // The recap's picks (or, without them, every moment) compete for the places goals and red cards leave free.
  const pool = (key.length ? key : usable)
    .filter((m) => !ALWAYS_IN.has(m.type))
    .sort((a, b) => b.significance - a.significance || matchOrder(a, b));
  const chosen = [...always, ...pool.slice(0, Math.max(0, max - always.length))];
  return [...new Map(chosen.map((m) => [m.moment_id!, m])).values()].sort(matchOrder);
}

/** "2-1" / "2–1" from a fact value, or null. */
function parseScore(v: unknown): Score | null {
  const r = typeof v === "string" ? /^\s*(\d+)\s*[-–:]\s*(\d+)\s*$/.exec(v) : null;
  return r ? { home: Number(r[1]), away: Number(r[2]) } : null;
}

/**
 * Score before and after every moment (by moment_id), from the goal moments in match order. A goal's
 * `score_after` fact wins when present (it also covers own goals); otherwise the scoring team gets one.
 */
export function runningScores(moments: readonly Moment[], homeTeamId: string | null): Map<string, { before: Score; after: Score }> {
  const out = new Map<string, { before: Score; after: Score }>();
  let score: Score = { home: 0, away: 0 };
  for (const m of [...moments].sort(matchOrder)) {
    const before = score;
    if (m.type === "goal") {
      const fact = parseScore(m.facts.find((f) => f.key === "score_after")?.value);
      score = fact ?? (m.team_id === homeTeamId ? { home: score.home + 1, away: score.away } : { home: score.home, away: score.away + 1 });
    }
    if (m.moment_id) out.set(m.moment_id, { before, after: score });
  }
  return out;
}

/** First sentence of an insight body, capped at `max` characters on a word boundary (with an ellipsis). */
export function shortLine(body: string | null | undefined, max = 140): string | null {
  const text = (body ?? "").replace(/\s+/g, " ").trim();
  if (!text) return null;
  const sentence = /^(.+?[.!?])(\s|$)/.exec(text)?.[1] ?? text;
  if (sentence.length <= max) return sentence;
  const cut = sentence.slice(0, max - 1);
  const space = cut.lastIndexOf(" ");
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).replace(/[\s,;:–-]+$/, "")}…`;
}

/** The reel for a finished match: moments, copy and running score. Deterministic for the same inputs. */
export function buildReel(src: ReelSource, max = FALLBACK_MAX): ReelItem[] {
  const scores = runningScores(src.moments, src.homeTeamId);
  return pickReelMoments(src.moments, src.recap, max).map((m) => {
    const v = src.insights[m.moment_id!]?.variants[0];
    const headline = v?.headline?.trim();
    const s = scores.get(m.moment_id!) ?? { before: { home: 0, away: 0 }, after: { home: 0, away: 0 } };
    return {
      moment: m,
      headline: headline || src.label(m),
      line: headline ? shortLine(v?.body) : null,
      speech: (headline && v?.commentary_line?.trim()) || null,
      lang: v?.language ?? null,
      fromInsight: !!headline,
      scoreBefore: s.before,
      scoreAfter: s.after,
    };
  });
}

// ---------------------------------------------------------------------------------------------------- timing

/** Intro card on screen before the first moment. */
export const INTRO_MS = 3500;
/** A moment whose replay could not load still holds its caption this long. */
export const EMPTY_REPLAY_MS = 3000;
/** Longest a replay can take (load + lead + up to ~12 s of play + hold), used for the step's safety timeout. */
export const REPLAY_BUDGET_MS = 20_000;

/** Rough spoken length of a line (~2.6 words/s plus engine start-up), for the step's safety timeout. */
export const speechBudgetMs = (text: string | null) => (text ? 2500 + text.trim().split(/\s+/).length * 400 : 0);

/**
 * Hard cap for one moment: if the replay or the voice never reports back (Azure slow, the browser engine silent),
 * the reel moves on anyway. Speech starts with the replay, so the cap is the longer of the two plus a margin.
 */
export const stepCapMs = (item: Pick<ReelItem, "speech">, muted: boolean) =>
  Math.max(REPLAY_BUDGET_MS, speechBudgetMs(muted ? null : item.speech) + 4000);

// ---------------------------------------------------------------------------------------------------- state machine

export type ReelPhase = "intro" | "moment" | "outro";

export interface ReelState {
  phase: ReelPhase;
  /** Index of the moment on screen (phase "moment"). */
  index: number;
  /** Number of moments in the reel. */
  n: number;
  paused: boolean;
  replayDone: boolean;
  speechDone: boolean;
  /** Bumps on every step change; completions carry the step they belong to, so late ones are ignored. */
  step: number;
}

export type ReelAction =
  | { type: "intro_done"; step: number }
  | { type: "replay_done"; step: number }
  | { type: "speech_done"; step: number }
  | { type: "timeout"; step: number }
  | { type: "next" }
  | { type: "prev" }
  | { type: "toggle_pause" }
  | { type: "restart" };

export const initialReel = (n: number): ReelState => ({ phase: "intro", index: 0, n, paused: false, replayDone: false, speechDone: false, step: 0 });

const goTo = (s: ReelState, phase: ReelPhase, index = 0): ReelState => ({
  ...s,
  phase,
  index: phase === "moment" ? index : 0,
  replayDone: false,
  speechDone: false,
  step: s.step + 1,
});

/** The step after the current one (intro → first moment → … → outro; the outro is the end). */
function forward(s: ReelState): ReelState {
  if (s.phase === "intro") return s.n ? goTo(s, "moment", 0) : goTo(s, "outro");
  if (s.phase === "moment") return s.index + 1 < s.n ? goTo(s, "moment", s.index + 1) : goTo(s, "outro");
  return s;
}

function back(s: ReelState): ReelState {
  if (s.phase === "outro") return s.n ? goTo(s, "moment", s.n - 1) : goTo(s, "intro");
  if (s.phase === "moment") return s.index > 0 ? goTo(s, "moment", s.index - 1) : goTo(s, "intro");
  return goTo(s, "intro"); // restart the intro card
}

/** A moment is finished when both its replay and its spoken line are done; a pause holds it on screen. */
const settle = (s: ReelState): ReelState => (s.phase === "moment" && !s.paused && s.replayDone && s.speechDone ? forward(s) : s);

export function reelReducer(s: ReelState, a: ReelAction): ReelState {
  switch (a.type) {
    case "intro_done":
      return a.step === s.step && s.phase === "intro" && !s.paused ? forward(s) : s;
    case "replay_done":
      return a.step === s.step ? settle({ ...s, replayDone: true }) : s;
    case "speech_done":
      return a.step === s.step ? settle({ ...s, speechDone: true }) : s;
    case "timeout":
      return a.step === s.step && s.phase === "moment" && !s.paused ? forward(s) : s;
    case "next":
      return forward(s);
    case "prev":
      return back(s);
    case "toggle_pause":
      // Resuming an intro restarts its timer (new step); resuming a finished moment moves on.
      if (s.phase === "outro") return s;
      if (s.paused) return s.phase === "intro" ? { ...s, paused: false, step: s.step + 1 } : settle({ ...s, paused: false });
      return { ...s, paused: true };
    case "restart":
      return { ...initialReel(s.n), step: s.step + 1 };
    default:
      return s;
  }
}
