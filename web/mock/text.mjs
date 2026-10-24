// Fulcrum mock "agent" copy: insight variants per persona × language, overlay cues and recaps.
//
// Stands in for WS3 (agents) until the real pipeline is up. Every number in the copy is taken from the
// moment's fact pack — the same rule the real agents follow (CLAUDE.md rule 3) — so the mock never shows
// a number the engine did not produce. Languages other than English use decimal commas.

export const LANGUAGES = ["en", "es", "fr", "pt", "de"];
export const PERSONAS = ["analyst", "casual", "club_fan", "player_lens"];

const fit = (s, n) => (s.length <= n ? s : `${s.slice(0, n - 1).replace(/\s+\S*$/, "")}…`);

function facts(moment, lang) {
  // Every type's template is built eagerly, so absent facts read as "" instead of throwing.
  const raw = new Proxy(Object.fromEntries(moment.facts.map((f) => [f.key, f.value])), {
    get: (t, k) => (k in t ? t[k] : ""),
  });
  const num = (k, d = null) => {
    const v = raw[k];
    if (typeof v !== "number") return String(v ?? "");
    const s = d == null ? String(v) : v.toFixed(d);
    return lang === "en" ? s : s.replace(".", ",");
  };
  return { raw, num };
}

const PHASE = {
  en: { control_home: "{home} in control", control_away: "{away} in control", chaos: "open, chaotic play", balanced: "an even contest" },
  es: { control_home: "dominio de {home}", control_away: "dominio de {away}", chaos: "un partido caótico", balanced: "un duelo igualado" },
  fr: { control_home: "{home} aux commandes", control_away: "{away} aux commandes", chaos: "un jeu chaotique", balanced: "un match équilibré" },
  pt: { control_home: "{home} no controle", control_away: "{away} no controle", chaos: "um jogo caótico", balanced: "um jogo equilibrado" },
  de: { control_home: "{home} hat die Kontrolle", control_away: "{away} hat die Kontrolle", chaos: "offenes, wildes Spiel", balanced: "ein ausgeglichenes Spiel" },
};
const OUTCOME = {
  en: { saved: "saved", "off target": "off target", blocked: "blocked", woodwork: "off the woodwork" },
  es: { saved: "parada", "off target": "fuera", blocked: "bloqueado", woodwork: "al palo" },
  fr: { saved: "arrêt", "off target": "non cadré", blocked: "contré", woodwork: "sur le poteau" },
  pt: { saved: "defesa", "off target": "para fora", blocked: "bloqueado", woodwork: "na trave" },
  de: { saved: "gehalten", "off target": "daneben", blocked: "geblockt", woodwork: "Aluminium" },
};

/**
 * Persona-neutral copy blocks for one moment in one language.
 * Returns { analyst: {h, b, c}, casual: {h, b, c} } — club_fan and player_lens are derived from casual.
 */
function copy(moment, lang, ctx) {
  const { raw, num } = facts(moment, lang);
  const team = ctx.teamName(moment.team_id) ?? "";
  const opp = ctx.opponentName(moment.team_id) ?? "";
  const k = moment.team_id === ctx.home.team_id ? "home" : "away";
  const m = raw.minute;
  const phase = (p) => (PHASE[lang][p] ?? p).replace("{home}", raw.home_team).replace("{away}", raw.away_team);
  const out = OUTCOME[lang][raw.outcome] ?? raw.outcome;
  const T = {
    goal: {
      en: {
        analyst: [`${raw.scorer} ${m}: ${num("xg", 2)} xG finish swings it to ${num("wp_scorer_after")}%`,
          `${team}'s win probability moves from ${num("wp_scorer_before")}% to ${num("wp_scorer_after")}% (${num("wp_scorer_delta")} pts). A ${num("xg", 2)} xG chance from ${num("distance_m", 1)} m, struck at ${num("shot_speed_kmh")} km/h.${raw.assist ? ` ${raw.assist}'s assist rated ${num("pass_difficulty_of_assist", 1)}/10 for difficulty.` : ""}`],
        casual: [`${raw.scorer} scores! ${raw.score_after}`,
          `${team} strike in the ${m} minute. Their chances of winning jump to ${num("wp_scorer_after")}%.${raw.assist ? ` Lovely work from ${raw.assist} to set it up.` : ""}`],
        line: `${raw.scorer}! ${team} lead the way now, ${raw.score_after}, and that changes everything.`,
      },
      es: {
        analyst: [`${raw.scorer} ${m}: remate de ${num("xg", 2)} xG, victoria al ${num("wp_scorer_after")}%`,
          `La probabilidad de victoria de ${team} pasa del ${num("wp_scorer_before")}% al ${num("wp_scorer_after")}% (${num("wp_scorer_delta")} pts). Ocasión de ${num("xg", 2)} xG desde ${num("distance_m", 1)} m, a ${num("shot_speed_kmh")} km/h.${raw.assist ? ` La asistencia de ${raw.assist}: dificultad ${num("pass_difficulty_of_assist", 1)}/10.` : ""}`],
        casual: [`¡Gol de ${raw.scorer}! ${raw.score_after}`,
          `${team} marca en el minuto ${m}. Sus opciones de ganar suben al ${num("wp_scorer_after")}%.${raw.assist ? ` Gran pase de ${raw.assist}.` : ""}`],
        line: `¡${raw.scorer}! ${team} golpea, ${raw.score_after}, y el partido cambia por completo.`,
      },
      fr: {
        analyst: [`${raw.scorer} ${m} : finition à ${num("xg", 2)} xG, victoire à ${num("wp_scorer_after")} %`,
          `La probabilité de victoire de ${team} passe de ${num("wp_scorer_before")} % à ${num("wp_scorer_after")} % (${num("wp_scorer_delta")} pts). Occasion à ${num("xg", 2)} xG depuis ${num("distance_m", 1)} m, frappe à ${num("shot_speed_kmh")} km/h.`],
        casual: [`But de ${raw.scorer} ! ${raw.score_after}`,
          `${team} frappe à la ${m} minute. Ses chances de gagner grimpent à ${num("wp_scorer_after")} %.${raw.assist ? ` Superbe passe de ${raw.assist}.` : ""}`],
        line: `${raw.scorer} ! ${team} frappe, ${raw.score_after}, et tout bascule.`,
      },
      pt: {
        analyst: [`${raw.scorer} ${m}: finalização de ${num("xg", 2)} xG, vitória a ${num("wp_scorer_after")}%`,
          `A probabilidade de vitória do ${team} vai de ${num("wp_scorer_before")}% para ${num("wp_scorer_after")}% (${num("wp_scorer_delta")} pts). Chance de ${num("xg", 2)} xG a ${num("distance_m", 1)} m, chute a ${num("shot_speed_kmh")} km/h.`],
        casual: [`Gol de ${raw.scorer}! ${raw.score_after}`,
          `${team} marca aos ${m}. As chances de vitória sobem para ${num("wp_scorer_after")}%.${raw.assist ? ` Belo passe de ${raw.assist}.` : ""}`],
        line: `${raw.scorer}! ${team} marca, ${raw.score_after}, e o jogo muda de figura.`,
      },
      de: {
        analyst: [`${raw.scorer} ${m}: Abschluss mit ${num("xg", 2)} xG, Siegchance ${num("wp_scorer_after")} %`,
          `Die Siegwahrscheinlichkeit von ${team} steigt von ${num("wp_scorer_before")} % auf ${num("wp_scorer_after")} % (${num("wp_scorer_delta")} Pkt.). Chance mit ${num("xg", 2)} xG aus ${num("distance_m", 1)} m, ${num("shot_speed_kmh")} km/h.`],
        casual: [`Tor durch ${raw.scorer}! ${raw.score_after}`,
          `${team} trifft in der ${m} Minute. Die Siegchance klettert auf ${num("wp_scorer_after")} %.${raw.assist ? ` Starke Vorarbeit von ${raw.assist}.` : ""}`],
        line: `${raw.scorer}! ${team} trifft, ${raw.score_after}, und alles ist anders.`,
      },
    },
    big_chance: {
      en: {
        analyst: [`${num("xg", 2)} xG chance for ${raw.shooter}, ${out}`,
          `${raw.shooter} gets a ${num("xg", 2)} xG look from ${num("distance_m", 1)} m and it is ${out}. ${team} now sit on ${num("team_xg_total", 2)} xG for the match.`],
        casual: [`So close for ${raw.shooter}!`, `${raw.shooter} had a golden chance from ${num("distance_m", 1)} m for ${team}, but it's ${out}.`],
        line: `That's a huge chance for ${raw.shooter}, and somehow it stays out!`,
      },
      es: {
        analyst: [`Ocasión de ${num("xg", 2)} xG para ${raw.shooter}: ${out}`,
          `${raw.shooter} remata una ocasión de ${num("xg", 2)} xG desde ${num("distance_m", 1)} m: ${out}. ${team} acumula ${num("team_xg_total", 2)} xG.`],
        casual: [`¡Casi para ${raw.shooter}!`, `${raw.shooter} tuvo una ocasión clarísima desde ${num("distance_m", 1)} m, pero termina en ${out}.`],
        line: `¡Qué ocasión para ${raw.shooter}! Y no entra.`,
      },
      fr: {
        analyst: [`Occasion à ${num("xg", 2)} xG pour ${raw.shooter} : ${out}`,
          `${raw.shooter} obtient une occasion à ${num("xg", 2)} xG depuis ${num("distance_m", 1)} m : ${out}. ${team} totalise ${num("team_xg_total", 2)} xG.`],
        casual: [`Si près pour ${raw.shooter} !`, `${raw.shooter} avait une énorme occasion à ${num("distance_m", 1)} m, mais c'est ${out}.`],
        line: `Quelle occasion pour ${raw.shooter}, et ça ne rentre pas !`,
      },
      pt: {
        analyst: [`Chance de ${num("xg", 2)} xG para ${raw.shooter}: ${out}`,
          `${raw.shooter} tem uma chance de ${num("xg", 2)} xG a ${num("distance_m", 1)} m: ${out}. O ${team} soma ${num("team_xg_total", 2)} xG.`],
        casual: [`Quase de ${raw.shooter}!`, `${raw.shooter} teve uma chance enorme a ${num("distance_m", 1)} m, mas foi ${out}.`],
        line: `Que chance para ${raw.shooter}, e não entra!`,
      },
      de: {
        analyst: [`${num("xg", 2)}-xG-Chance für ${raw.shooter}: ${out}`,
          `${raw.shooter} hat eine Chance mit ${num("xg", 2)} xG aus ${num("distance_m", 1)} m: ${out}. ${team} steht bei ${num("team_xg_total", 2)} xG.`],
        casual: [`Fast! ${raw.shooter} vergibt`, `${raw.shooter} hatte aus ${num("distance_m", 1)} m die Riesenchance, doch: ${out}.`],
        line: `Die Riesenchance für ${raw.shooter}, und der Ball geht nicht rein!`,
      },
    },
    wp_swing: {
      en: {
        analyst: [`Outcome probability swing: ${team} ${num(`wp_${k}_before`)}% to ${num(`wp_${k}_after`)}%`,
          `No goal, but the model moves ${num(`wp_${k}_delta`)} pts towards ${team} inside 90 seconds. Pressure index ${num("pressure_index_home")} v ${num("pressure_index_away")}.`],
        casual: [`The game is tilting towards ${team}`, `Nothing on the scoreboard yet, but ${team} now have a ${num(`wp_${k}_after`)}% chance of winning.`],
        line: `You can feel it shifting. ${team} are taking hold of this one.`,
      },
      es: {
        analyst: [`Giro en la probabilidad: ${team} del ${num(`wp_${k}_before`)}% al ${num(`wp_${k}_after`)}%`,
          `Sin gol, el modelo se mueve ${num(`wp_${k}_delta`)} pts hacia ${team} en 90 segundos. Índice de presión ${num("pressure_index_home")} contra ${num("pressure_index_away")}.`],
        casual: [`El partido se inclina hacia ${team}`, `Sin goles todavía, pero ${team} ya tiene un ${num(`wp_${k}_after`)}% de opciones de ganar.`],
        line: `Se nota el cambio. ${team} empieza a mandar.`,
      },
      fr: {
        analyst: [`Bascule de probabilité : ${team} de ${num(`wp_${k}_before`)} % à ${num(`wp_${k}_after`)} %`,
          `Sans but, le modèle bouge de ${num(`wp_${k}_delta`)} pts vers ${team} en 90 secondes. Pression ${num("pressure_index_home")} contre ${num("pressure_index_away")}.`],
        casual: [`Le match penche vers ${team}`, `Pas encore de but, mais ${team} a désormais ${num(`wp_${k}_after`)} % de chances de gagner.`],
        line: `Ça bascule. ${team} prend la main.`,
      },
      pt: {
        analyst: [`Virada de probabilidade: ${team} de ${num(`wp_${k}_before`)}% para ${num(`wp_${k}_after`)}%`,
          `Sem gol, o modelo anda ${num(`wp_${k}_delta`)} pts para o ${team} em 90 segundos. Pressão ${num("pressure_index_home")} contra ${num("pressure_index_away")}.`],
        casual: [`O jogo pende para o ${team}`, `Ainda sem gol, mas o ${team} já tem ${num(`wp_${k}_after`)}% de chance de vencer.`],
        line: `Dá para sentir. O ${team} está tomando conta.`,
      },
      de: {
        analyst: [`Wahrscheinlichkeit kippt: ${team} von ${num(`wp_${k}_before`)} % auf ${num(`wp_${k}_after`)} %`,
          `Ohne Tor verschiebt sich das Modell binnen 90 Sekunden um ${num(`wp_${k}_delta`)} Pkt. zu ${team}. Druckindex ${num("pressure_index_home")} zu ${num("pressure_index_away")}.`],
        casual: [`Das Spiel kippt Richtung ${team}`, `Noch kein Tor, aber ${team} hat jetzt eine Siegchance von ${num(`wp_${k}_after`)} %.`],
        line: `Man spürt es: ${team} übernimmt.`,
      },
    },
    pressure_shift: {
      en: {
        analyst: [`Pressure flips to ${team}: index ${num(`pressure_index_${k}_after`)}`,
          `${team}'s pressure index climbs from ${num(`pressure_index_${k}_before`)} to ${num(`pressure_index_${k}_after`)}, with ${num("shots_last_5")} shots in the last five minutes and ${num("box_entries_last_5")} box entries.`],
        casual: [`${team} are turning the screw`, `${team} have flipped the pressure in their favour and are camped in ${opp}'s half.`],
        line: `${team} have turned this around, and ${opp} can't get out.`,
      },
      es: {
        analyst: [`La presión cambia hacia ${team}: índice ${num(`pressure_index_${k}_after`)}`,
          `El índice de presión de ${team} sube de ${num(`pressure_index_${k}_before`)} a ${num(`pressure_index_${k}_after`)}, con ${num("shots_last_5")} tiros en cinco minutos y ${num("box_entries_last_5")} entradas al área.`],
        casual: [`${team} aprieta`, `${team} ha dado la vuelta a la presión y encierra a ${opp} en su campo.`],
        line: `${team} le ha dado la vuelta y ${opp} no puede salir.`,
      },
      fr: {
        analyst: [`La pression change de camp : ${team}, indice ${num(`pressure_index_${k}_after`)}`,
          `L'indice de pression de ${team} passe de ${num(`pressure_index_${k}_before`)} à ${num(`pressure_index_${k}_after`)}, avec ${num("shots_last_5")} tirs en cinq minutes.`],
        casual: [`${team} met la pression`, `${team} a renversé la tendance et enferme ${opp} dans son camp.`],
        line: `${team} a renversé la vapeur, ${opp} étouffe.`,
      },
      pt: {
        analyst: [`A pressão muda para o ${team}: índice ${num(`pressure_index_${k}_after`)}`,
          `O índice de pressão do ${team} sobe de ${num(`pressure_index_${k}_before`)} para ${num(`pressure_index_${k}_after`)}, com ${num("shots_last_5")} chutes em cinco minutos.`],
        casual: [`${team} aperta`, `O ${team} virou a pressão e prende o ${opp} no campo de defesa.`],
        line: `O ${team} virou o jogo na pressão e o ${opp} não sai.`,
      },
      de: {
        analyst: [`Druck kippt zu ${team}: Index ${num(`pressure_index_${k}_after`)}`,
          `Der Druckindex von ${team} steigt von ${num(`pressure_index_${k}_before`)} auf ${num(`pressure_index_${k}_after`)}, bei ${num("shots_last_5")} Schüssen in fünf Minuten.`],
        casual: [`${team} macht Druck`, `${team} hat den Druck gedreht und schnürt ${opp} ein.`],
        line: `${team} dreht auf, ${opp} kommt nicht mehr raus.`,
      },
    },
    phase_change: {
      en: {
        analyst: [`Phase change: now ${phase(raw.phase_after)}`,
          `From ${phase(raw.phase_before)} to ${phase(raw.phase_after)}. Control index ${num("control_index_home")} v ${num("control_index_away")}, chaos index ${num("chaos_index")}, ${num("turnovers_last_5")} turnovers in five minutes.`],
        casual: [`The game has changed shape`, `This has become ${phase(raw.phase_after)}.`],
        line: `The whole feel of this match has changed.`,
      },
      es: {
        analyst: [`Cambio de fase: ahora ${phase(raw.phase_after)}`,
          `De ${phase(raw.phase_before)} a ${phase(raw.phase_after)}. Índice de control ${num("control_index_home")} contra ${num("control_index_away")}, caos ${num("chaos_index")}, ${num("turnovers_last_5")} pérdidas en cinco minutos.`],
        casual: [`El partido cambia de cara`, `Ahora vemos ${phase(raw.phase_after)}.`],
        line: `El partido ha cambiado por completo.`,
      },
      fr: {
        analyst: [`Changement de phase : ${phase(raw.phase_after)}`,
          `De ${phase(raw.phase_before)} à ${phase(raw.phase_after)}. Contrôle ${num("control_index_home")} contre ${num("control_index_away")}, chaos ${num("chaos_index")}.`],
        casual: [`Le match change de visage`, `On assiste maintenant à ${phase(raw.phase_after)}.`],
        line: `Le match a complètement changé.`,
      },
      pt: {
        analyst: [`Mudança de fase: ${phase(raw.phase_after)}`,
          `De ${phase(raw.phase_before)} para ${phase(raw.phase_after)}. Controle ${num("control_index_home")} contra ${num("control_index_away")}, caos ${num("chaos_index")}.`],
        casual: [`O jogo mudou de cara`, `Agora é ${phase(raw.phase_after)}.`],
        line: `O jogo mudou completamente.`,
      },
      de: {
        analyst: [`Phasenwechsel: ${phase(raw.phase_after)}`,
          `Von ${phase(raw.phase_before)} zu ${phase(raw.phase_after)}. Kontrolle ${num("control_index_home")} zu ${num("control_index_away")}, Chaosindex ${num("chaos_index")}.`],
        casual: [`Das Spiel hat sich gedreht`, `Jetzt sehen wir ${phase(raw.phase_after)}.`],
        line: `Das ganze Spiel fühlt sich anders an.`,
      },
    },
    tempo_change: {
      en: {
        analyst: [`Tempo ${raw.tempo_direction === "up" ? "spikes" : "drops"}: ${num("tempo_ppm", 1)} passes a minute`,
          `Rolling five-minute tempo is ${num("tempo_ppm", 1)} passes per minute against a match average of ${num("tempo_match_avg", 1)}.`],
        casual: [raw.tempo_direction === "up" ? `The pace has picked up` : `Everything has slowed down`, raw.tempo_direction === "up" ? `The ball is zipping around much faster than earlier.` : `Fewer passes, more caution. The game has taken a breath.`],
        line: raw.tempo_direction === "up" ? `This has really sped up now.` : `The game has gone quiet for a moment.`,
      },
      es: {
        analyst: [`El ritmo ${raw.tempo_direction === "up" ? "se dispara" : "cae"}: ${num("tempo_ppm", 1)} pases por minuto`,
          `Ritmo de cinco minutos: ${num("tempo_ppm", 1)} pases por minuto frente a una media de ${num("tempo_match_avg", 1)}.`],
        casual: [raw.tempo_direction === "up" ? `El partido se acelera` : `El partido se frena`, raw.tempo_direction === "up" ? `El balón circula mucho más rápido.` : `Menos pases, más cautela.`],
        line: raw.tempo_direction === "up" ? `¡Esto se ha acelerado!` : `El partido se toma un respiro.`,
      },
      fr: {
        analyst: [`Rythme ${raw.tempo_direction === "up" ? "en hausse" : "en baisse"} : ${num("tempo_ppm", 1)} passes par minute`,
          `Rythme sur cinq minutes : ${num("tempo_ppm", 1)} passes par minute, contre ${num("tempo_match_avg", 1)} en moyenne.`],
        casual: [raw.tempo_direction === "up" ? `Le match s'emballe` : `Le match ralentit`, raw.tempo_direction === "up" ? `Le ballon circule bien plus vite.` : `Moins de passes, plus de prudence.`],
        line: raw.tempo_direction === "up" ? `Ça accélère !` : `Le match souffle un peu.`,
      },
      pt: {
        analyst: [`Ritmo ${raw.tempo_direction === "up" ? "dispara" : "cai"}: ${num("tempo_ppm", 1)} passes por minuto`,
          `Ritmo de cinco minutos: ${num("tempo_ppm", 1)} passes por minuto, contra média de ${num("tempo_match_avg", 1)}.`],
        casual: [raw.tempo_direction === "up" ? `O jogo acelerou` : `O jogo desacelerou`, raw.tempo_direction === "up" ? `A bola roda muito mais rápido.` : `Menos passes, mais cautela.`],
        line: raw.tempo_direction === "up" ? `Que ritmo agora!` : `O jogo respira um pouco.`,
      },
      de: {
        analyst: [`Tempo ${raw.tempo_direction === "up" ? "zieht an" : "fällt"}: ${num("tempo_ppm", 1)} Pässe pro Minute`,
          `Fünf-Minuten-Tempo: ${num("tempo_ppm", 1)} Pässe pro Minute, Spielschnitt ${num("tempo_match_avg", 1)}.`],
        casual: [raw.tempo_direction === "up" ? `Das Tempo zieht an` : `Das Spiel wird langsamer`, raw.tempo_direction === "up" ? `Der Ball läuft viel schneller.` : `Weniger Pässe, mehr Vorsicht.`],
        line: raw.tempo_direction === "up" ? `Jetzt geht's richtig rund!` : `Das Spiel atmet durch.`,
      },
    },
    red_card: {
      en: {
        analyst: [`Red card, ${raw.player}: ${opp} up to ${num(`wp_${k === "home" ? "away" : "home"}_after`)}%`,
          `${raw.player} goes off (${raw.card}). ${team} down to ten; ${raw.home_team} win probability ${num("wp_home_before")}% to ${num("wp_home_after")}%.`],
        casual: [`${raw.player} is sent off!`, `${team} are down to ten men in the ${m} minute, and that hands ${opp} a real opening.`],
        line: `${raw.player} has to go, and ${team} have a mountain to climb now.`,
      },
      es: {
        analyst: [`Roja para ${raw.player}: ${opp} sube al ${num(`wp_${k === "home" ? "away" : "home"}_after`)}%`,
          `${raw.player} expulsado. ${team} con diez; probabilidad de ${raw.home_team} del ${num("wp_home_before")}% al ${num("wp_home_after")}%.`],
        casual: [`¡Expulsado ${raw.player}!`, `${team} se queda con diez en el minuto ${m} y eso abre la puerta a ${opp}.`],
        line: `${raw.player} se va a la calle y ${team} lo tiene muy difícil.`,
      },
      fr: {
        analyst: [`Rouge pour ${raw.player} : ${opp} à ${num(`wp_${k === "home" ? "away" : "home"}_after`)} %`,
          `${raw.player} est exclu. ${team} à dix ; probabilité de ${raw.home_team} de ${num("wp_home_before")} % à ${num("wp_home_after")} %.`],
        casual: [`${raw.player} est expulsé !`, `${team} se retrouve à dix à la ${m} minute, une vraie chance pour ${opp}.`],
        line: `${raw.player} doit sortir, la soirée se complique pour ${team}.`,
      },
      pt: {
        analyst: [`Vermelho para ${raw.player}: ${opp} vai a ${num(`wp_${k === "home" ? "away" : "home"}_after`)}%`,
          `${raw.player} expulso. ${team} com dez; probabilidade do ${raw.home_team} de ${num("wp_home_before")}% para ${num("wp_home_after")}%.`],
        casual: [`${raw.player} expulso!`, `O ${team} fica com dez aos ${m}, e isso abre o jogo para o ${opp}.`],
        line: `${raw.player} está fora, e o ${team} vai sofrer agora.`,
      },
      de: {
        analyst: [`Rot für ${raw.player}: ${opp} jetzt bei ${num(`wp_${k === "home" ? "away" : "home"}_after`)} %`,
          `${raw.player} fliegt. ${team} in Unterzahl; Siegchance ${raw.home_team} von ${num("wp_home_before")} % auf ${num("wp_home_after")} %.`],
        casual: [`Platzverweis für ${raw.player}!`, `${team} nur noch zu zehnt ab der ${m} Minute, die große Chance für ${opp}.`],
        line: `${raw.player} muss runter, für ${team} wird es jetzt richtig schwer.`,
      },
    },
    speed_record: {
      en: {
        analyst: [`${raw.player}: ${num("speed_kmh")} km/h, a new match high`,
          `${String(raw.record_kind).charAt(0).toUpperCase()}${String(raw.record_kind).slice(1)}: ${num("speed_kmh")} km/h, beating the previous match high of ${num("previous_match_high")} km/h.`],
        casual: [`${raw.player} hits ${num("speed_kmh")} km/h!`, `That's the ${raw.record_kind} so far.`],
        line: `Rapid! ${raw.player} at ${num("speed_kmh")} kilometres an hour.`,
      },
      es: {
        analyst: [`${raw.player}: ${num("speed_kmh")} km/h, nuevo récord del partido`,
          `Supera el récord previo del partido de ${num("previous_match_high")} km/h con ${num("speed_kmh")} km/h.`],
        casual: [`¡${raw.player} a ${num("speed_kmh")} km/h!`, `Lo más rápido que hemos visto en este partido.`],
        line: `¡Qué velocidad! ${raw.player} a ${num("speed_kmh")} kilómetros por hora.`,
      },
      fr: {
        analyst: [`${raw.player} : ${num("speed_kmh")} km/h, record du match`,
          `Il bat le précédent record du match (${num("previous_match_high")} km/h) avec ${num("speed_kmh")} km/h.`],
        casual: [`${raw.player} à ${num("speed_kmh")} km/h !`, `Le plus rapide du match jusqu'ici.`],
        line: `Quelle vitesse ! ${raw.player} à ${num("speed_kmh")} km/h.`,
      },
      pt: {
        analyst: [`${raw.player}: ${num("speed_kmh")} km/h, recorde do jogo`,
          `Supera o recorde anterior do jogo (${num("previous_match_high")} km/h) com ${num("speed_kmh")} km/h.`],
        casual: [`${raw.player} a ${num("speed_kmh")} km/h!`, `O mais rápido do jogo até agora.`],
        line: `Que velocidade! ${raw.player} a ${num("speed_kmh")} km/h.`,
      },
      de: {
        analyst: [`${raw.player}: ${num("speed_kmh")} km/h, Spitzenwert im Spiel`,
          `Übertrifft den bisherigen Spitzenwert von ${num("previous_match_high")} km/h mit ${num("speed_kmh")} km/h.`],
        casual: [`${raw.player} mit ${num("speed_kmh")} km/h!`, `So schnell war in diesem Spiel noch keiner.`],
        line: `Wahnsinn! ${raw.player} mit ${num("speed_kmh")} Stundenkilometern.`,
      },
    },
    elite_pass: {
      en: {
        analyst: [`${raw.passer} finds ${raw.recipient}: difficulty ${num("difficulty", 1)}/10`,
          `A ${num("length_m", 1)} m pass that gains ${num("progressive_m", 1)} m. The model gave it a ${num("expected_completion_pct")}% chance of arriving; it travelled at ${num("ball_speed_kmh")} km/h.`],
        casual: [`What a pass from ${raw.passer}!`, `${raw.passer} threads it through to ${raw.recipient}. Most passes like that don't arrive: only ${num("expected_completion_pct")}% do.`],
        line: `Oh, that is a beautiful ball from ${raw.passer}!`,
      },
      es: {
        analyst: [`${raw.passer} encuentra a ${raw.recipient}: dificultad ${num("difficulty", 1)}/10`,
          `Pase de ${num("length_m", 1)} m que gana ${num("progressive_m", 1)} m. El modelo le daba un ${num("expected_completion_pct")}% de éxito; viajó a ${num("ball_speed_kmh")} km/h.`],
        casual: [`¡Qué pase de ${raw.passer}!`, `${raw.passer} filtra el balón a ${raw.recipient}. Solo el ${num("expected_completion_pct")}% de pases así llegan.`],
        line: `¡Qué balón de ${raw.passer}!`,
      },
      fr: {
        analyst: [`${raw.passer} trouve ${raw.recipient} : difficulté ${num("difficulty", 1)}/10`,
          `Passe de ${num("length_m", 1)} m qui fait gagner ${num("progressive_m", 1)} m. Probabilité de réussite : ${num("expected_completion_pct")} %.`],
        casual: [`Quelle passe de ${raw.passer} !`, `${raw.passer} trouve ${raw.recipient}. Seulement ${num("expected_completion_pct")} % de ces passes arrivent.`],
        line: `Quel ballon de ${raw.passer} !`,
      },
      pt: {
        analyst: [`${raw.passer} acha ${raw.recipient}: dificuldade ${num("difficulty", 1)}/10`,
          `Passe de ${num("length_m", 1)} m que ganha ${num("progressive_m", 1)} m. Chance de chegar: ${num("expected_completion_pct")}%.`],
        casual: [`Que passe de ${raw.passer}!`, `${raw.passer} encontra ${raw.recipient}. Só ${num("expected_completion_pct")}% desses passes chegam.`],
        line: `Que bola de ${raw.passer}!`,
      },
      de: {
        analyst: [`${raw.passer} findet ${raw.recipient}: Schwierigkeit ${num("difficulty", 1)}/10`,
          `Ein ${num("length_m", 1)}-m-Pass mit ${num("progressive_m", 1)} m Raumgewinn. Erfolgswahrscheinlichkeit laut Modell: ${num("expected_completion_pct")} %.`],
        casual: [`Was für ein Pass von ${raw.passer}!`, `${raw.passer} spielt ${raw.recipient} frei. Nur ${num("expected_completion_pct")} % solcher Pässe kommen an.`],
        line: `Traumpass von ${raw.passer}!`,
      },
    },
    player_milestone: {
      en: {
        analyst: [`${raw.player}: ${num("value")} completed passes at ${num("pass_accuracy")}%`,
          `${raw.player} has the ${raw.milestone}: ${num("value")}, at ${num("pass_accuracy")}% accuracy. The hub of ${team}'s possession.`],
        casual: [`${raw.player} is running this game`, `Everything ${team} do goes through ${raw.player}: ${num("value")} passes completed already.`],
        line: `${raw.player} is everywhere. Everything goes through ${raw.player}.`,
      },
      es: {
        analyst: [`${raw.player}: ${num("value")} pases completados al ${num("pass_accuracy")}%`,
          `${raw.player} es el centrocampista con más pases completados: ${num("value")}, con un ${num("pass_accuracy")}% de acierto.`],
        casual: [`${raw.player} manda en el partido`, `Todo pasa por ${raw.player}: ya lleva ${num("value")} pases completados.`],
        line: `${raw.player} está en todas partes.`,
      },
      fr: {
        analyst: [`${raw.player} : ${num("value")} passes réussies à ${num("pass_accuracy")} %`,
          `${raw.player} est le milieu le plus précis du match : ${num("value")} passes réussies, ${num("pass_accuracy")} % de réussite.`],
        casual: [`${raw.player} dirige le match`, `Tout passe par ${raw.player} : déjà ${num("value")} passes réussies.`],
        line: `${raw.player} est partout.`,
      },
      pt: {
        analyst: [`${raw.player}: ${num("value")} passes certos com ${num("pass_accuracy")}%`,
          `${raw.player} é o meio-campista com mais passes certos: ${num("value")}, com ${num("pass_accuracy")}% de acerto.`],
        casual: [`${raw.player} comanda o jogo`, `Tudo passa por ${raw.player}: já são ${num("value")} passes certos.`],
        line: `${raw.player} está em todo lugar.`,
      },
      de: {
        analyst: [`${raw.player}: ${num("value")} angekommene Pässe, ${num("pass_accuracy")} %`,
          `${raw.player} ist der Mittelfeldspieler mit den meisten angekommenen Pässen: ${num("value")}, Quote ${num("pass_accuracy")} %.`],
        casual: [`${raw.player} lenkt das Spiel`, `Alles läuft über ${raw.player}: schon ${num("value")} angekommene Pässe.`],
        line: `${raw.player} ist überall.`,
      },
    },
    momentum_run: {
      en: {
        analyst: [`${team}: ${num("shots_last_5")} shots in five minutes, ${num("xg_last_5", 2)} xG`,
          `${team} have ${num("shots_last_5")} shots and ${num("xg_last_5", 2)} xG in the last five minutes; ${opp} have ${num("opp_shots_last_5")}.`],
        casual: [`${team} are all over them`, `Shot after shot from ${team}. ${opp} can't get a foothold.`],
        line: `Wave after wave from ${team}. Something has to give.`,
      },
      es: {
        analyst: [`${team}: ${num("shots_last_5")} tiros en cinco minutos, ${num("xg_last_5", 2)} xG`,
          `${team} suma ${num("shots_last_5")} tiros y ${num("xg_last_5", 2)} xG en cinco minutos; ${opp}, ${num("opp_shots_last_5")}.`],
        casual: [`${team} se vuelca al ataque`, `Tiro tras tiro de ${team}. ${opp} no encuentra la pelota.`],
        line: `Oleada tras oleada de ${team}.`,
      },
      fr: {
        analyst: [`${team} : ${num("shots_last_5")} tirs en cinq minutes, ${num("xg_last_5", 2)} xG`,
          `${team} compte ${num("shots_last_5")} tirs et ${num("xg_last_5", 2)} xG en cinq minutes ; ${opp}, ${num("opp_shots_last_5")}.`],
        casual: [`${team} pousse fort`, `Tir après tir pour ${team}. ${opp} subit.`],
        line: `Vague après vague de ${team}.`,
      },
      pt: {
        analyst: [`${team}: ${num("shots_last_5")} chutes em cinco minutos, ${num("xg_last_5", 2)} xG`,
          `O ${team} tem ${num("shots_last_5")} chutes e ${num("xg_last_5", 2)} xG em cinco minutos; o ${opp}, ${num("opp_shots_last_5")}.`],
        casual: [`${team} em cima`, `Chute atrás de chute do ${team}. O ${opp} só se defende.`],
        line: `Onda atrás de onda do ${team}.`,
      },
      de: {
        analyst: [`${team}: ${num("shots_last_5")} Schüsse in fünf Minuten, ${num("xg_last_5", 2)} xG`,
          `${team} mit ${num("shots_last_5")} Schüssen und ${num("xg_last_5", 2)} xG in fünf Minuten; ${opp} mit ${num("opp_shots_last_5")}.`],
        casual: [`${team} drückt gewaltig`, `Schuss um Schuss von ${team}. ${opp} kommt nicht mehr raus.`],
        line: `Angriff um Angriff von ${team}.`,
      },
    },
  };
  const summary = (title) => ({
    en: {
      analyst: [`${title}: ${raw.home_team} ${raw.score} ${raw.away_team}`,
        `xG ${num("xg_home", 2)} v ${num("xg_away", 2)}, shots ${num("shots_home")} v ${num("shots_away")}, possession ${num("possession_home")}% v ${num("possession_away")}%. Outcome probability ${num("wp_home")}% / ${num("wp_draw")}% / ${num("wp_away")}%.`],
      casual: [`${title}: ${raw.score}`, `${raw.home_team} ${raw.score} ${raw.away_team}. Standout so far: ${raw.top_player_1}.`],
      line: `${title} here, and it's ${raw.home_team} ${raw.score} ${raw.away_team}.`,
    },
    es: {
      analyst: [`${title === "Half-time" ? "Descanso" : "Final"}: ${raw.home_team} ${raw.score} ${raw.away_team}`,
        `xG ${num("xg_home", 2)} contra ${num("xg_away", 2)}, tiros ${num("shots_home")} contra ${num("shots_away")}, posesión ${num("possession_home")}% contra ${num("possession_away")}%.`],
      casual: [`${title === "Half-time" ? "Descanso" : "Final"}: ${raw.score}`, `${raw.home_team} ${raw.score} ${raw.away_team}. El mejor: ${raw.top_player_1}.`],
      line: `${title === "Half-time" ? "Descanso" : "Final"}: ${raw.home_team} ${raw.score} ${raw.away_team}.`,
    },
    fr: {
      analyst: [`${title === "Half-time" ? "Mi-temps" : "Fin du match"} : ${raw.home_team} ${raw.score} ${raw.away_team}`,
        `xG ${num("xg_home", 2)} contre ${num("xg_away", 2)}, tirs ${num("shots_home")} contre ${num("shots_away")}, possession ${num("possession_home")} % contre ${num("possession_away")} %.`],
      casual: [`${title === "Half-time" ? "Mi-temps" : "Fin du match"} : ${raw.score}`, `${raw.home_team} ${raw.score} ${raw.away_team}. Le meilleur : ${raw.top_player_1}.`],
      line: `${title === "Half-time" ? "Mi-temps" : "Fin du match"} : ${raw.home_team} ${raw.score} ${raw.away_team}.`,
    },
    pt: {
      analyst: [`${title === "Half-time" ? "Intervalo" : "Fim de jogo"}: ${raw.home_team} ${raw.score} ${raw.away_team}`,
        `xG ${num("xg_home", 2)} contra ${num("xg_away", 2)}, chutes ${num("shots_home")} contra ${num("shots_away")}, posse ${num("possession_home")}% contra ${num("possession_away")}%.`],
      casual: [`${title === "Half-time" ? "Intervalo" : "Fim de jogo"}: ${raw.score}`, `${raw.home_team} ${raw.score} ${raw.away_team}. Destaque: ${raw.top_player_1}.`],
      line: `${title === "Half-time" ? "Intervalo" : "Fim de jogo"}: ${raw.home_team} ${raw.score} ${raw.away_team}.`,
    },
    de: {
      analyst: [`${title === "Half-time" ? "Halbzeit" : "Abpfiff"}: ${raw.home_team} ${raw.score} ${raw.away_team}`,
        `xG ${num("xg_home", 2)} zu ${num("xg_away", 2)}, Schüsse ${num("shots_home")} zu ${num("shots_away")}, Ballbesitz ${num("possession_home")} % zu ${num("possession_away")} %.`],
      casual: [`${title === "Half-time" ? "Halbzeit" : "Abpfiff"}: ${raw.score}`, `${raw.home_team} ${raw.score} ${raw.away_team}. Bester Spieler: ${raw.top_player_1}.`],
      line: `${title === "Half-time" ? "Halbzeit" : "Abpfiff"}: ${raw.home_team} ${raw.score} ${raw.away_team}.`,
    },
  });
  if (moment.type === "half_time") return summary("Half-time")[lang];
  if (moment.type === "full_time") return summary("Full time")[lang];
  return T[moment.type]?.[lang] ?? T.wp_swing[lang];
}

const FAN = {
  en: { pos: "Get in!", neg: "That stings.", neutral: "Eyes on {team}.", tailPos: "Keep it going, {team}.", tailNeg: "{team} need an answer." },
  es: { pos: "¡Vamos!", neg: "Duele.", neutral: "Atentos, {team}.", tailPos: "¡Sigue así, {team}!", tailNeg: "{team} necesita reaccionar." },
  fr: { pos: "Allez !", neg: "Ça fait mal.", neutral: "Attention, {team}.", tailPos: "Continue, {team} !", tailNeg: "{team} doit réagir." },
  pt: { pos: "Vamos!", neg: "Dói.", neutral: "Atenção, {team}.", tailPos: "Segue assim, {team}!", tailNeg: "O {team} precisa reagir." },
  de: { pos: "Jawohl!", neg: "Bitter.", neutral: "Achtung, {team}.", tailPos: "Weiter so, {team}!", tailNeg: "{team} muss antworten." },
};
const LENS = {
  en: { in: "{name} was right in the middle of it.", out: "{name} wasn't involved in this one." },
  es: { in: "{name} estuvo en el centro de la jugada.", out: "{name} no participó en esta acción." },
  fr: { in: "{name} était au cœur de l'action.", out: "{name} n'était pas impliqué cette fois." },
  pt: { in: "{name} estava no centro do lance.", out: "{name} não participou deste lance." },
  de: { in: "{name} war mittendrin.", out: "{name} war hier nicht beteiligt." },
};

/** Build one InsightVariant. `perspective` is a team id (club_fan) or player object (player_lens). */
export function variant(moment, persona, lang, ctx, perspective = null) {
  const c = copy(moment, lang, ctx);
  const base = { persona, language: lang, perspective_team_id: null, perspective_player_id: null };
  if (persona === "analyst" || persona === "casual") {
    const [h, b] = c[persona];
    return { ...base, headline: fit(h, 90), body: fit(b, 300), commentary_line: fit(c.line, 160) };
  }
  if (persona === "club_fan") {
    const fan = FAN[lang];
    const teamName = ctx.teamName(perspective);
    const mood = moment.team_id == null ? "neutral" : moment.team_id === perspective ? "pos" : "neg";
    const [h, b] = c.casual;
    const tail = mood === "pos" ? fan.tailPos : mood === "neg" ? fan.tailNeg : "";
    return {
      ...base, perspective_team_id: perspective,
      headline: fit(`${fan[mood].replace("{team}", teamName)} ${h}`, 90),
      body: fit(`${b} ${tail.replace("{team}", teamName)}`.trim(), 300),
      commentary_line: fit(c.line, 160),
    };
  }
  // player_lens
  const p = perspective;
  const involved = moment.player_ids.includes(p.player_id);
  const [h, b] = c.casual;
  return {
    ...base, perspective_player_id: p.player_id,
    headline: fit(involved ? `${p.short_name}: ${h}` : h, 90),
    body: fit(`${b} ${LENS[lang][involved ? "in" : "out"].replace("{name}", p.short_name)}`, 300),
    commentary_line: fit(c.line, 160),
  };
}

const TAG = {
  goal: "turning_point", big_chance: "chaos", wp_swing: "turning_point", pressure_shift: "pressure_shift",
  phase_change: "control", tempo_change: "rhythm", red_card: "turning_point", speed_record: "individual_brilliance",
  elite_pass: "individual_brilliance", player_milestone: "individual_brilliance", momentum_run: "pressure_shift",
  half_time: "rhythm", full_time: "turning_point",
};

/** Build the full Insight (all personas, all languages; club_fan for both teams). */
export function buildInsight(moment, ctx, { id, latencyMs, status }) {
  const variants = [];
  for (const lang of LANGUAGES) {
    variants.push(variant(moment, "analyst", lang, ctx), variant(moment, "casual", lang, ctx));
    for (const t of [ctx.home.team_id, ctx.away.team_id]) variants.push(variant(moment, "club_fan", lang, ctx, t));
  }
  const en = copy(moment, "en", ctx);
  const notes = {
    passed: [],
    repaired: ["Editor draft quoted a number not in the fact pack; regenerated once, then passed"],
    fallback: ["Editor output failed fact-check twice; deterministic template used"],
  }[status];
  return {
    contract_version: "1.0.0", insight_id: id, match_id: moment.match_id, moment_id: moment.moment_id,
    clock_ms: moment.clock_ms,
    explanation: {
      headline: fit(en.analyst[0], 90), why_it_matters: fit(en.analyst[1], 400), tactical_tag: TAG[moment.type] ?? "turning_point",
      key_fact_keys: moment.facts.slice(4).map((f) => f.key).slice(0, 6),
    },
    variants, check_status: status, check_notes: notes, agent_trace_id: id.slice(0, 32), latency_ms: latencyMs,
    created_at: new Date().toISOString(),
  };
}

/** Pick the variant for a viewer's prefs (server-side personalisation), generating player_lens on demand. */
export function pickVariant(insight, moment, prefs, ctx) {
  const lang = LANGUAGES.includes(prefs.language) ? prefs.language : "en";
  if (prefs.persona === "player_lens") {
    const p = ctx.player(prefs.followed_player_id) ?? ctx.player(moment.player_ids[0]) ?? ctx.home.players[9];
    return variant(moment, "player_lens", lang, ctx, p);
  }
  if (prefs.persona === "club_fan") {
    const team = prefs.favourite_team_id ?? ctx.home.team_id;
    return insight.variants.find((v) => v.persona === "club_fan" && v.language === lang && v.perspective_team_id === team) ??
      variant(moment, "club_fan", lang, ctx, team);
  }
  return insight.variants.find((v) => v.persona === prefs.persona && v.language === lang) ??
    insight.variants.find((v) => v.persona === "casual" && v.language === lang);
}

// ------------------------------------------------------------------ overlay cues
const STAT = {
  xg: { en: "xG", es: "xG", fr: "xG", pt: "xG", de: "xG" },
  speed: { en: "Speed", es: "Velocidad", fr: "Vitesse", pt: "Velocidade", de: "Tempo" },
  distance: { en: "Distance", es: "Distancia", fr: "Distance", pt: "Distância", de: "Distanz" },
  wp: { en: "Win prob.", es: "Prob. victoria", fr: "Prob. victoire", pt: "Prob. vitória", de: "Siegchance" },
  difficulty: { en: "Difficulty", es: "Dificultad", fr: "Difficulté", pt: "Dificuldade", de: "Schwierigkeit" },
  completion: { en: "Exp. completion", es: "Éxito esperado", fr: "Réussite attendue", pt: "Acerto esperado", de: "Erw. Quote" },
  length: { en: "Length", es: "Longitud", fr: "Longueur", pt: "Distância", de: "Länge" },
  pressure: { en: "Pressure", es: "Presión", fr: "Pression", pt: "Pressão", de: "Druck" },
  control: { en: "Control", es: "Control", fr: "Contrôle", pt: "Controle", de: "Kontrolle" },
  chaos: { en: "Chaos", es: "Caos", fr: "Chaos", pt: "Caos", de: "Chaos" },
  shots: { en: "Shots (5 min)", es: "Tiros (5 min)", fr: "Tirs (5 min)", pt: "Chutes (5 min)", de: "Schüsse (5 Min.)" },
  team_xg: { en: "Team xG", es: "xG equipo", fr: "xG équipe", pt: "xG equipe", de: "Team-xG" },
  tempo: { en: "Passes/min", es: "Pases/min", fr: "Passes/min", pt: "Passes/min", de: "Pässe/Min." },
  passes: { en: "Completed passes", es: "Pases completados", fr: "Passes réussies", pt: "Passes certos", de: "Angek. Pässe" },
  accuracy: { en: "Accuracy", es: "Acierto", fr: "Réussite", pt: "Acerto", de: "Quote" },
  possession: { en: "Possession", es: "Posesión", fr: "Possession", pt: "Posse", de: "Ballbesitz" },
  before: { en: "Before", es: "Antes", fr: "Avant", pt: "Antes", de: "Vorher" },
  after: { en: "Now", es: "Ahora", fr: "Maintenant", pt: "Agora", de: "Jetzt" },
};
const SHOOT = { en: "Shot", es: "Tiro", fr: "Tir", pt: "Chute", de: "Schuss" };
const TOUCH = {
  shot: SHOOT, goal: { en: "Goal", es: "Gol", fr: "But", pt: "Gol", de: "Tor" },
  dribble: { en: "Dribble won", es: "Regate ganado", fr: "Dribble réussi", pt: "Drible certo", de: "Dribbling gewonnen" },
  tackle: { en: "Tackle won", es: "Entrada ganada", fr: "Tacle réussi", pt: "Desarme", de: "Zweikampf gewonnen" },
  interception: { en: "Interception", es: "Intercepción", fr: "Interception", pt: "Interceptação", de: "Ballgewinn" },
  pass: { en: "Line-breaking pass", es: "Pase entre líneas", fr: "Passe qui casse les lignes", pt: "Passe entre linhas", de: "Linienbrechender Pass" },
};

function cueStats(moment, lang) {
  const { raw, num } = facts(moment, lang);
  const s = (key, value, unit = null) => ({ label: STAT[key][lang], value: String(value), unit });
  const k = moment.team_id === raw._home_id ? "home" : "away";
  switch (moment.type) {
    case "goal":
      return [s("xg", num("xg", 2)), s("speed", num("shot_speed_kmh"), "km/h"), s("distance", num("distance_m", 1), "m"), s("wp", num("wp_scorer_after"), "%")];
    case "big_chance":
      return [s("xg", num("xg", 2)), s("distance", num("distance_m", 1), "m"), s("team_xg", num("team_xg_total", 2))];
    case "elite_pass":
      return [s("difficulty", `${num("difficulty", 1)}/10`), s("completion", num("expected_completion_pct"), "%"), s("length", num("length_m", 1), "m"), s("speed", num("ball_speed_kmh"), "km/h")];
    case "speed_record":
      return [s("speed", num("speed_kmh"), "km/h"), s("before", num("previous_match_high"), "km/h")];
    case "player_milestone":
      return [s("passes", num("value")), s("accuracy", num("pass_accuracy"), "%")];
    case "pressure_shift":
      return [s("pressure", num(`pressure_index_${k}_after`)), s("before", num(`pressure_index_${k}_before`)), s("shots", num("shots_last_5"))];
    case "momentum_run":
      return [s("shots", num("shots_last_5")), s("xg", num("xg_last_5", 2))];
    case "phase_change":
      return [s("control", `${num("control_index_home")}–${num("control_index_away")}`), s("chaos", num("chaos_index"))];
    case "tempo_change":
      return [s("tempo", num("tempo_ppm", 1)), s("before", num("tempo_match_avg", 1))];
    case "half_time":
    case "full_time":
      return [s("xg", `${num("xg_home", 2)}–${num("xg_away", 2)}`), s("possession", `${num("possession_home")}–${num("possession_away")}`, "%")];
    default:
      return [];
  }
}

/** Map one insight variant to the OverlayCue[] for that viewer (API_CONTRACT §4). */
export function buildCues(moment, insight, v, ctx, { showAtClockMs, syncSeq, newId }) {
  const lang = v.language;
  const facts0 = { ...moment, facts: [...moment.facts, { key: "_home_id", label: "", value: ctx.home.team_id }] };
  const stats = cueStats(facts0, lang);
  const common = {
    contract_version: "1.0.0", match_id: moment.match_id, moment_id: moment.moment_id, insight_id: insight.insight_id,
    sync_clock_ms: moment.clock_ms, sync_seq: syncSeq, show_at_clock_ms: showAtClockMs, persona: v.persona,
    language: lang, perspective_team_id: v.perspective_team_id, perspective_player_id: v.perspective_player_id,
    team_id: moment.team_id, player_id: moment.player_ids[0] ?? null,
  };
  const cue = (template, anchor, priority, duration, extra = {}) => ({
    cue_id: newId(), ...common, template, anchor, priority, duration_ms: duration,
    headline: v.headline, body: v.body, stats, ...extra,
  });
  const wpStats = (key) => {
    const before = Math.round(moment.wp_before[key] * 100);
    const after = Math.round(moment.wp_after[key] * 100);
    return [{ label: STAT.before[lang], value: String(before), unit: "%" }, { label: STAT.after[lang], value: String(after), unit: "%" }];
  };
  const side = moment.team_id === ctx.home.team_id ? "home" : "away";
  switch (moment.type) {
    case "goal":
      return [
        cue("lower_third", "bottom_center", 5, 8000),
        cue("wp_swing", "top_right", 4, 6000, { body: v.commentary_line, stats: wpStats(side) }),
      ];
    case "red_card":
      return [
        cue("lower_third", "bottom_center", 4, 7000, { stats: [] }),
        cue("wp_swing", "top_right", 4, 6000, { body: v.commentary_line, stats: wpStats(side === "home" ? "away" : "home"), team_id: ctx.opponentId(moment.team_id) }),
      ];
    case "wp_swing":
      return [cue("wp_swing", "top_right", 4, 6000, { stats: wpStats(side) })];
    case "pressure_shift":
    case "momentum_run":
    case "phase_change":
    case "tempo_change":
      return [cue("pressure_bar", "top_left", 3, 6000)];
    case "big_chance":
      return [cue("stat_card", "bottom_right", 3, 6000)];
    case "elite_pass":
    case "speed_record":
    case "player_milestone":
      return [cue("stat_card", "bottom_right", 2, 6000)];
    case "half_time":
      return [cue("stat_card", "bottom_right", 3, 8000)];
    case "full_time":
      return [cue("recap_card", "bottom_center", 5, 15000)];
    default:
      return [];
  }
}

/** Template player_tag cue when the viewer's followed player is involved (no LLM; API_CONTRACT §3). */
export function playerTagCue(ev, player, prefs, { newId }) {
  const lang = LANGUAGES.includes(prefs.language) ? prefs.language : "en";
  let kind = ev.type;
  if (kind === "pass" && !ev.pass?.line_breaking) return null;
  if (!TOUCH[kind]) return null;
  if (["dribble", "tackle", "interception", "pass"].includes(kind) && ev.outcome !== "success") return null;
  const stats = [];
  if (ev.shot) stats.push({ label: "xG", value: lang === "en" ? ev.shot.xg.toFixed(2) : ev.shot.xg.toFixed(2).replace(".", ","), unit: null });
  if (ev.pass) stats.push({ label: STAT.difficulty[lang], value: `${lang === "en" ? ev.pass.difficulty : String(ev.pass.difficulty).replace(".", ",")}/10`, unit: null });
  return {
    contract_version: "1.0.0", cue_id: newId(), match_id: ev.match_id, moment_id: null, insight_id: null,
    template: "player_tag", anchor: "bottom_left", priority: 2, sync_clock_ms: ev.clock_ms, sync_seq: ev.seq,
    show_at_clock_ms: ev.clock_ms, duration_ms: 4000, persona: prefs.persona, language: lang,
    perspective_team_id: null, perspective_player_id: player.player_id, team_id: ev.team_id, player_id: player.player_id,
    headline: `${player.shirt_number} ${player.short_name}`, body: TOUCH[kind][lang], stats,
  };
}

// ------------------------------------------------------------------ recap
const RECAP = {
  en: { title: "{home} {score} {away}", goals: "Goals", turn: "The turning point", potm: "Player of the match", xg: "xG", poss: "possession" },
  es: { title: "{home} {score} {away}", goals: "Goles", turn: "El momento clave", potm: "Jugador del partido", xg: "xG", poss: "posesión" },
  fr: { title: "{home} {score} {away}", goals: "Buts", turn: "Le tournant", potm: "Homme du match", xg: "xG", poss: "possession" },
  pt: { title: "{home} {score} {away}", goals: "Gols", turn: "O momento decisivo", potm: "Melhor em campo", xg: "xG", poss: "posse" },
  de: { title: "{home} {score} {away}", goals: "Tore", turn: "Der Wendepunkt", potm: "Spieler des Spiels", xg: "xG", poss: "Ballbesitz" },
};

/** Build a Recap for one persona/language from the full-time moment and the goal moments. */
export function buildRecap(ftMoment, moments, ctx, prefs) {
  const lang = LANGUAGES.includes(prefs.language) ? prefs.language : "en";
  const R = RECAP[lang];
  const { raw, num } = facts(ftMoment, lang);
  const goals = moments.filter((m) => m.type === "goal").map((m) => {
    const f = Object.fromEntries(m.facts.map((x) => [x.key, x.value]));
    return `${f.scorer} ${f.minute}`;
  });
  const turning = [...moments].filter((m) => m.type !== "full_time" && m.type !== "half_time").sort((a, b) => b.significance - a.significance || (b.type === "goal") - (a.type === "goal"))[0];
  const turnText = turning ? copy(turning, lang, ctx).casual[0] : "";
  const potmId = raw.top_player_1_id;
  const potm = ctx.player(potmId);
  const title = R.title.replace("{home}", raw.home_team).replace("{score}", raw.score).replace("{away}", raw.away_team);
  const lines = {
    analyst: `${R.xg} ${num("xg_home", 2)}–${num("xg_away", 2)}, ${R.poss} ${num("possession_home")}%–${num("possession_away")}%. ${R.goals}: ${goals.join(", ")}. ${R.turn}: ${turnText}. ${R.potm}: ${raw.top_player_1}.`,
    casual: `${R.goals}: ${goals.join(", ")}. ${R.turn}: ${turnText}. ${R.potm}: ${potm?.name ?? raw.top_player_1}.`,
  };
  let summary = lines[prefs.persona] ?? lines.casual;
  if (prefs.persona === "club_fan" && prefs.favourite_team_id) {
    const v = variant(ftMoment, "club_fan", lang, ctx, prefs.favourite_team_id);
    summary = `${lines.casual} ${v.body.split(". ").slice(-1)[0]}`;
  }
  if (prefs.persona === "player_lens" && prefs.followed_player_id) {
    const p = ctx.player(prefs.followed_player_id);
    if (p) summary = `${lines.casual} ${LENS[lang][goals.some((g) => g.startsWith(p.name)) ? "in" : "out"].replace("{name}", p.short_name)}`;
  }
  const keyIds = moments
    .filter((m) => ["goal", "red_card"].includes(m.type) || m.significance >= 0.5)
    .filter((m) => m.type !== "full_time" && m.type !== "half_time")
    .map((m) => m.moment_id);
  return {
    match_id: ftMoment.match_id, persona: prefs.persona, language: lang, title,
    summary: summary.slice(0, 1200), key_moment_ids: keyIds, player_of_the_match_id: potmId ?? null, check_status: "passed",
  };
}
