import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { TEMPLATES, type CueContext } from "../components/cues/CueTemplates";
import { FactText } from "../components/FactTrail";
import { CheckBadge } from "../components/InsightFeed";
import { OverlayLayer } from "../components/OverlayLayer";
import { Pitch } from "../components/Pitch";
import { KitStripe } from "../components/TeamMark";
import { buildFactIndex, traceCount } from "../lib/factTrail";
import { minuteLabel, teamInk } from "../lib/format";
import { momentLabel } from "../lib/i18n";
import {
  AUTO_APPROVE_OPTIONS,
  ageTone,
  changedVariants,
  cleanRequest,
  formatAge,
  graphicSuggestions,
  MAX_REQUEST,
  orderVariants,
  previewCue,
  producerErrorMessage,
  slotKey,
  TEMPLATE_LABEL,
  variantLabel,
} from "../lib/producer";
import {
  approveHeld,
  editHeld,
  fetchProducer,
  killHeld,
  postProducerSettings,
  ProducerApiError,
  producerKey,
  putOnAir,
  requestGraphic,
  setProducerKey,
} from "../lib/producerApi";
import type { MatchFeed } from "../lib/useMatchFeed";
import type { GraphicProposal, HeldItem, InsightVariant, OverlayAnchor, ProducerLogEntry, ProducerState } from "../types";
import { ScoreBug } from "./BroadcastView";

const POLL_MS = 1000;

/** Polls the producer state for the current match; exposes a refresh for after each action. */
function useProducerState(matchId: string | null) {
  const [state, setState] = useState<ProducerState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fetchedAt, setFetchedAt] = useState(() => performance.now());
  const refresh = useCallback(async () => {
    if (!matchId) return;
    try {
      setState(await fetchProducer(matchId));
      setFetchedAt(performance.now());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [matchId]);
  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(t);
  }, [refresh]);
  return { state, setState, error, refresh, fetchedAt };
}

/** Seconds since the last poll, ticking locally so ages move smoothly between polls. */
function useSince(fetchedAt: number) {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(performance.now()), 250);
    return () => window.clearInterval(t);
  }, []);
  return Math.max(0, (now - fetchedAt) / 1000);
}

// --------------------------------------------------------------------------- left: the editorial gate

function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      role="switch"
      aria-checked={on}
      onClick={() => onChange(!on)}
      className="group flex items-center gap-3 text-sm font-medium text-ink-100"
    >
      <span className={`relative h-6 w-11 rounded-full ring-1 transition-colors ${on ? "bg-[#e5484d] ring-[#e5484d]" : "bg-ink-800 ring-ink-600"}`}>
        <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-ink-50 shadow transition-transform ${on ? "translate-x-[22px]" : "translate-x-0.5"}`} />
      </span>
      {label}
    </button>
  );
}

const TONE = {
  fresh: "bg-ink-800 text-ink-100 ring-ink-600",
  waiting: "bg-[#3a2a0c] text-[#ffcf6b] ring-[#7a5a17]",
  stale: "bg-[#3b1214] text-[#ff8b8e] ring-[#7d2a2e]",
} as const;

function HeldCard({ item, feed, matchId, since, onDone }: { item: HeldItem; feed: MatchFeed; matchId: string; since: number; onDone: () => void }) {
  const { insight, moment } = item;
  const insightId = insight.insight_id ?? "";
  const team = moment?.team_id === feed.home?.team_id ? feed.home : moment?.team_id === feed.away?.team_id ? feed.away : null;
  const index = useMemo(
    () => buildFactIndex(moment?.facts ?? [], { clockMs: moment?.clock_ms, preferKeys: insight.explanation.key_fact_keys }),
    [moment, insight.explanation.key_fact_keys],
  );
  const variants = orderVariants(insight.variants, feed.home?.team_id);
  const traced = traceCount(variants.flatMap((v) => [v.headline, v.body]), index);
  const age = item.age_s + since;
  const left = item.auto_release_in_s != null ? Math.max(0, item.auto_release_in_s - since) : null;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, Partial<InsightVariant>>>({});
  const [issues, setIssues] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const act = async (what: string, fn: () => Promise<unknown>) => {
    setBusy(what);
    setError(null);
    try {
      await fn();
      onDone();
    } catch (e) {
      if (e instanceof ProducerApiError && e.status === 422) setIssues(e.issues.length ? e.issues : [e.message]);
      else setError(producerErrorMessage((e as Error).message));
    } finally {
      setBusy(null);
    }
  };

  const sendEdit = () => {
    const changed = changedVariants(insight.variants, draft);
    if (!changed.length) return act("approve", () => approveHeld(matchId, insightId));
    setIssues([]);
    return act("edit", () => editHeld(matchId, insightId, changed));
  };

  const field = "w-full rounded-md bg-ink-950 px-2.5 py-1.5 text-sm text-ink-50 ring-1 ring-ink-600 focus:outline-none focus:ring-signal";
  return (
    <li className="feed-in rounded-lg bg-ink-850 ring-1 ring-ink-700" aria-label={`Held: ${variants[1]?.headline ?? insight.explanation.headline}`}>
      <div className="flex items-center gap-2 border-b border-ink-700/70 px-4 py-2.5 text-xs">
        <KitStripe team={team} className="h-4 w-1" />
        {moment && (
          <>
            <span className="font-display text-base font-semibold text-ink-50">{minuteLabel(moment.clock_ms, moment.period, feed.periodStarts)}</span>
            <span className="font-medium" style={{ color: team ? teamInk(team) : "#a7b0c0" }}>
              {momentLabel("en", moment.type)}
            </span>
          </>
        )}
        <CheckBadge status={insight.check_status} />
        {traced.total > 0 && (
          <span
            title="Numbers in the copy that match the moment's fact pack"
            className={`rounded-sm px-1.5 py-px font-semibold ring-1 ${traced.traced === traced.total ? "text-signal ring-signal/40" : "text-[#ff8b8e] ring-[#7d2a2e]"}`}
          >
            {traced.traced}/{traced.total} numbers traced
          </span>
        )}
        <span className="ml-auto flex items-center gap-2">
          {left != null && <span className="text-ink-400">auto in {Math.ceil(left)} s</span>}
          <span className={`font-display rounded-sm px-2 py-px text-sm font-semibold tabular-nums ring-1 ${TONE[ageTone(age)]}`} title="Time held">
            {formatAge(age)}
          </span>
        </span>
      </div>

      <div className="grid gap-px bg-ink-700/50 sm:grid-cols-2">
        {variants.map((v) => {
          const key = slotKey(v);
          const d = draft[key] ?? {};
          return (
            <div key={key} className="bg-ink-850 px-4 py-3">
              <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-400">{variantLabel(v, feed.home, feed.away)}</div>
              {editing ? (
                <div className="mt-1.5 space-y-1.5">
                  <input
                    aria-label={`${variantLabel(v, feed.home, feed.away)} headline`}
                    className={`${field} font-display font-semibold`}
                    maxLength={90}
                    value={d.headline ?? v.headline}
                    onChange={(e) => setDraft({ ...draft, [key]: { ...d, headline: e.target.value } })}
                  />
                  <textarea
                    aria-label={`${variantLabel(v, feed.home, feed.away)} body`}
                    className={`${field} resize-none leading-snug`}
                    rows={3}
                    maxLength={300}
                    value={d.body ?? v.body}
                    onChange={(e) => setDraft({ ...draft, [key]: { ...d, body: e.target.value } })}
                  />
                </div>
              ) : (
                <>
                  <p className="font-display mt-1 text-[17px] font-semibold leading-snug text-ink-50">
                    <FactText text={v.headline} index={index} language={v.language ?? "en"} />
                  </p>
                  <p className="mt-1 text-sm leading-snug text-ink-300">
                    <FactText text={v.body} index={index} language={v.language ?? "en"} />
                  </p>
                </>
              )}
            </div>
          );
        })}
      </div>

      {issues.length > 0 && (
        <div role="alert" className="border-t border-[#7d2a2e] bg-[#2a0f12] px-4 py-2.5 text-sm text-[#ffb3b5]">
          <div className="font-semibold">Edit rejected by the fact-checker. Fix and resend:</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-[13px]">
            {issues.map((i) => (
              <li key={i}>{i}</li>
            ))}
          </ul>
        </div>
      )}
      {error && <div className="border-t border-ink-700 px-4 py-2 text-sm text-[#ff8b8e]">{error}</div>}

      <div className="flex items-center gap-2 border-t border-ink-700/70 px-4 py-2.5">
        {editing ? (
          <>
            <button
              disabled={!!busy}
              onClick={sendEdit}
              className="rounded-md bg-signal px-3.5 py-1.5 text-sm font-semibold text-ink-950 disabled:opacity-60"
            >
              {busy === "edit" ? "Checking…" : "Check & send"}
            </button>
            <button
              onClick={() => {
                setEditing(false);
                setDraft({});
                setIssues([]);
              }}
              className="rounded-md px-3 py-1.5 text-sm text-ink-300 ring-1 ring-ink-600 hover:text-ink-50"
            >
              Cancel
            </button>
            <span className="ml-auto text-xs text-ink-500">Every number must come from the moment's facts.</span>
          </>
        ) : (
          <>
            <button
              disabled={!!busy}
              onClick={() => act("approve", () => approveHeld(matchId, insightId))}
              className="rounded-md bg-signal px-3.5 py-1.5 text-sm font-semibold text-ink-950 hover:brightness-110 disabled:opacity-60"
            >
              {busy === "approve" ? "Sending…" : "Approve"}
            </button>
            <button
              disabled={!!busy}
              onClick={() => setEditing(true)}
              className="rounded-md px-3.5 py-1.5 text-sm font-medium text-ink-100 ring-1 ring-ink-500 hover:text-ink-50 hover:ring-ink-300"
            >
              Edit
            </button>
            <button
              disabled={!!busy}
              onClick={() => act("kill", () => killHeld(matchId, insightId))}
              className="ml-auto rounded-md px-3.5 py-1.5 text-sm font-medium text-[#ff8b8e] ring-1 ring-[#7d2a2e] hover:bg-[#2a0f12]"
            >
              {busy === "kill" ? "Killing…" : "Kill"}
            </button>
          </>
        )}
      </div>
    </li>
  );
}

const LOG_LABEL: Record<ProducerLogEntry["action"], string> = {
  approve: "Approved",
  edit: "Edited",
  kill: "Killed",
  auto_approve: "Auto-approved",
  release: "Released",
  air: "On air",
  drop: "Dropped",
};

function DecisionLog({ log }: { log: ProducerLogEntry[] }) {
  if (!log.length) return null;
  return (
    <div className="border-t border-ink-700 px-5 py-3">
      <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-500">Decision log</div>
      <ol className="mt-1.5 space-y-1">
        {log.slice(0, 5).map((e, i) => (
          <li key={`${e.at}-${i}`} className="flex items-baseline gap-2 text-[13px]">
            <span className="w-24 shrink-0 font-medium text-ink-300">{LOG_LABEL[e.action]}</span>
            <span className="min-w-0 flex-1 truncate text-ink-100">{e.headline}</span>
            {e.note && <span className="shrink-0 text-ink-500">{e.note}</span>}
          </li>
        ))}
      </ol>
    </div>
  );
}

function KeyPrompt({ onSaved }: { onSaved: () => void }) {
  const [key, setKey] = useState(producerKey());
  return (
    <form
      className="mx-5 mt-4 flex items-center gap-2 rounded-md bg-ink-900 p-3 ring-1 ring-ink-700"
      onSubmit={(e) => {
        e.preventDefault();
        setProducerKey(key.trim());
        onSaved();
      }}
    >
      <label className="text-sm text-ink-300" htmlFor="producer-key">
        Producer key
      </label>
      <input
        id="producer-key"
        type="password"
        value={key}
        onChange={(e) => setKey(e.target.value)}
        className="min-w-0 flex-1 rounded-md bg-ink-950 px-2.5 py-1.5 text-sm ring-1 ring-ink-600 focus:outline-none focus:ring-signal"
      />
      <button className="rounded-md bg-signal px-3 py-1.5 text-sm font-semibold text-ink-950">Unlock</button>
    </form>
  );
}

function GatePanel({ feed }: { feed: MatchFeed }) {
  const matchId = feed.matchId;
  const { state, setState, error, refresh, fetchedAt } = useProducerState(matchId);
  const since = useSince(fetchedAt);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const settings = async (body: { approval_required?: boolean; auto_approve_s?: number }) => {
    if (!matchId) return;
    try {
      setState(await postProducerSettings(matchId, body));
      setSettingsError(null);
    } catch (e) {
      setSettingsError(producerErrorMessage((e as Error).message));
    }
  };
  const on = !!state?.approval_required;
  const held = state?.held ?? [];

  return (
    <section className="flex min-h-0 flex-col border-r border-ink-700" aria-label="Editorial gate">
      <div className="border-b border-ink-700 px-5 py-4">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
          <h2 className="font-display text-2xl font-semibold">Editorial gate</h2>
          <Toggle on={on} onChange={(v) => settings({ approval_required: v })} label="Approval required" />
          <label className="flex items-center gap-2 text-sm text-ink-300">
            Auto-approve
            <select
              value={state?.auto_approve_s ?? 0}
              onChange={(e) => settings({ auto_approve_s: Number(e.target.value) })}
              className="rounded-md bg-ink-850 px-2 py-1 text-sm text-ink-100 ring-1 ring-ink-700 focus:outline-none focus:ring-signal"
            >
              {AUTO_APPROVE_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s === 0 ? "Off" : `after ${s} s`}
                </option>
              ))}
            </select>
          </label>
          <span className="ml-auto flex items-center gap-2 text-sm">
            <span className={`font-display text-3xl font-bold leading-none ${held.length ? "text-[#ffcf6b]" : "text-ink-500"}`}>{held.length}</span>
            <span className="text-ink-400">held</span>
          </span>
        </div>
        <p className="mt-2 text-sm text-ink-400">
          {on
            ? "New insights and their graphics wait here. Nothing reaches viewers until you approve, edit or kill it."
            : "Off: insights go straight to air. Switch on to review the AI copy before viewers see it."}
        </p>
        {settingsError && <p className="mt-2 text-sm text-[#ff8b8e]">{settingsError}</p>}
      </div>
      {error?.startsWith("401") && <KeyPrompt onSaved={refresh} />}
      {error && !error.startsWith("401") && <p className="px-5 pt-3 text-sm text-[#ff8b8e]">{producerErrorMessage(error)}</p>}
      <ol className="scroll-thin flex-1 space-y-3 overflow-y-auto p-5">
        {held.map((item) => (
          <HeldCard key={item.insight.insight_id ?? item.held_at} item={item} feed={feed} matchId={matchId!} since={since} onDone={refresh} />
        ))}
        {held.length === 0 && (
          <li className="flex h-full min-h-40 flex-col items-center justify-center rounded-lg border border-dashed border-ink-700 text-center text-sm text-ink-500">
            {on ? "Queue clear. The next moment's insight will be held here." : "Nothing held."}
          </li>
        )}
      </ol>
      <DecisionLog log={state?.log ?? []} />
    </section>
  );
}

// --------------------------------------------------------------------------- right: graphics desk + program monitor

const PREVIEW_POS: Record<OverlayAnchor, string> = {
  top_left: "top-0 left-0",
  top_right: "top-0 right-0",
  bottom_left: "bottom-0 left-0",
  bottom_right: "bottom-0 right-0",
  bottom_center: "bottom-0 inset-x-0 flex justify-center",
};

function Monitor({ label, tally, children }: { label: string; tally: "red" | "green"; children: ReactNode }) {
  return (
    <div className="relative aspect-video w-full overflow-hidden rounded-md bg-black ring-1 ring-ink-700">
      {children}
      <span
        className={`font-display absolute left-2 top-2 z-10 rounded-sm px-2 py-px text-sm font-bold tracking-[0.2em] ${tally === "red" ? "bg-[#e5484d] text-white" : "bg-[#2fbf71] text-ink-950"}`}
      >
        {label}
      </span>
    </div>
  );
}

function GraphicsDesk({ feed }: { feed: MatchFeed }) {
  const matchId = feed.matchId;
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<"request" | "air" | null>(null);
  const [proposal, setProposal] = useState<GraphicProposal | null>(null);
  const [aired, setAired] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const suggestions = graphicSuggestions(feed.state, feed.home, feed.away);

  const ask = async (raw: string) => {
    const req = cleanRequest(raw);
    if (!req || !matchId) return;
    setText(req);
    setBusy("request");
    setError(null);
    setAired(null);
    try {
      setProposal(await requestGraphic(matchId, req));
    } catch (e) {
      setProposal(null);
      setError(producerErrorMessage((e as Error).message));
    } finally {
      setBusy(null);
    }
  };

  const air = async () => {
    if (!proposal?.ok || !matchId) return;
    setBusy("air");
    try {
      const cue = await putOnAir(matchId, proposal.proposal_id ?? "");
      setAired(cue.cue_id ?? "aired");
    } catch (e) {
      setError(producerErrorMessage((e as Error).message));
    } finally {
      setBusy(null);
    }
  };

  const cue = proposal?.ok && proposal.cue ? previewCue(proposal.cue) : null;
  const Tpl = cue ? TEMPLATES[cue.template] : null;
  const ctx: CueContext = { home: feed.home, away: feed.away, state: feed.state, moment: undefined, periodStarts: feed.periodStarts, size: "md" };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="font-display text-2xl font-semibold">Request a graphic</h2>
        <p className="mt-0.5 text-sm text-ink-400">Plain language in, a fact-checked overlay cue out. Every number comes from the live match data.</p>
        <form
          className="mt-3 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            ask(text);
          }}
        >
          <input
            ref={input}
            value={text}
            maxLength={MAX_REQUEST}
            onChange={(e) => setText(e.target.value)}
            placeholder="e.g. stat card on a player's passing"
            aria-label="Graphic request"
            className="min-w-0 flex-1 rounded-md bg-ink-900 px-3 py-2 text-[15px] text-ink-50 ring-1 ring-ink-600 placeholder:text-ink-500 focus:outline-none focus:ring-signal"
          />
          <button
            disabled={busy !== null || !cleanRequest(text)}
            className="rounded-md bg-signal px-4 py-2 text-sm font-semibold text-ink-950 disabled:opacity-50"
          >
            {busy === "request" ? "Building…" : "Generate"}
          </button>
        </form>
        <div className="mt-2.5 flex flex-wrap gap-2">
          {suggestions.map((s) => (
            <button
              key={s}
              disabled={busy !== null}
              onClick={() => ask(s)}
              className="rounded-full bg-ink-850 px-3 py-1 text-[13px] text-ink-100 ring-1 ring-ink-700 hover:text-ink-50 hover:ring-signal/60 disabled:opacity-50"
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-3 xl:grid-cols-2">
        <div>
          <Monitor label="PVW" tally="green">
            <div className="absolute inset-0 bg-pitch opacity-70" />
            {busy === "request" && <div className="shimmer absolute inset-0 opacity-20" />}
            {cue && Tpl ? (
              <div className={`absolute p-3 ${PREVIEW_POS[cue.anchor ?? "bottom_left"]}`}>
                <div key={proposal?.proposal_id} className="cue-enter origin-top-left">
                  <Tpl cue={cue} ctx={ctx} />
                </div>
              </div>
            ) : (
              <div className="absolute inset-0 flex items-center justify-center p-6 text-center text-sm text-ink-400">
                {busy === "request" ? "The graphics producer is building it…" : proposal && !proposal.ok ? "No graphic" : "Preview appears here"}
              </div>
            )}
          </Monitor>
          {proposal && (
            <div className="mt-2.5 rounded-md bg-ink-900 p-3 ring-1 ring-ink-700">
              {proposal.ok && proposal.cue ? (
                <>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-ink-300">
                    <span className="font-semibold text-ink-50">{TEMPLATE_LABEL[proposal.cue.template] ?? proposal.cue.template}</span>
                    <span>{proposal.cue.anchor?.replace("_", " ")}</span>
                    <span>priority {proposal.cue.priority}</span>
                    <CheckBadge status={proposal.check_status} />
                    {proposal.latency_ms != null && <span className="text-ink-500">{(proposal.latency_ms / 1000).toFixed(1)} s</span>}
                  </div>
                  {(proposal.fact_keys?.length ?? 0) > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {proposal.fact_keys?.map((k) => (
                        <code key={k} className="rounded-sm bg-ink-800 px-1.5 py-px text-[11px] text-signal">
                          {k}
                        </code>
                      ))}
                    </div>
                  )}
                  <div className="mt-3 flex items-center gap-2">
                    <button
                      onClick={air}
                      disabled={busy !== null}
                      className="font-display rounded-md bg-[#e5484d] px-5 py-2 text-lg font-bold tracking-wide text-white shadow-[0_0_24px_-6px_#e5484d] hover:brightness-110 disabled:opacity-60"
                    >
                      {busy === "air" ? "Taking…" : "Put on air"}
                    </button>
                    {aired && <span className="text-sm font-medium text-[#2fbf71]">On air now</span>}
                  </div>
                </>
              ) : (
                <div role="alert" className="text-sm">
                  <div className="font-semibold text-[#ffcf6b]">Refused</div>
                  <p className="mt-0.5 text-ink-100">{proposal.refusal}</p>
                  {(proposal.issues?.length ?? 0) > 0 && (
                    <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-[13px] text-ink-400">
                      {proposal.issues?.map((i) => (
                        <li key={i}>{i}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          )}
          {error && <p className="mt-2 text-sm text-[#ff8b8e]">{error}</p>}
        </div>
        <div>
          <Monitor label="PGM" tally="red">
            <Pitch model={feed.pitch} label="Program output" />
            <OverlayLayer feed={feed} size="md" />
            <div className="absolute left-1/2 top-2 origin-top -translate-x-1/2 scale-75">
              <ScoreBug feed={feed} />
            </div>
          </Monitor>
          <p className="mt-2 text-xs text-ink-500">Program: what viewers see now, cues included.</p>
        </div>
      </div>
    </div>
  );
}

/** Producer console: the editorial gate (left) and the graphics desk with preview + program monitors (right). */
export function ProducerView({ feed }: { feed: MatchFeed }) {
  return (
    <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
      <GatePanel feed={feed} />
      <section className="scroll-thin min-h-0 overflow-y-auto p-5" aria-label="Graphics desk">
        <GraphicsDesk feed={feed} />
      </section>
    </div>
  );
}
