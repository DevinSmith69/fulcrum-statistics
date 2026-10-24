import type { Language, MomentType } from "../types";

/** Small UI vocabulary for the parts of the overlay that sit next to localised insight copy. */
const UI: Record<Language, Record<string, string>> = {
  en: { analysing: "Analysing moment", waiting: "Waiting for the first moment" },
  es: { analysing: "Analizando la jugada", waiting: "Esperando la primera jugada" },
  fr: { analysing: "Analyse en cours", waiting: "En attente du premier temps fort" },
  pt: { analysing: "Analisando o lance", waiting: "Aguardando o primeiro lance" },
  de: { analysing: "Szene wird analysiert", waiting: "Warten auf die erste Szene" },
};

const MOMENT: Record<Language, Record<MomentType, string>> = {
  en: {
    goal: "Goal", big_chance: "Big chance", wp_swing: "Probability swing", pressure_shift: "Pressure shift",
    phase_change: "Phase change", tempo_change: "Tempo change", red_card: "Red card", speed_record: "Speed record",
    elite_pass: "Elite pass", player_milestone: "Milestone", momentum_run: "Momentum run", half_time: "Half-time",
    full_time: "Full time",
  },
  es: {
    goal: "Gol", big_chance: "Ocasión clara", wp_swing: "Giro de probabilidad", pressure_shift: "Cambio de presión",
    phase_change: "Cambio de fase", tempo_change: "Cambio de ritmo", red_card: "Tarjeta roja",
    speed_record: "Récord de velocidad", elite_pass: "Pase de élite", player_milestone: "Hito", momentum_run: "Racha",
    half_time: "Descanso", full_time: "Final",
  },
  fr: {
    goal: "But", big_chance: "Grosse occasion", wp_swing: "Bascule", pressure_shift: "Pression",
    phase_change: "Changement de phase", tempo_change: "Rythme", red_card: "Carton rouge",
    speed_record: "Record de vitesse", elite_pass: "Passe d’élite", player_milestone: "Cap franchi",
    momentum_run: "Temps fort", half_time: "Mi-temps", full_time: "Fin du match",
  },
  pt: {
    goal: "Gol", big_chance: "Grande chance", wp_swing: "Virada de probabilidade", pressure_shift: "Pressão",
    phase_change: "Mudança de fase", tempo_change: "Ritmo", red_card: "Cartão vermelho",
    speed_record: "Recorde de velocidade", elite_pass: "Passe de elite", player_milestone: "Marco",
    momentum_run: "Sequência", half_time: "Intervalo", full_time: "Fim de jogo",
  },
  de: {
    goal: "Tor", big_chance: "Großchance", wp_swing: "Wahrscheinlichkeit kippt", pressure_shift: "Druckwechsel",
    phase_change: "Phasenwechsel", tempo_change: "Tempowechsel", red_card: "Rote Karte", speed_record: "Tempo-Rekord",
    elite_pass: "Elite-Pass", player_milestone: "Meilenstein", momentum_run: "Drangphase", half_time: "Halbzeit",
    full_time: "Abpfiff",
  },
};

const asLang = (l: string | undefined): Language => (l && l in UI ? (l as Language) : "en");

export const t = (lang: string | undefined, key: string) => UI[asLang(lang)][key] ?? UI.en[key] ?? key;
export const momentLabel = (lang: string | undefined, type: MomentType) => MOMENT[asLang(lang)][type] ?? type;

export const LANGUAGES: { code: Language; label: string }[] = [
  { code: "en", label: "English" },
  { code: "es", label: "Español" },
  { code: "fr", label: "Français" },
  { code: "pt", label: "Português" },
  { code: "de", label: "Deutsch" },
];

export const PERSONAS = [
  { id: "analyst", label: "Analyst", blurb: "Numbers first, tactical language" },
  { id: "casual", label: "Casual", blurb: "Plain words, the story of the match" },
  { id: "club_fan", label: "Club fan", blurb: "Told from your club’s side" },
  { id: "player_lens", label: "Player lens", blurb: "Everything through one player" },
] as const;

export const personaLabel = (p: string | undefined) => PERSONAS.find((x) => x.id === p)?.label ?? "Casual";
