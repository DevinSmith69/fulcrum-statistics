import { useEffect, useId, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { describeFact, factSegments, formatFactValue, type FactIndex, type FactRef } from "../lib/factTrail";
import type { CheckStatus, Fact, Insight, Moment } from "../types";

interface Tip {
  text: string;
  rect: DOMRect;
}

/**
 * Tooltip drawn in a portal with fixed positioning so the scrolling feed never clips it. It is visual only:
 * screen readers get the same text through each number's aria-describedby.
 */
function FloatingTip({ tip }: { tip: Tip }) {
  const above = tip.rect.top > 44;
  const x = Math.min(Math.max(tip.rect.left + tip.rect.width / 2, 120), window.innerWidth - 120);
  return createPortal(
    <div
      aria-hidden
      className="pointer-events-none fixed z-50 max-w-[240px] rounded-sm bg-ink-950 px-2 py-1 text-[11px] leading-snug text-ink-100 ring-1 ring-signal/40 shadow-[0_8px_24px_-8px_rgba(0,0,0,0.9)]"
      style={{ left: x, top: above ? tip.rect.top - 6 : tip.rect.bottom + 6, transform: `translate(-50%, ${above ? "-100%" : "0"})` }}
    >
      {tip.text}
    </div>,
    document.body,
  );
}

/** One number traced to a fact: highlighted, focusable, described by the fact. */
function FactNumber({ text, factRef }: { text: string; factRef: FactRef }) {
  const id = useId();
  const [tip, setTip] = useState<Tip | null>(null);
  const desc = describeFact(factRef.fact);

  useEffect(() => {
    if (!tip) return;
    const hide = () => setTip(null);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && hide();
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
      window.removeEventListener("keydown", onKey);
    };
  }, [tip]);

  const show = (el: HTMLElement) => setTip({ text: desc, rect: el.getBoundingClientRect() });
  return (
    <>
      <span
        tabIndex={0}
        aria-describedby={id}
        onMouseEnter={(e) => show(e.currentTarget)}
        onMouseLeave={() => setTip(null)}
        onFocus={(e) => show(e.currentTarget)}
        onBlur={() => setTip(null)}
        className="fact-num cursor-help rounded-[2px] bg-signal/10 text-inherit underline decoration-signal/55 decoration-dotted decoration-1 underline-offset-[3px] [box-decoration-break:clone] hover:bg-signal/20 focus-visible:bg-signal/20"
      >
        {text}
      </span>
      <span id={id} hidden>
        {desc}
      </span>
      {tip && <FloatingTip tip={tip} />}
    </>
  );
}

/** Insight copy with every number that matches the moment's fact pack highlighted and explained. */
export function FactText({ text, index, language }: { text: string; index: FactIndex; language: string }): ReactNode {
  return factSegments(text, index, language).map((s, i) =>
    s.ref ? <FactNumber key={i} text={s.text} factRef={s.ref} /> : s.text,
  );
}

const GUARANTEE: Record<CheckStatus, string> = {
  passed: "Passed on the first draft.",
  repaired: "The first draft failed and was regenerated once; this version passed.",
  fallback: "Drafts failed twice, so a deterministic template written from the facts is shown.",
};

/**
 * The facts the explanation rests on (Explanation.key_fact_keys) and what the fact-check guarantees.
 * Rendered inside a disclosure on each insight card.
 */
export function FactTrailPanel({ id, moment, insight, traced }: { id: string; moment: Moment; insight: Insight; traced: { traced: number; total: number } }) {
  const byKey = new Map<string, Fact>(moment.facts.map((f) => [f.key, f]));
  const keys = insight.explanation.key_fact_keys;
  return (
    <div id={id} className="mt-2 rounded-sm border-l-2 border-signal/40 bg-ink-900/70 py-2 pl-2.5 pr-2 text-xs">
      {keys.length > 0 ? (
        <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1">
          {keys.map((k) => {
            const f = byKey.get(k);
            return (
              <div key={k} className="contents">
                <dt className="min-w-0 truncate text-ink-300" title={f ? `key ${k}` : undefined}>
                  {f ? f.label : k}
                  <span className="ml-1.5 font-mono text-[10px] text-ink-500">{k}</span>
                </dt>
                <dd className="font-display text-right text-sm font-semibold leading-tight text-ink-50">
                  {f ? formatFactValue(f) : <span className="text-xs font-normal text-ink-400">not in fact pack</span>}
                </dd>
              </div>
            );
          })}
        </dl>
      ) : (
        <p className="text-ink-400">No key facts listed for this explanation.</p>
      )}
      <p className="mt-2 leading-snug text-ink-400">
        <span className="font-medium text-ink-100">{traced.total ? `${traced.traced} of ${traced.total} numbers traced. ` : ""}</span>
        A deterministic checker (no AI) verified every number in this copy exists in the moment's fact pack. {GUARANTEE[insight.check_status]}
      </p>
    </div>
  );
}
