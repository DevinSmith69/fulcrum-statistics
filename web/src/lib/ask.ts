import type { Language } from "../types";

/** "Ask the studio": pure helpers for the panel (tested in tests/ask.test.ts). */

export const MAX_QUESTION = 300;

const SUGGESTIONS: Record<Language, string[]> = {
  en: ["Who's on top right now?", "Who has been the best passer so far?", "What was the biggest turning point?"],
  es: ["¿Quién domina ahora mismo?", "¿Quién ha sido el mejor pasador hasta ahora?", "¿Cuál fue el gran punto de inflexión?"],
  fr: ["Qui domine en ce moment ?", "Qui est le meilleur passeur jusqu'ici ?", "Quel a été le grand tournant du match ?"],
  pt: ["Quem está por cima agora?", "Quem foi o melhor passador até agora?", "Qual foi a grande virada do jogo?"],
  de: ["Wer ist gerade überlegen?", "Wer war bisher der beste Passgeber?", "Was war der große Wendepunkt?"],
};

const COPY: Record<Language, Record<string, string>> = {
  en: {
    title: "Ask the studio", placeholder: "Ask about this match…", ask: "Ask", thinking: "Checking the match data",
    cited: "Based on", unavailable: "The studio's agents aren't running right now. Try again in a moment.",
    busy: "Too many questions at once. Wait a few seconds and ask again.", failed: "Couldn't reach the studio. Try again.",
    declined: "Outside what the match data can answer",
  },
  es: {
    title: "Pregunta al estudio", placeholder: "Pregunta sobre este partido…", ask: "Preguntar",
    thinking: "Consultando los datos del partido", cited: "Basado en",
    unavailable: "Los agentes del estudio no están activos ahora mismo. Inténtalo de nuevo en un momento.",
    busy: "Demasiadas preguntas seguidas. Espera unos segundos y vuelve a preguntar.",
    failed: "No se pudo contactar con el estudio. Inténtalo de nuevo.", declined: "Fuera de lo que cubren los datos del partido",
  },
  fr: {
    title: "Demandez au studio", placeholder: "Posez une question sur ce match…", ask: "Demander",
    thinking: "Vérification des données du match", cited: "D'après",
    unavailable: "Les agents du studio ne sont pas actifs pour le moment. Réessayez dans un instant.",
    busy: "Trop de questions d'un coup. Patientez quelques secondes.", failed: "Impossible de joindre le studio. Réessayez.",
    declined: "Hors de ce que les données du match permettent de dire",
  },
  pt: {
    title: "Pergunte ao estúdio", placeholder: "Pergunte sobre esta partida…", ask: "Perguntar",
    thinking: "Consultando os dados da partida", cited: "Com base em",
    unavailable: "Os agentes do estúdio não estão ativos agora. Tente de novo em instantes.",
    busy: "Perguntas demais de uma vez. Espere alguns segundos.", failed: "Não foi possível falar com o estúdio. Tente de novo.",
    declined: "Fora do que os dados da partida respondem",
  },
  de: {
    title: "Frag das Studio", placeholder: "Frag etwas zu diesem Spiel…", ask: "Fragen", thinking: "Spieldaten werden geprüft",
    cited: "Basierend auf", unavailable: "Die Studio-Agenten laufen gerade nicht. Versuch es gleich noch einmal.",
    busy: "Zu viele Fragen auf einmal. Warte ein paar Sekunden.", failed: "Das Studio ist nicht erreichbar. Versuch es noch einmal.",
    declined: "Geht über die Spieldaten hinaus",
  },
};

const asLang = (l: string | undefined | null): Language => (l && l.split("-")[0] in SUGGESTIONS ? (l.split("-")[0] as Language) : "en");

/** Three suggested questions in the viewer's language. */
export const suggestedQuestions = (lang: string | undefined | null) => SUGGESTIONS[asLang(lang)];

/** Panel copy in the viewer's language (English fallback). */
export const askCopy = (lang: string | undefined | null, key: string) => COPY[asLang(lang)][key] ?? COPY.en[key] ?? key;

/** The question as it will be sent (whitespace collapsed), or null if it is empty or too long. */
export function cleanQuestion(raw: string): string | null {
  const q = raw.replace(/\s+/g, " ").trim();
  return q.length >= 2 && q.length <= MAX_QUESTION ? q : null;
}

/** A viewer-friendly message for a failed ask (`Error.message` is "<status> <detail>", see api.ts). */
export function askErrorMessage(message: string, lang: string | undefined | null): string {
  if (message.startsWith("503")) return askCopy(lang, "unavailable");
  if (message.startsWith("429")) return askCopy(lang, "busy");
  return askCopy(lang, "failed");
}
