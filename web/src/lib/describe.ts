/**
 * Audio description: short, deterministic, localised lines for the key events of the match (no LLM), for blind and
 * low-vision fans. Spoken through the same queue as the co-commentary and announced in Match Studio's live region.
 * Pure — unit-tested in tests/describe.test.ts.
 */
import type { MatchEvent, MomentType, Team } from "../types/generated";
// Same values as PRIORITY in speechQueue.ts (kept local: node --test needs import-free modules here).
const PRIORITY = { low: 1, normal: 2, high: 3 } as const;

export interface Described {
  id: string;
  text: string;
  priority: number;
  /** Announce assertively (interrupting the screen reader): goals and red cards. */
  assertive: boolean;
}

type ShotEnd = "saved" | "off_target" | "blocked" | "woodwork";

interface Phrases {
  score: (home: string, h: number, away: string, a: number) => string;
  kickOff: (home: string, away: string) => string;
  secondHalf: (score: string) => string;
  extraTime: () => string;
  halfTime: (score: string) => string;
  fullTime: (score: string) => string;
  goal: (player: string, team: string, score: string) => string;
  yellow: (player: string, team: string) => string;
  red: (player: string, team: string) => string;
  bigChance: (team: string, player: string, end: ShotEnd) => string;
  sub: (team: string, on: string, off: string) => string;
}

const score = (home: string, h: number, away: string, a: number) => `${home} ${h}, ${away} ${a}`;

const PHRASES: Record<string, Phrases> = {
  en: {
    score,
    kickOff: (h, a) => `Kick-off. ${h} against ${a}.`,
    secondHalf: (s) => `The second half is under way. ${s}.`,
    extraTime: () => "Extra time is under way.",
    halfTime: (s) => `Half-time. ${s}.`,
    fullTime: (s) => `Full time. ${s}.`,
    goal: (p, t, s) => `Goal! ${p} scores for ${t}. ${s}.`,
    yellow: (p, t) => `Yellow card for ${p}, ${t}.`,
    red: (p, t) => `Red card! ${p} of ${t} is sent off.`,
    bigChance: (t, p, e) =>
      `Big chance for ${t}! ${p}'s shot ${{ saved: "is saved", off_target: "goes wide", blocked: "is blocked", woodwork: "hits the woodwork" }[e]}.`,
    sub: (t, on, off) => `Substitution for ${t}: ${on} replaces ${off}.`,
  },
  es: {
    score,
    kickOff: (h, a) => `Comienza el partido: ${h} contra ${a}.`,
    secondHalf: (s) => `Arranca la segunda parte. ${s}.`,
    extraTime: () => "Comienza la prórroga.",
    halfTime: (s) => `Descanso. ${s}.`,
    fullTime: (s) => `Final del partido. ${s}.`,
    goal: (p, t, s) => `¡Gol! Marca ${p} para ${t}. ${s}.`,
    yellow: (p, t) => `Tarjeta amarilla para ${p}, de ${t}.`,
    red: (p, t) => `¡Tarjeta roja! Expulsión de ${p}, de ${t}.`,
    bigChance: (t, p, e) =>
      `¡Ocasión clara para ${t}! El disparo de ${p} ${{ saved: "lo para el portero", off_target: "se va fuera", blocked: "es bloqueado", woodwork: "da en el palo" }[e]}.`,
    sub: (t, on, off) => `Cambio en ${t}: entra ${on} por ${off}.`,
  },
  fr: {
    score,
    kickOff: (h, a) => `Coup d’envoi : ${h} contre ${a}.`,
    secondHalf: (s) => `La seconde période est lancée. ${s}.`,
    extraTime: () => "Début de la prolongation.",
    halfTime: (s) => `Mi-temps. ${s}.`,
    fullTime: (s) => `Fin du match. ${s}.`,
    goal: (p, t, s) => `But ! ${p} marque pour ${t}. ${s}.`,
    yellow: (p, t) => `Carton jaune pour ${p}, de ${t}.`,
    red: (p, t) => `Carton rouge ! Exclusion de ${p}, de ${t}.`,
    bigChance: (t, p, e) =>
      `Grosse occasion pour ${t} ! La frappe de ${p} ${{ saved: "est arrêtée", off_target: "passe à côté", blocked: "est contrée", woodwork: "touche le poteau" }[e]}.`,
    sub: (t, on, off) => `Changement pour ${t} : ${on} remplace ${off}.`,
  },
  pt: {
    score,
    kickOff: (h, a) => `Começa o jogo: ${h} contra ${a}.`,
    secondHalf: (s) => `Começa o segundo tempo. ${s}.`,
    extraTime: () => "Começa a prorrogação.",
    halfTime: (s) => `Intervalo. ${s}.`,
    fullTime: (s) => `Fim de jogo. ${s}.`,
    goal: (p, t, s) => `Gol! ${p} marca para ${t}. ${s}.`,
    yellow: (p, t) => `Cartão amarelo para ${p}, ${t}.`,
    red: (p, t) => `Cartão vermelho! Expulsão de ${p}, ${t}.`,
    bigChance: (t, p, e) =>
      `Grande chance para ${t}! O chute de ${p} ${{ saved: "é defendido", off_target: "vai para fora", blocked: "é bloqueado", woodwork: "acerta a trave" }[e]}.`,
    sub: (t, on, off) => `Substituição em ${t}: entra ${on}, sai ${off}.`,
  },
  de: {
    score,
    kickOff: (h, a) => `Anpfiff: ${h} gegen ${a}.`,
    secondHalf: (s) => `Die zweite Halbzeit läuft. ${s}.`,
    extraTime: () => "Die Verlängerung beginnt.",
    halfTime: (s) => `Halbzeit. ${s}.`,
    fullTime: (s) => `Abpfiff. ${s}.`,
    goal: (p, t, s) => `Tor! ${p} trifft für ${t}. ${s}.`,
    yellow: (p, t) => `Gelbe Karte für ${p}, ${t}.`,
    red: (p, t) => `Rote Karte! Platzverweis für ${p}, ${t}.`,
    bigChance: (t, p, e) =>
      `Großchance für ${t}! Der Schuss von ${p} ${{ saved: "wird gehalten", off_target: "geht daneben", blocked: "wird geblockt", woodwork: "trifft den Pfosten" }[e]}.`,
    sub: (t, on, off) => `Wechsel bei ${t}: ${on} kommt für ${off}.`,
  },
};

export interface DescribeContext {
  home: Team | null;
  away: Team | null;
  lang: string;
}

const SHOT_ENDS: readonly string[] = ["saved", "off_target", "blocked", "woodwork"];

/** One spoken line for a key event, or null for events that aren't described (passes, tackles, …). */
export function describeEvent(ev: MatchEvent, ctx: DescribeContext): Described | null {
  const P = PHRASES[ctx.lang] ?? PHRASES.en;
  const teams = [ctx.home, ctx.away].filter((t): t is Team => !!t);
  const team = teams.find((t) => t.team_id === ev.team_id) ?? null;
  const teamName = team?.name ?? ev.team_id ?? "";
  const playerName = (id: string | null | undefined) => {
    if (!id) return teamName;
    for (const t of teams) {
      const p = t.players.find((x) => x.player_id === id);
      if (p) return p.short_name || p.name;
    }
    return teamName;
  };
  const sc = P.score(ctx.home?.name ?? "", ev.score_home ?? 0, ctx.away?.name ?? "", ev.score_away ?? 0);
  const id = ev.event_id ?? `${ev.match_id}:${ev.seq}`;
  const line = (text: string, priority: number, assertive = false): Described => ({ id, text, priority, assertive });

  switch (ev.type) {
    case "period_start":
      if (ev.period === 1) return line(P.kickOff(ctx.home?.name ?? "", ctx.away?.name ?? ""), PRIORITY.normal);
      if (ev.period === 2) return line(P.secondHalf(sc), PRIORITY.normal);
      return line(P.extraTime(), PRIORITY.normal);
    case "period_end":
      return ev.period === 1 ? line(P.halfTime(sc), PRIORITY.normal) : null; // full time comes with match_end
    case "match_end":
      return line(P.fullTime(sc), PRIORITY.high);
    case "goal":
      return line(P.goal(playerName(ev.player_id), teamName, sc), PRIORITY.high, true);
    case "card": {
      const colour = ev.card?.colour ?? (ev.outcome === "red" ? "red" : "yellow");
      return colour === "yellow"
        ? line(P.yellow(playerName(ev.player_id), teamName), PRIORITY.normal)
        : line(P.red(playerName(ev.player_id), teamName), PRIORITY.high, true);
    }
    case "shot": {
      const end = ev.outcome ?? "";
      if (!ev.shot?.big_chance || !SHOT_ENDS.includes(end)) return null; // goals are described by the goal event
      return line(P.bigChance(teamName, playerName(ev.player_id), end as ShotEnd), PRIORITY.normal);
    }
    case "substitution": {
      const s = ev.substitution;
      if (!s) return null;
      return line(P.sub(teamName, playerName(s.player_on_id), playerName(s.player_off_id)), PRIORITY.low);
    }
    default:
      return null;
  }
}

/** Speech priority of an insight's commentary line, from its moment type. */
export function momentPriority(type: MomentType | undefined): number {
  if (type === "goal" || type === "red_card") return PRIORITY.high;
  if (type === "big_chance" || type === "wp_swing" || type === "half_time" || type === "full_time") return PRIORITY.normal;
  return PRIORITY.low;
}
