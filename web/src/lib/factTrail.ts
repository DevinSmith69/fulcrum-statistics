import type { Fact } from "../types";

/*
 * Fact trail: find every number in a piece of insight copy and trace it back to the moment's fact pack.
 *
 * The tokenising rules mirror src/fulcrum/agents/factcheck.py (the deterministic Fact-Checker) so the UI
 * highlights exactly what the checker accepted:
 *   - a number is `\d+(?:[.,]\d+)?`, so "67'", "71 %" and "90+3'" yield 67 / 71 / 90 and 3;
 *   - decimal commas are normalised ("0,34" == "0.34") and comparison is numeric ("0.30" == 0.3);
 *   - sign is ignored (a -22 delta may be written "dropped 22 points");
 *   - thousands groups ("11,240"; "11.240" outside English) are one integer;
 *   - allowed numbers come from fact values, numbers inside string values and labels, and the moment's
 *     minute (clock_ms // 60000 and the minute after).
 */

const NUMBER_RE = /\d+(?:[.,]\d+)?/g;
const THOUSANDS_COMMA_RE = /(?<![\d.,])[1-9]\d{0,2}(?:,\d{3})+(?![\d,]|\.\d)/g;
const THOUSANDS_DOT_RE = /(?<![\d.,])[1-9]\d{0,2}(?:\.\d{3})+(?![\d.]|,\d)/g;
/** Unit glued to a number that we include in the highlight: "71%", "71 %", "67'". */
const SUFFIX_RE = /^(?:[   ]?%|['’′])/;

/** 'es-ES' → 'es'; undefined → 'en'. */
export const baseLanguage = (language?: string | null) => (language || "en").split(/[-_]/)[0].toLowerCase();

/** One numeric token found in copy: its position, raw text and canonical value. */
export interface NumberToken {
  start: number;
  end: number;
  token: string;
  /** Canonical absolute value as a string ("0,30" → "0.3"), or null if unparsable. */
  value: string | null;
}

/** Canonical form of a numeric token, as factcheck.to_decimal compares it. */
export function canonical(token: string): string | null {
  const n = Number(token.replace(",", "."));
  return Number.isFinite(n) ? String(Math.abs(n)) : null;
}

/** Numeric tokens in `text`, in order, with positions (factcheck.numbers_in with offsets). */
export function numbersIn(text: string, language = "en"): NumberToken[] {
  const out: NumberToken[] = [];
  let rest = text;
  const take = (re: RegExp) => {
    rest = rest.replace(re, (m: string, offset: number) => {
      const digits = m.replace(/[.,]/g, "");
      out.push({ start: offset, end: offset + m.length, token: m, value: canonical(digits) });
      return " ".repeat(m.length); // keep offsets stable; spaces separate tokens just like the Python " "
    });
  };
  take(THOUSANDS_COMMA_RE);
  if (baseLanguage(language) !== "en") take(THOUSANDS_DOT_RE);
  for (const m of rest.matchAll(NUMBER_RE)) {
    const start = m.index ?? 0;
    out.push({ start, end: start + m[0].length, token: m[0], value: canonical(m[0]) });
  }
  return out.sort((a, b) => a.start - b.start);
}

/** Where an allowed number came from. Lower rank wins when several facts share a number. */
export type FactSource = "value" | "text" | "label" | "clock";
const RANK: Record<FactSource, number> = { value: 0, text: 1, label: 2, clock: 3 };

export interface FactRef {
  fact: Fact;
  source: FactSource;
}

export interface FactIndexOptions {
  /** The moment's match clock: its minute (and the next) are allowed numbers, as in FactSheet.build. */
  clockMs?: number | null;
  /** Fact keys to prefer on ties (the explanation's key_fact_keys). */
  preferKeys?: readonly string[];
}

export type FactIndex = Map<string, FactRef>;

/** Map each allowed number (canonical string) to the fact that best explains it. */
export function buildFactIndex(facts: readonly Fact[], opts: FactIndexOptions = {}): FactIndex {
  const prefer = new Set(opts.preferKeys ?? []);
  const index: FactIndex = new Map();
  const add = (value: string | null, ref: FactRef) => {
    if (value == null) return;
    const cur = index.get(value);
    const better =
      !cur ||
      RANK[ref.source] < RANK[cur.source] ||
      (RANK[ref.source] === RANK[cur.source] && prefer.has(ref.fact.key) && !prefer.has(cur.fact.key));
    if (better) index.set(value, ref);
  };
  for (const fact of facts) {
    if (typeof fact.value === "number") add(canonical(String(fact.value)), { fact, source: "value" });
    else if (typeof fact.value === "string") for (const t of numbersIn(fact.value)) add(t.value, { fact, source: "text" });
    for (const t of numbersIn(fact.label)) add(t.value, { fact, source: "label" });
  }
  if (opts.clockMs != null) {
    const minute = Math.floor(opts.clockMs / 60_000);
    for (const m of [minute, minute + 1]) {
      add(String(m), { fact: { key: "clock_ms", label: "Match clock at the moment", value: m, unit: "min" }, source: "clock" });
    }
  }
  return index;
}

/** A run of copy: plain text, or a number traced to a fact (`ref`), or a number with no fact (`unmatched`). */
export interface Segment {
  text: string;
  ref?: FactRef;
  unmatched?: boolean;
}

/** Split `text` into plain runs and number runs, each number linked to its fact when one exists. */
export function factSegments(text: string, index: FactIndex, language = "en"): Segment[] {
  const out: Segment[] = [];
  let at = 0;
  for (const tok of numbersIn(text, language)) {
    if (tok.start < at) continue;
    if (tok.start > at) out.push({ text: text.slice(at, tok.start) });
    const ref = tok.value != null ? index.get(tok.value) : undefined;
    let end = tok.end;
    if (ref) {
      const suffix = SUFFIX_RE.exec(text.slice(end));
      if (suffix) end += suffix[0].length;
    }
    out.push(ref ? { text: text.slice(tok.start, end), ref } : { text: text.slice(tok.start, end), unmatched: true });
    at = end;
  }
  if (at < text.length) out.push({ text: text.slice(at) });
  return out;
}

/** How many numbers in the given copy were traced to a fact. */
export function traceCount(texts: readonly string[], index: FactIndex, language = "en") {
  let traced = 0;
  let total = 0;
  for (const text of texts) {
    for (const s of factSegments(text, index, language)) {
      if (s.ref) traced++;
      if (s.ref || s.unmatched) total++;
    }
  }
  return { traced, total };
}

/** "0.34 xG", "71%", "67'", "2-1". */
export function formatFactValue(fact: Fact): string {
  const v = String(fact.value);
  if (!fact.unit) return v;
  return fact.unit === "%" || fact.unit === "'" ? `${v}${fact.unit}` : `${v} ${fact.unit}`;
}

/** One-line tooltip text, e.g. "Shot xG · 0.34 xG · key xg". */
export function describeFact(fact: Fact): string {
  return `${fact.label} · ${formatFactValue(fact)} · key ${fact.key}`;
}
