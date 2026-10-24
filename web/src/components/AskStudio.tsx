import { useEffect, useRef, useState } from "react";
import { askStudio } from "../lib/api";
import { askCopy, askErrorMessage, cleanQuestion, MAX_QUESTION, suggestedQuestions } from "../lib/ask";
import { minuteLabel } from "../lib/format";
import { momentLabel } from "../lib/i18n";
import type { MatchFeed } from "../lib/useMatchFeed";
import type { AskAnswer, ViewerPrefs } from "../types";
import { CheckBadge } from "./InsightFeed";

const OPEN_KEY = "fulcrum.ask.open";

function readOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * "Ask the studio": a viewer types a question about the live match and the Studio Analyst answers from this
 * match's data only, fact-checked like every insight, in the viewer's language and persona. Collapsible so it
 * doesn't crowd the analysis rail; cited moments are chips that jump to the moment in the feed.
 */
export function AskStudio({ feed, prefs, onCite }: { feed: MatchFeed; prefs: ViewerPrefs; onCite?: (momentId: string) => void }) {
  const lang = prefs.language ?? "en";
  const [open, setOpen] = useState(readOpen);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<AskAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);

  useEffect(() => {
    try {
      localStorage.setItem(OPEN_KEY, open ? "1" : "0");
    } catch {
      /* storage unavailable */
    }
  }, [open]);

  // A new match (or reconnect to another one) makes the old answer meaningless.
  useEffect(() => {
    request.current += 1;
    setAnswer(null);
    setError(null);
    setBusy(false);
  }, [feed.matchId]);

  async function ask(raw: string) {
    const q = cleanQuestion(raw);
    if (!q || !feed.matchId || busy) return;
    const id = ++request.current;
    setQuestion(q);
    setBusy(true);
    setError(null);
    try {
      const got = await askStudio(feed.matchId, {
        question: q,
        persona: prefs.persona ?? "casual",
        language: lang,
        favourite_team_id: prefs.favourite_team_id ?? null,
      });
      if (id === request.current) setAnswer(got);
    } catch (e) {
      if (id === request.current) {
        setAnswer(null);
        setError(askErrorMessage(e instanceof Error ? e.message : String(e), lang));
      }
    } finally {
      if (id === request.current) setBusy(false);
    }
  }

  const cited = (answer?.cited_moment_ids ?? [])
    .map((mid) => feed.moments.find((m) => m.moment_id === mid))
    .filter((m): m is NonNullable<typeof m> => m != null);

  return (
    <div className="panel shrink-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls="ask-studio-body"
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left"
      >
        <h2 className="text-sm font-medium text-ink-100">{askCopy(lang, "title")}</h2>
        <span aria-hidden className={`text-xs text-ink-400 transition-transform ${open ? "rotate-180" : ""}`}>
          ▾
        </span>
      </button>
      {open && (
        <div id="ask-studio-body" className="flex flex-col gap-3 px-4 pb-4">
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void ask(question);
            }}
          >
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              maxLength={MAX_QUESTION}
              placeholder={askCopy(lang, "placeholder")}
              aria-label={askCopy(lang, "title")}
              className="min-w-0 flex-1 rounded-md bg-ink-850 px-3 py-1.5 text-sm text-ink-50 ring-1 ring-ink-700 placeholder:text-ink-500 focus:outline-none focus:ring-signal/70"
            />
            <button
              type="submit"
              disabled={busy || !cleanQuestion(question) || !feed.matchId}
              className="rounded-md bg-signal px-3 py-1.5 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {askCopy(lang, "ask")}
            </button>
          </form>
          <div className="flex flex-wrap gap-1.5">
            {suggestedQuestions(lang).map((s) => (
              <button
                key={s}
                type="button"
                disabled={busy || !feed.matchId}
                onClick={() => void ask(s)}
                className="rounded-full px-2.5 py-1 text-xs text-ink-300 ring-1 ring-ink-700 hover:text-ink-50 hover:ring-ink-500 disabled:opacity-40"
              >
                {s}
              </button>
            ))}
          </div>
          {busy && (
            <div className="flex items-center gap-2" aria-live="polite">
              <span className="shimmer h-3 flex-1 rounded-sm" />
              <span className="text-xs text-ink-300">{askCopy(lang, "thinking")}…</span>
            </div>
          )}
          {!busy && error && (
            <p className="rounded-md border border-dashed border-ink-700 p-3 text-sm text-ink-300" role="status">
              {error}
            </p>
          )}
          {!busy && answer && (
            <div className="feed-in rounded-md bg-ink-850 p-3 ring-1 ring-ink-700/70" aria-live="polite">
              <div className="flex items-center gap-2 text-xs">
                <span className="min-w-0 flex-1 truncate text-ink-400" title={answer.question}>
                  {answer.question}
                </span>
                <CheckBadge status={answer.check_status} />
              </div>
              <p className="mt-1.5 text-sm leading-snug text-ink-50">{answer.answer}</p>
              {!answer.answerable && <p className="mt-1.5 text-[11px] text-ink-500">{askCopy(lang, "declined")}</p>}
              {cited.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px]">
                  <span className="text-ink-500">{askCopy(lang, "cited")}</span>
                  {cited.map((m) => (
                    <button
                      key={m.moment_id}
                      type="button"
                      onClick={() => m.moment_id && onCite?.(m.moment_id)}
                      className="rounded-sm px-1.5 py-px font-medium text-ink-100 ring-1 ring-ink-600 hover:ring-signal/70"
                    >
                      {minuteLabel(m.clock_ms, m.period, feed.periodStarts)} {momentLabel(lang, m.type)}
                    </button>
                  ))}
                </div>
              )}
              {answer.latency_ms != null && (
                <p className="mt-1.5 text-[11px] text-ink-500">{(answer.latency_ms / 1000).toFixed(1)} s</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
