import { useId, useMemo, useRef, useState } from "react";
import { buildFactIndex, traceCount } from "../lib/factTrail";
import { minuteLabel, teamInk } from "../lib/format";
import { momentLabel, t } from "../lib/i18n";
import { isReplayable } from "../lib/replay";
import type { MatchFeed } from "../lib/useMatchFeed";
import type { CheckStatus, Insight, Moment } from "../types";
import { FactText, FactTrailPanel } from "./FactTrail";
import { MiniReplay } from "./MiniReplay";
import { KitStripe } from "./TeamMark";

const STATUS: Record<CheckStatus, { label: string; cls: string; title: string }> = {
  passed: { label: "Passed", cls: "text-signal ring-signal/50", title: "Every number matched the moment's fact pack on the first draft" },
  repaired: { label: "Repaired", cls: "text-ink-50 ring-ink-300/70", title: "First draft failed the fact-check; regenerated once, then passed" },
  fallback: { label: "Fallback", cls: "text-ink-300 ring-ink-500 border-dashed", title: "Draft failed the fact-check twice; deterministic template used" },
};

export function CheckBadge({ status }: { status: CheckStatus }) {
  const s = STATUS[status];
  return (
    <span title={s.title} className={`inline-flex items-center gap-1 rounded-sm px-1.5 py-px text-[11px] font-semibold ring-1 ${s.cls}`}>
      <span aria-hidden>{status === "passed" ? "✓" : status === "repaired" ? "↻" : "≡"}</span>
      {s.label}
    </span>
  );
}

function Item({ m, insight, feed, lang, dense, newest, anchor, highlighted }: { m: Moment; insight?: Insight; feed: MatchFeed; lang: string; dense: boolean; newest: boolean; anchor: boolean; highlighted: boolean }) {
  const team = m.team_id === feed.home?.team_id ? feed.home : m.team_id === feed.away?.team_id ? feed.away : null;
  const v = insight?.variants[0];
  const major = m.type === "goal" || m.type === "red_card" || m.type === "full_time";
  const copyLang = v?.language ?? lang;
  const keyFacts = insight?.explanation.key_fact_keys;
  const index = useMemo(() => buildFactIndex(m.facts, { clockMs: m.clock_ms, preferKeys: keyFacts }), [m.facts, m.clock_ms, keyFacts]);
  const traced = useMemo(() => (v ? traceCount([v.headline, v.body], index, copyLang) : { traced: 0, total: 0 }), [v, index, copyLang]);
  const [trailOpen, setTrailOpen] = useState(false);
  const [replayOpen, setReplayOpen] = useState(false);
  const replayBtn = useRef<HTMLButtonElement>(null);
  const trailId = useId();
  const minute = minuteLabel(m.clock_ms, m.period, feed.periodStarts);
  return (
    <li
      id={anchor && m.moment_id ? momentAnchorId(m.moment_id) : undefined}
      className={`feed-in relative rounded-md bg-ink-850 transition-shadow ${highlighted ? "ring-2 ring-signal" : `ring-1 ${major ? "ring-ink-600" : "ring-ink-700/70"}`} ${dense ? "p-2.5" : "p-3"}`}
    >
      <div className="flex items-center gap-2 text-xs">
        <KitStripe team={team} className="h-3.5 w-1" />
        <span className="font-display text-sm font-semibold text-ink-50">{minute}</span>
        <span className="font-medium" style={{ color: team ? teamInk(team) : "#a7b0c0" }}>
          {momentLabel(lang, m.type)}
        </span>
        <span className="ml-auto flex items-center gap-2">
          <span className="h-1 w-10 overflow-hidden rounded-full bg-ink-700" title={`Significance ${Math.round(m.significance * 100)}`}>
            <span className="block h-full bg-ink-300" style={{ width: `${m.significance * 100}%` }} />
          </span>
          {insight && <CheckBadge status={insight.check_status} />}
        </span>
      </div>
      {v ? (
        <>
          <p className={`font-display mt-1.5 font-semibold leading-snug text-ink-50 ${dense ? "text-base" : "text-lg"}`}>
            <FactText text={v.headline} index={index} language={copyLang} />
          </p>
          {/* Dense (split view): the body is where personas differ, so keep it, clamped except for the newest. */}
          <p className={`mt-1 text-sm leading-snug text-ink-300 ${dense && !newest ? "line-clamp-3" : ""}`}>
            <FactText text={v.body} index={index} language={copyLang} />
          </p>
          <div className="mt-1.5 flex items-center gap-3 text-[11px] text-ink-500">
            <button
              type="button"
              aria-expanded={trailOpen}
              aria-controls={trailId}
              onClick={() => setTrailOpen((o) => !o)}
              className="flex items-center gap-1 rounded-sm hover:text-ink-100"
            >
              <span aria-hidden className={`inline-block transition-transform ${trailOpen ? "rotate-90" : ""}`}>›</span>
              Fact trail
              {traced.total > 0 && (
                <span className="text-ink-400">
                  · {traced.traced}/{traced.total} numbers
                </span>
              )}
            </button>
            {insight?.latency_ms != null && !dense && <span>Explained in {(insight.latency_ms / 1000).toFixed(1)} s</span>}
            {isReplayable(m) && (
              <button
                ref={replayBtn}
                type="button"
                aria-pressed={replayOpen}
                onClick={() => setReplayOpen((o) => !o)}
                className={`ml-auto flex items-center gap-1 rounded-sm px-1.5 py-0.5 font-medium ring-1 transition-colors ${replayOpen ? "text-signal ring-signal/50" : "text-ink-300 ring-ink-600 hover:text-ink-50 hover:ring-ink-400"}`}
              >
                <span aria-hidden>▶</span>
                Replay
              </button>
            )}
          </div>
          {trailOpen && insight && <FactTrailPanel id={trailId} moment={m} insight={insight} traced={traced} />}
          {replayOpen && (
            <MiniReplay
              moment={m}
              home={feed.home}
              away={feed.away}
              title={`${momentLabel(lang, m.type)} ${minute}`}
              onClose={(hadFocus) => {
                setReplayOpen(false);
                if (hadFocus) replayBtn.current?.focus();
              }}
            />
          )}
        </>
      ) : (
        <div className="mt-2 flex items-center gap-2" aria-live="polite">
          <span className="shimmer h-3 flex-1 rounded-sm" />
          <span className="text-xs text-ink-300">{t(lang, "analysing")}…</span>
        </div>
      )}
    </li>
  );
}

/** DOM id of a moment's feed item (only on an `anchored` feed), used by "Ask the studio" citations. */
export const momentAnchorId = (momentId: string) => `moment-${momentId}`;

/** Newest-first feed of moments; shows "analysing…" until the insight for a moment arrives.
 * `anchored` gives each item a DOM id (one anchored feed per page); `highlightId` rings that moment. */
export function InsightFeed({
  feed,
  lang,
  limit = 30,
  dense = false,
  anchored = false,
  highlightId = null,
}: {
  feed: MatchFeed;
  lang: string;
  limit?: number;
  dense?: boolean;
  anchored?: boolean;
  highlightId?: string | null;
}) {
  const items = [...feed.moments].reverse().slice(0, limit);
  if (!items.length) {
    return <p className="rounded-md border border-dashed border-ink-700 p-4 text-sm text-ink-400">{t(lang, "waiting")}.</p>;
  }
  return (
    <ul className="flex flex-col gap-2">
      {items.map((m, i) => (
        <Item key={m.moment_id} m={m} insight={m.moment_id ? feed.insights[m.moment_id] : undefined} feed={feed} lang={lang} dense={dense} newest={i === 0} anchor={anchored} highlighted={highlightId != null && m.moment_id === highlightId} />
      ))}
    </ul>
  );
}
