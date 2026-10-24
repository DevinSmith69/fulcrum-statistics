import { fixtureColours } from "../lib/format";
import type { MatchFeed } from "../lib/useMatchFeed";

/** Momentum (−100 … +100): the bar grows from the centre towards whichever side is on top. */
export function MomentumBar({ feed }: { feed: MatchFeed }) {
  const m = feed.state?.momentum ?? 0;
  const c = fixtureColours(feed.home, feed.away);
  const w = Math.min(50, Math.abs(m) / 2);
  const leader = m > 3 ? feed.home?.short_name : m < -3 ? feed.away?.short_name : null;
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between">
        <h3 className="text-sm font-medium text-ink-100">Momentum</h3>
        <span className="text-xs text-ink-300">{leader ? `${leader} on top · ${Math.round(Math.abs(m))}` : "Even"}</span>
      </div>
      <div className="relative h-3 overflow-hidden rounded-full bg-ink-800 ring-1 ring-ink-700" role="meter" aria-valuemin={-100} aria-valuemax={100} aria-valuenow={Math.round(m)} aria-label="Momentum">
        <span className="absolute inset-y-0 left-1/2 w-px bg-ink-500" />
        <span
          className="absolute inset-y-0.5 rounded-full transition-all duration-700"
          style={m >= 0 ? { right: "50%", width: `${w}%`, background: c.home } : { left: "50%", width: `${w}%`, background: c.away }}
        />
      </div>
      <div className="mt-1 flex justify-between text-xs text-ink-500">
        <span>{feed.home?.short_name}</span>
        <span>{feed.away?.short_name}</span>
      </div>
    </div>
  );
}

function Arc({ value, colour, label, team }: { value: number; colour: string; label: string; team: string }) {
  const v = Math.max(0, Math.min(100, value));
  const r = 30;
  const len = Math.PI * r;
  return (
    <div className="flex flex-col items-center">
      <svg viewBox="0 0 76 44" className="w-full max-w-[96px]" role="meter" aria-label={`${team} ${label}`} aria-valuenow={Math.round(v)} aria-valuemin={0} aria-valuemax={100}>
        <path d="M8 40 A30 30 0 0 1 68 40" fill="none" stroke="#1e2738" strokeWidth="6" strokeLinecap="round" />
        <path
          d="M8 40 A30 30 0 0 1 68 40"
          fill="none"
          stroke={colour}
          strokeWidth="6"
          strokeLinecap="round"
          strokeDasharray={`${(len * v) / 100} ${len}`}
          style={{ transition: "stroke-dasharray 700ms ease" }}
        />
        <text x="38" y="38" textAnchor="middle" className="font-display" fontSize="17" fontWeight="600" fill="#f3f6fa">
          {Math.round(v)}
        </text>
      </svg>
      <span className="-mt-0.5 text-xs text-ink-300">{team}</span>
    </div>
  );
}

/** Pressure and control indices (0–100) for both teams as paired arc gauges. */
export function Gauges({ feed }: { feed: MatchFeed }) {
  const c = fixtureColours(feed.home, feed.away);
  const s = feed.state;
  const groups = [
    { label: "Pressure", h: s?.home.pressure_index ?? 0, a: s?.away.pressure_index ?? 0 },
    { label: "Control", h: s?.home.control_index ?? 0, a: s?.away.control_index ?? 0 },
  ];
  return (
    <div className="grid grid-cols-2 gap-3">
      {groups.map((g) => (
        <div key={g.label} className="rounded-md bg-ink-850 p-2.5 ring-1 ring-ink-700">
          <h3 className="mb-1 text-sm font-medium text-ink-100">{g.label}</h3>
          <div className="grid grid-cols-2 gap-1">
            <Arc value={g.h} colour={c.home} label={g.label} team={feed.home?.short_name ?? "Home"} />
            <Arc value={g.a} colour={c.away} label={g.label} team={feed.away?.short_name ?? "Away"} />
          </div>
        </div>
      ))}
    </div>
  );
}
