import type { CSSProperties } from "react";
import { minuteLabel, teamInk } from "../../lib/format";
import { momentLabel } from "../../lib/i18n";
import type { MatchState, Moment, OverlayCue, OverlayStat, Team } from "../../types";
import { KitStripe } from "../TeamMark";

export interface CueContext {
  home: Team | null;
  away: Team | null;
  state: MatchState | null;
  moment: Moment | undefined;
  periodStarts: Record<string, number>;
  /** Scales the whole graphic down for split view. */
  size: "lg" | "md";
}

const glass = "bg-ink-950/92 ring-1 ring-white/8 shadow-[0_18px_48px_-18px_rgba(0,0,0,0.9)]";

function teamFor(cue: OverlayCue, ctx: CueContext) {
  if (!cue.team_id) return null;
  return ctx.home?.team_id === cue.team_id ? ctx.home : ctx.away?.team_id === cue.team_id ? ctx.away : null;
}

function Timer({ ms }: { ms: number }) {
  return (
    <span className="absolute inset-x-0 bottom-0 h-[3px] overflow-hidden rounded-b-[inherit]">
      <span className="cue-timer block h-full bg-signal/80" style={{ animationDuration: `${ms}ms` }} />
    </span>
  );
}

function Stat({ s, big = false }: { s: OverlayStat; big?: boolean }) {
  return (
    <div className="min-w-0">
      <div className={`font-display font-semibold leading-none text-ink-50 ${big ? "text-4xl" : "text-2xl"}`}>
        {s.value}
        {s.unit && <span className="ml-0.5 text-[0.55em] font-medium text-ink-300">{s.unit}</span>}
      </div>
      <div className="mt-1 truncate text-xs text-ink-300">{s.label}</div>
    </div>
  );
}

function Kicker({ cue, ctx }: { cue: OverlayCue; ctx: CueContext }) {
  const m = ctx.moment;
  const minute = m ? minuteLabel(m.clock_ms, m.period, ctx.periodStarts) : null;
  return (
    <div className="flex items-center gap-2 text-sm text-ink-300">
      {minute && <span className="font-display rounded-sm bg-signal px-1.5 py-px text-base font-bold leading-tight text-ink-950">{minute}</span>}
      {m && <span className="font-medium text-ink-100">{momentLabel(cue.language, m.type)}</span>}
    </div>
  );
}

export function LowerThird({ cue, ctx }: { cue: OverlayCue; ctx: CueContext }) {
  const team = teamFor(cue, ctx);
  const lg = ctx.size === "lg";
  return (
    <div className={`relative flex overflow-hidden rounded-md ${glass} ${lg ? "w-[min(820px,74vw)]" : "w-[min(560px,90%)]"}`}>
      <KitStripe team={team} className="w-2 self-stretch rounded-none" />
      <div className={`min-w-0 flex-1 ${lg ? "px-6 py-4" : "px-4 py-3"}`}>
        <Kicker cue={cue} ctx={ctx} />
        <div className={`font-display mt-1.5 font-semibold leading-[1.05] text-ink-50 ${lg ? "text-[2.6rem]" : "text-2xl"}`}>{cue.headline}</div>
        {cue.body && <p className={`mt-2 max-w-[68ch] text-ink-100 ${lg ? "text-lg leading-snug" : "text-sm leading-snug"} line-clamp-2`}>{cue.body}</p>}
        {lg && (cue.stats?.length ?? 0) > 0 && (
          <div className="mt-3 flex gap-8 border-t border-white/8 pt-3">
            {cue.stats!.map((s) => (
              <Stat key={s.label} s={s} />
            ))}
          </div>
        )}
      </div>
      <Timer ms={cue.duration_ms} />
    </div>
  );
}

export function WpSwing({ cue, ctx }: { cue: OverlayCue; ctx: CueContext }) {
  const team = teamFor(cue, ctx);
  const [before, after] = cue.stats ?? [];
  const b = Number(before?.value ?? 0);
  const a = Number(after?.value ?? 0);
  const up = a >= b;
  const lg = ctx.size === "lg";
  return (
    <div className={`relative overflow-hidden rounded-md ${glass} ${lg ? "w-[340px] p-5" : "w-[250px] p-3.5"}`}>
      <div className="flex items-center gap-2 text-xs text-ink-300">
        <KitStripe team={team} className="h-3.5 w-1" />
        <span className="font-medium text-ink-100">{team?.name ?? "Match"}</span>
        <span className="ml-auto">Outcome probability</span>
      </div>
      <div className="mt-2 flex items-baseline gap-3">
        <span className={`font-display font-semibold text-ink-400 ${lg ? "text-3xl" : "text-2xl"}`}>{b}%</span>
        <span className="text-ink-500" aria-hidden>
          {up ? "▲" : "▼"}
        </span>
        <span className={`font-display font-bold leading-none text-signal ${lg ? "text-6xl" : "text-4xl"}`}>{a}%</span>
      </div>
      <div className="relative mt-3 h-2 overflow-hidden rounded-full bg-ink-700">
        <span className="absolute inset-y-0 left-0 rounded-full bg-ink-500" style={{ width: `${Math.min(a, b)}%` }} />
        <span
          className="absolute inset-y-0 left-0 origin-left rounded-full bg-signal"
          style={{ width: `${a}%`, animation: "bar-grow 900ms cubic-bezier(.2,.8,.2,1) both", ["--from" as string]: String(a ? b / a : 0) } as CSSProperties}
        />
      </div>
      {cue.body && lg && <p className="mt-3 text-sm leading-snug text-ink-100 line-clamp-2">{cue.body}</p>}
      <Timer ms={cue.duration_ms} />
    </div>
  );
}

export function StatCard({ cue, ctx }: { cue: OverlayCue; ctx: CueContext }) {
  const team = teamFor(cue, ctx);
  const lg = ctx.size === "lg";
  const stats = cue.stats ?? [];
  return (
    <div className={`relative overflow-hidden rounded-md ${glass} ${lg ? "w-[380px] p-5" : "w-[260px] p-3.5"}`}>
      <div className="flex items-center gap-2">
        <KitStripe team={team} className="h-4 w-1" />
        <Kicker cue={cue} ctx={ctx} />
      </div>
      <div className={`font-display mt-2 font-semibold leading-tight text-ink-50 ${lg ? "text-2xl" : "text-lg"}`}>{cue.headline}</div>
      {stats.length > 0 && (
        <div className={`mt-3 grid gap-x-5 gap-y-3 border-t border-white/8 pt-3 ${stats.length > 2 ? "grid-cols-2" : "grid-cols-2"}`}>
          {stats.slice(0, lg ? 4 : 2).map((s) => (
            <Stat key={s.label} s={s} big={lg && stats.length <= 2} />
          ))}
        </div>
      )}
      <Timer ms={cue.duration_ms} />
    </div>
  );
}

export function PlayerTag({ cue, ctx }: { cue: OverlayCue; ctx: CueContext }) {
  const team = teamFor(cue, ctx);
  const [num, ...rest] = cue.headline.split(" ");
  return (
    <div className={`relative flex items-center overflow-hidden rounded-md ${glass}`}>
      <span
        className="font-display flex h-full min-h-12 items-center px-3 text-2xl font-bold"
        style={{ background: team?.primary_colour ?? "#1e2738", color: team?.secondary_colour ?? "#fff" }}
      >
        {num}
      </span>
      <div className="px-3.5 py-2">
        <div className="font-display text-xl font-semibold leading-none text-ink-50">{rest.join(" ")}</div>
        <div className="mt-1 flex gap-2 text-xs text-ink-300">
          <span className="text-signal">{cue.body}</span>
          {cue.stats?.map((s) => (
            <span key={s.label}>
              {s.label} {s.value}
            </span>
          ))}
        </div>
      </div>
      <Timer ms={cue.duration_ms} />
    </div>
  );
}

export function PressureBar({ cue, ctx }: { cue: OverlayCue; ctx: CueContext }) {
  const h = ctx.state?.home.pressure_index ?? 50;
  const a = ctx.state?.away.pressure_index ?? 50;
  const total = h + a || 1;
  const lg = ctx.size === "lg";
  return (
    <div className={`relative overflow-hidden rounded-md ${glass} ${lg ? "w-[400px] p-5" : "w-[270px] p-3.5"}`}>
      <Kicker cue={cue} ctx={ctx} />
      <div className={`font-display mt-1.5 font-semibold leading-tight text-ink-50 ${lg ? "text-2xl" : "text-lg"}`}>{cue.headline}</div>
      <div className="mt-3 flex items-center justify-between text-xs text-ink-300">
        <span className="font-medium" style={{ color: teamInk(ctx.home) }}>
          {ctx.home?.short_name} {Math.round(h)}
        </span>
        <span>Pressure index</span>
        <span className="font-medium" style={{ color: teamInk(ctx.away) }}>
          {Math.round(a)} {ctx.away?.short_name}
        </span>
      </div>
      <div className="mt-1.5 flex h-2.5 gap-0.5 overflow-hidden rounded-full">
        <span className="h-full transition-[width] duration-700" style={{ width: `${(100 * h) / total}%`, background: teamInk(ctx.home) }} />
        <span className="h-full flex-1" style={{ background: teamInk(ctx.away) }} />
      </div>
      <Timer ms={cue.duration_ms} />
    </div>
  );
}

export function RecapCard({ cue, ctx }: { cue: OverlayCue; ctx: CueContext }) {
  const lg = ctx.size === "lg";
  return (
    <div className={`relative overflow-hidden rounded-md ${glass} ${lg ? "w-[min(760px,74vw)] p-7" : "w-[min(520px,92%)] p-4"}`}>
      <div className="flex items-center justify-center gap-5">
        <KitStripe team={ctx.home} className="h-10 w-1.5" />
        <span className={`font-display font-semibold ${lg ? "text-3xl" : "text-xl"}`}>{ctx.home?.short_name}</span>
        <span className={`font-display font-bold ${lg ? "text-6xl" : "text-4xl"}`}>
          {ctx.state?.score_home ?? 0}–{ctx.state?.score_away ?? 0}
        </span>
        <span className={`font-display font-semibold ${lg ? "text-3xl" : "text-xl"}`}>{ctx.away?.short_name}</span>
        <KitStripe team={ctx.away} className="h-10 w-1.5" />
      </div>
      <div className={`font-display mt-3 text-center font-semibold text-signal ${lg ? "text-2xl" : "text-lg"}`}>{cue.headline}</div>
      {cue.body && <p className={`mx-auto mt-2 max-w-[60ch] text-center text-ink-100 ${lg ? "text-base" : "text-sm"}`}>{cue.body}</p>}
      <Timer ms={cue.duration_ms} />
    </div>
  );
}

export const TEMPLATES = {
  lower_third: LowerThird,
  wp_swing: WpSwing,
  stat_card: StatCard,
  player_tag: PlayerTag,
  pressure_bar: PressureBar,
  recap_card: RecapCard,
} as const;
