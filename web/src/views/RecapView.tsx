import { useState } from "react";
import { CheckBadge } from "../components/InsightFeed";
import { KitStripe } from "../components/TeamMark";
import { WinProbChart } from "../components/WinProbChart";
import { as100, fixtureColours, minuteLabel } from "../lib/format";
import { momentLabel, personaLabel } from "../lib/i18n";
import type { MatchFeed } from "../lib/useMatchFeed";
import type { TeamLiveStats, ViewerPrefs } from "../types";

const ROWS: { label: string; get: (t: TeamLiveStats) => number; fmt?: (v: number) => string }[] = [
  { label: "Expected goals (xG)", get: (t) => t.xg ?? 0, fmt: (v) => v.toFixed(2) },
  { label: "Shots", get: (t) => t.shots ?? 0 },
  { label: "Shots on target", get: (t) => t.shots_on_target ?? 0 },
  { label: "Possession", get: (t) => as100(t.possession_pct), fmt: (v) => `${Math.round(v)}%` },
  { label: "Pass accuracy", get: (t) => as100(t.pass_accuracy), fmt: (v) => `${Math.round(v)}%` },
  { label: "Avg pass difficulty", get: (t) => t.avg_pass_difficulty ?? 0, fmt: (v) => v.toFixed(1) },
  { label: "Box entries", get: (t) => t.box_entries ?? 0 },
  { label: "Corners", get: (t) => t.corners ?? 0 },
  { label: "Fouls", get: (t) => t.fouls ?? 0 },
  { label: "Hardest shot", get: (t) => t.max_shot_speed_kmh ?? 0, fmt: (v) => `${Math.round(v)} km/h` },
];

/** Full-time recap: the persona's match report, key moments, outcome probability story and team stats. */
export function RecapView({ feed, prefs, onPlayHighlights }: { feed: MatchFeed; prefs: ViewerPrefs; onPlayHighlights?: () => void }) {
  const { recap, state, home, away } = feed;
  const c = fixtureColours(home, away);

  if (!recap) {
    const minute = state ? minuteLabel(state.clock_ms, state.period, feed.periodStarts) : null;
    return (
      <main className="flex flex-1 items-center justify-center p-8">
        <div className="panel max-w-lg p-8 text-center">
          {feed.finished ? (
            <>
              <h1 className="font-display text-4xl font-semibold">Full time: writing your recap</h1>
              <p className="mt-3 text-ink-300" aria-live="polite">
                The reporter agent is writing it up for your persona and language, using only numbers from the match. This takes
                about half a minute.
              </p>
              <span className="shimmer mx-auto mt-5 block h-2 w-48 rounded-full" />
              {onPlayHighlights && (
                <button type="button" onClick={onPlayHighlights} className="mx-auto mt-5 block text-sm font-semibold text-signal underline-offset-4 hover:underline">
                  Can't wait? Play the top moments now <kbd className="ml-1 text-ink-500">H</kbd>
                </button>
              )}
            </>
          ) : (
            <>
              <h1 className="font-display text-4xl font-semibold">The recap is written at full time</h1>
              <p className="mt-3 text-ink-300">
                {minute ? `We're at ${minute}. ` : ""}
                When the match ends, the reporter agent writes it up for your persona and language, using only numbers from the
                match.
              </p>
            </>
          )}
          <a href="#/" className="mt-6 inline-block rounded-md bg-signal px-4 py-2 text-sm font-semibold text-ink-950">
            Watch in Match Studio
          </a>
        </div>
      </main>
    );
  }

  const keyIds = new Set(recap.key_moment_ids);
  const key = feed.moments.filter((m) => m.moment_id && keyIds.has(m.moment_id));
  const potm = recap.player_of_the_match_id ? [...(home?.players ?? []), ...(away?.players ?? [])].find((p) => p.player_id === recap.player_of_the_match_id) : null;
  const potmTeam = potm && home?.players.includes(potm) ? home : away;
  const potmStats = potm ? state?.players?.[potm.player_id] : undefined;

  return (
    <main className="scroll-thin flex-1 overflow-y-auto">
      <div className="mx-auto grid max-w-[1500px] grid-cols-1 gap-6 p-6 lg:grid-cols-[minmax(0,1fr)_420px]">
        <section className="flex flex-col gap-6">
          <div className="panel p-8">
            <div className="flex items-center gap-2 text-sm text-ink-300">
              <span>Full time</span>
              <CheckBadge status={recap.check_status} />
              {(recap.persona !== (prefs.persona ?? "casual") || (recap.language ?? "en") !== (prefs.language ?? "en")) && (
                <span className="text-ink-400" aria-live="polite">
                  · Showing the {personaLabel(recap.persona)} {(recap.language ?? "en").toUpperCase()} recap while yours is written…
                </span>
              )}
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2">
              <span className="flex items-center gap-3">
                <KitStripe team={home} className="h-12 w-2" />
                <span className="font-display text-4xl font-semibold">{home?.name}</span>
              </span>
              <span className="font-display text-7xl font-bold leading-none">
                {state?.score_home ?? 0}–{state?.score_away ?? 0}
              </span>
              <span className="flex items-center gap-3">
                <span className="font-display text-4xl font-semibold">{away?.name}</span>
                <KitStripe team={away} className="h-12 w-2" />
              </span>
            </div>
            <p className="mt-6 max-w-[70ch] text-xl leading-relaxed text-ink-100">{recap.summary}</p>
            {onPlayHighlights && <PlayHighlights onClick={onPlayHighlights} />}
          </div>

          <div className="panel p-6">
            <WinProbChart feed={feed} height={240} />
          </div>

          <div className="panel p-6">
            <h2 className="font-display text-2xl font-semibold">Key moments</h2>
            <ol className="mt-4 flex flex-col">
              {key.map((m, i) => {
                const ins = m.moment_id ? feed.insights[m.moment_id] : undefined;
                const team = m.team_id === home?.team_id ? home : m.team_id === away?.team_id ? away : null;
                return (
                  <li key={m.moment_id} className={`grid grid-cols-[64px_1fr] gap-4 py-3 ${i ? "border-t border-ink-700" : ""}`}>
                    <span className="font-display text-2xl font-semibold text-ink-300">{minuteLabel(m.clock_ms, m.period, feed.periodStarts)}</span>
                    <div>
                      <div className="flex items-center gap-2 text-sm">
                        <KitStripe team={team} className="h-3.5 w-1" />
                        <span className="text-ink-300">{momentLabel(prefs.language, m.type)}</span>
                        <span className="text-ink-500">· WP {Math.round(m.wp_before.home * 100)}% → {Math.round(m.wp_after.home * 100)}% {home?.short_name}</span>
                      </div>
                      <p className="font-display mt-1 text-xl font-semibold leading-snug">{ins?.variants[0]?.headline ?? m.trigger}</p>
                      {ins?.variants[0]?.body && <p className="mt-1 text-sm text-ink-300">{ins.variants[0].body}</p>}
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>
        </section>

        <aside className="flex flex-col gap-6">
          {potm && (
            <div className="panel p-6">
              <h2 className="text-sm text-ink-300">Player of the match</h2>
              <div className="mt-3 flex items-center gap-4">
                <span
                  className="font-display flex h-16 w-16 items-center justify-center rounded-md text-4xl font-bold"
                  style={{ background: potmTeam?.primary_colour, color: potmTeam?.secondary_colour }}
                >
                  {potm.shirt_number}
                </span>
                <div>
                  <div className="font-display text-3xl font-semibold leading-tight">{potm.name}</div>
                  <div className="text-sm text-ink-300">
                    {potmTeam?.name} · {potm.role}
                  </div>
                </div>
              </div>
              {potmStats && (
                <dl className="mt-5 grid grid-cols-3 gap-3">
                  {[
                    ["Rating", (potmStats.rating ?? 6).toFixed(1)],
                    ["Goals", String(potmStats.goals ?? 0)],
                    ["xG", (potmStats.xg ?? 0).toFixed(2)],
                    ["Passes", String(potmStats.passes_completed ?? 0)],
                    ["Accuracy", `${Math.round(as100(potmStats.pass_accuracy))}%`],
                    ["Top speed", `${Math.round(potmStats.top_speed_kmh ?? 0)}`],
                  ].map(([k, v]) => (
                    <div key={k} className="rounded-md bg-ink-850 p-2.5 ring-1 ring-ink-700">
                      <dd className="font-display text-2xl font-semibold leading-none">{v}</dd>
                      <dt className="mt-1 text-xs text-ink-400">{k === "Top speed" ? "Top speed km/h" : k}</dt>
                    </div>
                  ))}
                </dl>
              )}
            </div>
          )}

          {state && (
            <div className="panel p-6">
              <div className="mb-3 flex items-center justify-between text-sm font-semibold">
                <span style={{ color: c.home }}>{home?.short_name}</span>
                <h2 className="font-normal text-ink-300">Team stats</h2>
                <span style={{ color: c.away }}>{away?.short_name}</span>
              </div>
              <table className="w-full text-sm">
                <tbody>
                  {ROWS.map((r) => {
                    const h = r.get(state.home);
                    const a = r.get(state.away);
                    const f = r.fmt ?? ((v: number) => String(Math.round(v)));
                    const share = h + a > 0 ? h / (h + a) : 0.5;
                    return (
                      <tr key={r.label} className="border-t border-ink-800">
                        <td className="font-display w-16 py-2 text-lg font-semibold">{f(h)}</td>
                        <td className="py-2">
                          <div className="text-center text-xs text-ink-300">{r.label}</div>
                          <div className="mt-1 flex h-1.5 gap-0.5 overflow-hidden rounded-full">
                            <span style={{ width: `${share * 100}%`, background: c.home }} />
                            <span className="flex-1" style={{ background: c.away }} />
                          </div>
                        </td>
                        <td className="font-display w-16 py-2 text-right text-lg font-semibold">{f(a)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {(recap.social_posts?.length ?? 0) > 0 && <SharePosts posts={recap.social_posts ?? []} />}
        </aside>
      </div>
    </main>
  );
}

/** The recap's main call to action: the one-click highlights reel. */
function PlayHighlights({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group mt-6 inline-flex items-center gap-4 rounded-md bg-signal py-2.5 pl-3 pr-5 text-ink-950 shadow-lg shadow-signal/10 hover:bg-signal/90"
    >
      <span aria-hidden className="flex h-10 w-10 items-center justify-center rounded-full bg-ink-950 text-lg text-signal">
        ▶
      </span>
      <span className="text-left">
        <span className="font-display block text-2xl font-bold leading-none">Play your highlights</span>
        <span className="mt-1 block text-xs font-semibold text-ink-800">
          About a minute · replays, voice and your persona's take <kbd className="ml-1">H</kbd>
        </span>
      </span>
    </button>
  );
}

/** Ready-to-post share copy from the reporter (fact-checked like the recap), one copy button each. */
function SharePosts({ posts }: { posts: string[] }) {
  const [copied, setCopied] = useState<number | null>(null);
  const copy = async (text: string, n: number) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(n);
      window.setTimeout(() => setCopied((c) => (c === n ? null : c)), 1500);
    } catch {
      /* clipboard blocked: the text is still selectable */
    }
  };
  return (
    <div className="panel p-6">
      <h2 className="text-sm text-ink-300">Share the result</h2>
      <p className="mt-1 text-xs text-ink-400">Written by the reporter agent, fact-checked like everything else.</p>
      <ul className="mt-4 flex flex-col gap-3">
        {posts.map((post, n) => (
          <li key={n} className="rounded-md bg-ink-850 p-3 ring-1 ring-ink-700">
            <p className="text-sm leading-snug text-ink-100 select-all">{post}</p>
            <div className="mt-2 flex items-center justify-between text-[11px] text-ink-500">
              <span>{post.length} / 280</span>
              <button
                type="button"
                onClick={() => copy(post, n)}
                className="rounded-sm px-2 py-0.5 font-semibold text-signal ring-1 ring-signal/50 hover:bg-signal/10"
              >
                {copied === n ? "Copied" : "Copy"}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
