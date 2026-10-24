import type { Team } from "../types";

/** Team identity without a crest: a two-colour kit stripe and the short name. */
export function KitStripe({ team, className = "h-8 w-1.5" }: { team: Team | null; className?: string }) {
  if (!team) return <span className={`${className} rounded-sm bg-ink-600`} />;
  return (
    <span
      aria-hidden
      className={`${className} shrink-0 rounded-sm`}
      style={{ background: `linear-gradient(${team.primary_colour} 0 62%, ${team.secondary_colour} 62% 100%)` }}
    />
  );
}

export function TeamName({ team, short = false, className = "" }: { team: Team | null; short?: boolean; className?: string }) {
  return <span className={className}>{team ? (short ? team.short_name : team.name) : "—"}</span>;
}
