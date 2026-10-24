import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { fetchCoach } from "../lib/api";
import { citedFacts, coachCopy, coachFactLine, coachFactValue, coachFacts, hasCopy, type CoachView } from "../lib/coach";
import { buildFactIndex } from "../lib/factTrail";
import type { CheckStatus, Insight, Moment } from "../types";
import { FactText } from "./FactTrail";

type Load = { state: "loading" } | { state: "ready"; view: CoachView } | { state: "error" };

/**
 * Coach's view of a goal / big chance (fetched when the panel opens, cached server-side per language): the
 * Coach's points with their numbers traced to the coach_ facts, the takeaway, "based on" and the check badge.
 * A fallback view lists the engine's coach_ facts instead (no LLM copy).
 */
export function CoachPanel({
  id,
  matchId,
  moment,
  insight,
  lang,
  badge,
}: {
  id: string;
  matchId: string;
  moment: Moment;
  insight: Insight;
  lang: string;
  badge: (status: CheckStatus) => ReactNode;
}) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    let live = true;
    setLoad({ state: "loading" });
    fetchCoach(matchId, insight.insight_id ?? "", lang)
      .then((view) => live && setLoad({ state: "ready", view }))
      .catch(() => live && setLoad({ state: "error" }));
    return () => {
      live = false;
    };
  }, [matchId, insight.insight_id, lang, attempt]);

  const facts = useMemo(() => (load.state === "ready" ? load.view.facts : coachFacts(moment.facts)), [load, moment.facts]);
  const defending = facts.find((f) => f.key === "coach_defending_team");
  const index = useMemo(
    () => buildFactIndex([...moment.facts.filter((f) => !f.key.startsWith("coach_")), ...facts], { clockMs: moment.clock_ms }),
    [moment.facts, moment.clock_ms, facts],
  );

  return (
    <div id={id} className="mt-2 rounded-sm border-l-2 border-signal/40 bg-ink-900/70 py-2 pl-2.5 pr-2 text-xs" aria-live="polite">
      <div className="flex items-center gap-2">
        <span className="font-display text-sm font-semibold text-ink-50">{coachCopy(lang, "title")}</span>
        {defending && <span className="truncate text-ink-400">{String(defending.value)}</span>}
        <span className="ml-auto">{load.state === "ready" && badge(load.view.check_status)}</span>
      </div>

      {load.state === "loading" && (
        <div className="mt-2 flex items-center gap-2">
          <span className="shimmer h-3 flex-1 rounded-sm" />
          <span className="text-ink-300">{coachCopy(lang, "loading")}…</span>
        </div>
      )}

      {load.state === "error" && (
        <p className="mt-2 flex items-center gap-2 text-ink-300">
          {coachCopy(lang, "failed")}
          <button type="button" onClick={retry} className="rounded-sm px-1.5 py-0.5 text-ink-100 ring-1 ring-ink-600 hover:ring-ink-400">
            {coachCopy(lang, "retry")}
          </button>
        </p>
      )}

      {load.state === "ready" && hasCopy(load.view) && (
        <>
          <ol className="mt-1.5 flex list-decimal flex-col gap-1 pl-4 text-sm leading-snug text-ink-100 marker:text-ink-500">
            {load.view.points.map((p, i) => (
              <li key={i}>
                <FactText text={p.text} index={index} language={load.view.language} />
              </li>
            ))}
          </ol>
          {load.view.takeaway && (
            <p className="mt-1.5 border-t border-ink-700/70 pt-1.5 text-sm italic leading-snug text-ink-50">
              <FactText text={load.view.takeaway} index={index} language={load.view.language} />
            </p>
          )}
          <BasedOn lang={lang} facts={citedFacts(load.view)} />
        </>
      )}

      {load.state === "ready" && !hasCopy(load.view) && (
        <>
          <p className="mt-1.5 text-ink-400">{coachCopy(lang, "fallback")}</p>
          <ul className="mt-1 flex list-disc flex-col gap-0.5 pl-4 text-ink-100">
            {load.view.facts
              .filter((f) => f.key !== "coach_defending_team")
              .map((f) => (
                <li key={f.key} title={`key ${f.key}`}>
                  {coachFactLine(f)}
                </li>
              ))}
          </ul>
        </>
      )}
    </div>
  );
}

function BasedOn({ lang, facts }: { lang: string; facts: ReturnType<typeof citedFacts> }) {
  if (!facts.length) return null;
  return (
    <div className="mt-2">
      <p className="text-[11px] uppercase tracking-wide text-ink-500">{coachCopy(lang, "basedOn")}</p>
      <dl className="mt-0.5 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5">
        {facts.map((f) => (
          <div key={f.key} className="contents">
            <dt className="min-w-0 truncate text-ink-300" title={`key ${f.key}`}>
              {f.label}
            </dt>
            <dd className="text-right font-semibold text-ink-50">{coachFactValue(f)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
