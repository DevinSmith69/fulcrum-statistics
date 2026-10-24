import { minuteLabel, teamInk } from "../lib/format";
import { momentLabel, t } from "../lib/i18n";
import type { MatchFeed } from "../lib/useMatchFeed";
import type { CheckStatus, Insight, Moment } from "../types";
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

function Item({ m, insight, feed, lang, dense }: { m: Moment; insight?: Insight; feed: MatchFeed; lang: string; dense: boolean }) {
  const team = m.team_id === feed.home?.team_id ? feed.home : m.team_id === feed.away?.team_id ? feed.away : null;
  const v = insight?.variants[0];
  const major = m.type === "goal" || m.type === "red_card" || m.type === "full_time";
  return (
    <li className={`feed-in relative rounded-md bg-ink-850 ring-1 ${major ? "ring-ink-600" : "ring-ink-700/70"} ${dense ? "p-2.5" : "p-3"}`}>
      <div className="flex items-center gap-2 text-xs">
        <KitStripe team={team} className="h-3.5 w-1" />
        <span className="font-display text-sm font-semibold text-ink-50">{minuteLabel(m.clock_ms, m.period, feed.periodStarts)}</span>
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
          <p className={`font-display mt-1.5 font-semibold leading-snug text-ink-50 ${dense ? "text-base" : "text-lg"}`}>{v.headline}</p>
          {!dense && <p className="mt-1 text-sm leading-snug text-ink-300">{v.body}</p>}
          {insight?.latency_ms != null && !dense && (
            <p className="mt-1.5 text-[11px] text-ink-500">Explained in {(insight.latency_ms / 1000).toFixed(1)} s</p>
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

/** Newest-first feed of moments; shows "analysing…" until the insight for a moment arrives. */
export function InsightFeed({ feed, lang, limit = 30, dense = false }: { feed: MatchFeed; lang: string; limit?: number; dense?: boolean }) {
  const items = [...feed.moments].reverse().slice(0, limit);
  if (!items.length) {
    return <p className="rounded-md border border-dashed border-ink-700 p-4 text-sm text-ink-400">{t(lang, "waiting")}.</p>;
  }
  return (
    <ul className="flex flex-col gap-2">
      {items.map((m) => (
        <Item key={m.moment_id} m={m} insight={m.moment_id ? feed.insights[m.moment_id] : undefined} feed={feed} lang={lang} dense={dense} />
      ))}
    </ul>
  );
}
