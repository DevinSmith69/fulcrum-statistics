// Coach's view (GET /api/matches/{id}/insights/{insight_id}/coach, API_CONTRACT §2): pure helpers for the panel,
// tested in tests/coach.test.ts. The engine computes the evidence (coach_ facts, MODEL_SPEC §4.1); the Coach agent
// only explains it, and every number in its copy is fact-checked server-side.
import type { CheckStatus, Fact, Language, Moment } from "../types";

/** One point of the view: a sentence and the coach_ fact keys it states. */
export interface CoachPoint {
  text: string;
  fact_keys: string[];
}

/** `CoachView` (fulcrum.agents.schemas). An API response, not a bus contract, so it is typed by hand here. */
export interface CoachView {
  match_id: string;
  insight_id: string;
  moment_id: string;
  moment_type: string;
  defending_team_id: string | null;
  language: string;
  check_status: CheckStatus;
  points: CoachPoint[];
  takeaway: string | null;
  facts: Fact[];
  latency_ms: number | null;
}

export const COACH_PREFIX = "coach_";
const COACH_TYPES = new Set(["goal", "big_chance"]);

/** The moment's coach_ facts, in engine order. */
export const coachFacts = (facts: readonly Fact[] | null | undefined): Fact[] =>
  (facts ?? []).filter((f) => f.key.startsWith(COACH_PREFIX));

/** True for goal / big-chance moments that carry coach_ facts (the only ones the endpoint serves). */
export function isCoachable(moment: Pick<Moment, "type" | "facts">): boolean {
  return COACH_TYPES.has(String(moment.type)) && coachFacts(moment.facts).length > 0;
}

/** Path of the endpoint (relative to API_URL). */
export function coachPath(matchId: string, insightId: string, language: string): string {
  const q = new URLSearchParams({ language });
  return `/api/matches/${encodeURIComponent(matchId)}/insights/${encodeURIComponent(insightId)}/coach?${q}`;
}

/** "0.11 xG", "31%", "46 s", "\"misplaced pass\"" → value as shown (same rules as factTrail.formatFactValue). */
export function coachFactValue(fact: Fact): string {
  const v = String(fact.value);
  if (!fact.unit) return v;
  return fact.unit === "%" || fact.unit === "'" ? `${v}${fact.unit}` : `${v} ${fact.unit}`;
}

/** One bullet of the deterministic fallback: "Expected completion of the misplaced pass: 31%". */
export const coachFactLine = (fact: Fact): string => `${fact.label}: ${coachFactValue(fact)}`;

/** The facts the view's points cite, in engine order, without duplicates ("based on"). The defending team is
 * implied by the panel's heading, so it is listed only if a point cites it. */
export function citedFacts(view: Pick<CoachView, "points" | "facts">): Fact[] {
  const keys = new Set(view.points.flatMap((p) => p.fact_keys));
  return view.facts.filter((f) => keys.has(f.key));
}

/** True when the view carries checked copy (otherwise the panel lists the facts). */
export const hasCopy = (view: Pick<CoachView, "points" | "check_status">): boolean =>
  view.check_status !== "fallback" && view.points.length > 0;

const COPY: Record<Language, Record<string, string>> = {
  en: {
    button: "Coach's view", title: "Coach's view", loading: "Reading the evidence", basedOn: "Based on",
    fallback: "No checked analysis right now. The engine's evidence:", failed: "Couldn't load the coach's view.",
    retry: "Retry",
  },
  es: {
    button: "Visión del entrenador", title: "Visión del entrenador", loading: "Leyendo los datos", basedOn: "Basado en",
    fallback: "Sin análisis verificado ahora mismo. Los datos del motor:", failed: "No se pudo cargar la visión del entrenador.",
    retry: "Reintentar",
  },
  fr: {
    button: "L'œil du coach", title: "L'œil du coach", loading: "Lecture des données", basedOn: "D'après",
    fallback: "Pas d'analyse vérifiée pour l'instant. Les données du moteur :", failed: "Impossible de charger l'œil du coach.",
    retry: "Réessayer",
  },
  pt: {
    button: "Visão do treinador", title: "Visão do treinador", loading: "Lendo os dados", basedOn: "Com base em",
    fallback: "Sem análise verificada agora. Os dados do motor:", failed: "Não foi possível carregar a visão do treinador.",
    retry: "Tentar de novo",
  },
  de: {
    button: "Trainerblick", title: "Trainerblick", loading: "Daten werden gelesen", basedOn: "Basierend auf",
    fallback: "Gerade keine geprüfte Analyse. Die Daten der Engine:", failed: "Der Trainerblick konnte nicht geladen werden.",
    retry: "Erneut versuchen",
  },
};

const asLang = (l: string | null | undefined): Language => {
  const base = (l ?? "en").split(/[-_]/)[0].toLowerCase();
  return (base in COPY ? base : "en") as Language;
};

/** Panel copy in the viewer's language (English fallback). Templates never contain digits. */
export const coachCopy = (lang: string | null | undefined, key: string): string => COPY[asLang(lang)][key] ?? COPY.en[key] ?? key;
