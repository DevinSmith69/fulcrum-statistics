import type { MatchEvent, Player, Side, Team } from "../types";

export const PITCH_L = 105;
export const PITCH_W = 68;

/** Team-frame 4-3-3 anchors (attacking towards x = 105; right side = high y), as in MODEL_SPEC §1.8. */
const ANCHORS: Record<string, [number, number]> = {
  GK: [5, 34], RB: [34, 60], RCB: [24, 44], LCB: [24, 24], LB: [34, 8], CDM: [40, 34],
  RCM: [52, 48], LCM: [52, 20], RW: [68, 60], ST: [72, 34], LW: [68, 8],
};

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** Event types whose team is the team in possession afterwards. */
const ON_BALL = new Set<MatchEvent["type"]>([
  "pass", "carry", "dribble", "shot", "kick_off", "possession_change", "corner", "free_kick", "throw_in", "goal_kick",
  "interception", "ball_recovery",
]);

/** Does this side attack towards x = 105 in absolute coordinates? (home does in period 1) */
export const attacksRight = (side: Side, period: number) => (side === "home") === (period % 2 === 1);

/** Team-relative event coordinates → absolute pitch coordinates. */
export function toAbs(side: Side, period: number, x: number, y: number): [number, number] {
  return attacksRight(side, period) ? [x, y] : [PITCH_L - x, PITCH_W - y];
}

/** Absolute → team frame (the transform is its own inverse). */
export const toTeam = toAbs;

export interface Dot {
  id: string;
  side: Side;
  number: number;
  name: string;
  x: number;
  y: number;
}

export interface Trace {
  kind: "pass" | "shot" | "goal" | "carry";
  side: Side;
  x: number;
  y: number;
  ex: number;
  ey: number;
  ok: boolean;
  at: number; // performance.now() when added
}

/**
 * Mutable, render-free model of what the pitch canvas draws. Fed from the event stream; the canvas reads it
 * every animation frame and eases dots towards these targets.
 */
export class PitchModel {
  home: Team | null = null;
  away: Team | null = null;
  lineup: Record<Side, Map<string, Player>> = { home: new Map(), away: new Map() };
  ball: [number, number] = [52.5, 34];
  period = 1;
  actor: { id: string; x: number; y: number } | null = null;
  receiver: { id: string; x: number; y: number } | null = null;
  traces: Trace[] = [];
  lastSeq = -1;
  version = 0;
  /** Side in possession (from the last on-ball event); the other side drops into a defensive block. */
  possession: Side | null = null;

  reset(home: Team, away: Team) {
    this.home = home;
    this.away = away;
    for (const [side, team] of [["home", home], ["away", away]] as const) {
      const m = new Map<string, Player>();
      for (const p of team.players.slice(0, 11)) m.set(p.role, p);
      this.lineup[side] = m;
    }
    this.ball = [52.5, 34];
    this.actor = this.receiver = null;
    this.traces = [];
    this.lastSeq = -1;
    this.version++;
  }

  private side(teamId: string | null | undefined): Side | null {
    if (!teamId) return null;
    if (this.home?.team_id === teamId) return "home";
    if (this.away?.team_id === teamId) return "away";
    return null;
  }

  private player(side: Side, id: string) {
    return (side === "home" ? this.home : this.away)?.players.find((p) => p.player_id === id);
  }

  /** Apply one event (idempotent on seq). `animate` = false when replaying history after a hello. */
  apply(ev: MatchEvent, animate = true) {
    // History replayed after a hello may overlap live events: older events still update line-ups.
    const stale = ev.seq <= this.lastSeq;
    if (stale && animate) return;
    if (!stale) {
      this.lastSeq = ev.seq;
      this.period = ev.period;
    }
    const side = this.side(ev.team_id);
    if (!stale && (ev.type === "period_start" || ev.type === "kick_off")) this.ball = [52.5, 34];
    if (!side) return;
    const lineup = this.lineup[side];

    if (ev.type === "substitution" && ev.substitution) {
      for (const [role, p] of lineup) {
        if (p.player_id === ev.substitution.player_off_id) {
          const on = this.player(side, ev.substitution.player_on_id);
          if (on) lineup.set(role, on);
        }
      }
      this.version++;
      return;
    }
    if (ev.type === "card" && ev.outcome === "red") {
      for (const [role, p] of lineup) if (p.player_id === ev.player_id) lineup.delete(role);
      this.version++;
      return;
    }
    if (stale || ev.x == null || ev.y == null) return;
    if (ON_BALL.has(ev.type)) this.possession = side;
    const [x, y] = toAbs(side, ev.period, ev.x, ev.y);
    const end = ev.end_x != null && ev.end_y != null ? toAbs(side, ev.period, ev.end_x, ev.end_y) : null;
    if (ev.player_id) this.actor = { id: ev.player_id, x, y };
    this.receiver = null;
    if (ev.type === "pass" && end) {
      const ok = ev.outcome === "success";
      if (ok && ev.pass?.recipient_id) this.receiver = { id: ev.pass.recipient_id, x: end[0], y: end[1] };
      this.ball = ok ? end : [x + (end[0] - x) * 0.6, y + (end[1] - y) * 0.6];
      if (animate) this.trace({ kind: "pass", side, x, y, ex: end[0], ey: end[1], ok });
    } else if (ev.type === "shot" && end) {
      this.ball = end;
      if (animate) this.trace({ kind: ev.outcome === "goal" ? "goal" : "shot", side, x, y, ex: end[0], ey: end[1], ok: ev.outcome === "goal" });
    } else if ((ev.type === "carry" || ev.type === "dribble") && end) {
      this.ball = end;
      this.actor = { id: ev.player_id ?? "", x: end[0], y: end[1] };
      if (animate && ev.type === "carry") this.trace({ kind: "carry", side, x, y, ex: end[0], ey: end[1], ok: true });
    } else if (ev.type === "goal_kick") {
      this.ball = [x, y];
    } else {
      this.ball = [x, y];
    }
  }

  private trace(t: Omit<Trace, "at">) {
    this.traces.push({ ...t, at: performance.now() });
    if (this.traces.length > 12) this.traces.shift();
  }

  /** Target positions for every on-pitch player given the current ball. */
  targets(): Dot[] {
    const out: Dot[] = [];
    for (const side of ["home", "away"] as const) {
      const [bx, by] = toTeam(side, this.period, this.ball[0], this.ball[1]);
      for (const [role, p] of this.lineup[side]) {
        const [ax, ay] = ANCHORS[role] ?? [50, 34];
        let tx: number, ty: number;
        if (role === "GK") {
          tx = clamp(ax + 0.08 * (bx - 52.5), 2, 16);
          ty = clamp(ay + 0.15 * (by - 34), 26, 42);
        } else {
          tx = ax + 0.45 * (bx - 52.5);
          ty = ay + 0.3 * (by - 34);
          if (this.possession && this.possession !== side) {
            // Out of possession: drop deeper and narrower, goal-side of the ball.
            tx -= 9;
            ty = 34 + (ty - 34) * 0.78;
          }
          tx = clamp(tx, 3, 102);
          ty = clamp(ty, 3, 65);
        }
        let [x, y] = toAbs(side, this.period, tx, ty);
        if (this.actor?.id === p.player_id) [x, y] = [this.actor.x, this.actor.y];
        else if (this.receiver?.id === p.player_id) [x, y] = [this.receiver.x, this.receiver.y];
        out.push({ id: p.player_id, side, number: p.shirt_number, name: p.short_name, x, y });
      }
    }
    return out;
  }
}
