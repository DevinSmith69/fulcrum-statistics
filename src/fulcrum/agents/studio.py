"""Studio Analyst ("Ask the studio", on demand, cached) — a viewer's question about the live match, answered.

The answer is grounded only in a **context pack** built deterministically from the match so far
(`build_pack`): the score and minute, both teams' live stats and the top player stats from `MatchState`, and the
moments so far with their fact packs (trimmed to the most significant plus the most recent), plus the rosters.
Every number in the answer must exist in that pack: the answer goes through the same deterministic
fact-checker as every insight (numbers incl. decimal commas, real-club denylist, foreign names, betting terms,
length, cut-off copy), plus checks on cited moment ids, fact keys and leaked ids. One repair; if it still fails,
or the LLM errors / times out, the viewer gets a safe deterministic reply with `answerable=false`.

Betting/odds questions and questions naming a real club or competition are refused before any LLM call;
other off-topic questions are declined by the model itself (the prompt asks for `answerable=false`).

Verified answers are cached per (match, normalised question, language, persona, favourite team, pack
version) in a bounded LRU; the pack version is the number of moments so far, so an answer refreshes when the
match moves on. Concurrent requests for the same key share one LLM call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import cached_property

from fulcrum.contracts import (
    AskAnswer,
    CheckStatus,
    Fact,
    Insight,
    MatchState,
    Moment,
    Persona,
    PlayerLiveStats,
    Team,
    TeamLiveStats,
)
from fulcrum.llm import LLMClient

from .context import fmt_value, format_facts_table, format_roster, minute_label, moment_minute
from .factcheck import FactSheet, Issue, base_language, cut_off
from .orchestrator import LLMTimeout, OrchestratorConfig, call_llm
from .prompts import STUDIO_ANALYST
from .schemas import StudioAnswerOutput
from .translate import LANGUAGE_NAMES, normalise_language

log = logging.getLogger("fulcrum.agents")

MAX_MOMENTS = 12  # moments in the pack: the most significant ...
RECENT_MOMENTS = 4  # ... plus always the latest few
FACTS_PER_MOMENT = 12
MAX_ANSWER = 400
CACHE_SIZE = 256
SKIP_MOMENT_FACTS = {"home_team", "away_team", "home_team_id", "away_team_id"}

# Deterministic replies (no numbers, no banned terms; `tests/agents/test_studio.py` fact-checks them).
SAFE_REPLY: dict[str, str] = {
    "en": "I can only answer from this match's data. Try asking who is on top, how the win probability has "
    "moved, or who the standout players are.",
    "es": "Solo puedo responder con los datos de este partido. Prueba a preguntar quién domina, cómo ha "
    "cambiado la probabilidad de victoria o quiénes son los jugadores más destacados.",
    "fr": "Je ne peux répondre qu'avec les données de ce match. Demandez plutôt qui domine, comment la "
    "probabilité de victoire a évolué ou quels joueurs se distinguent.",
    "pt": "Só posso responder com os dados desta partida. Pergunte quem está por cima, como a probabilidade "
    "de vitória mudou ou quem são os destaques.",
    "de": "Ich kann nur mit den Daten dieses Spiels antworten. Frag zum Beispiel, wer das Spiel bestimmt, wie "
    "sich die Siegwahrscheinlichkeit verändert hat oder wer heraussticht.",
}
OUT_OF_SCOPE_REPLY: dict[str, str] = {
    "en": "That's not something I cover: I only explain this match, from its own data. Ask me who is on top, "
    "what changed the game, or who stands out.",
    "es": "Eso no lo cubro: solo explico este partido, con sus propios datos. Pregúntame quién domina, qué "
    "cambió el partido o quién destaca.",
    "fr": "Ce n'est pas mon domaine : j'explique uniquement ce match, à partir de ses propres données. "
    "Demandez-moi qui domine, ce qui a changé le match ou qui se distingue.",
    "pt": "Isso não é comigo: só explico esta partida, com os dados dela. Pergunte quem está por cima, o que "
    "mudou o jogo ou quem se destaca.",
    "de": "Das gehört nicht zu meinen Themen: Ich erkläre nur dieses Spiel, mit seinen eigenen Daten. Frag "
    "mich, wer das Spiel bestimmt, was es gedreht hat oder wer heraussticht.",
}

PERSONA_TONE: dict[str, str] = {
    "analyst": "analyst: precise and tactical; quote the 2-3 most relevant numbers.",
    "casual": 'casual: warm, plain words, no jargon (say "chance quality", not "xG"); at most 1 number.',
    "club_fan": "club_fan for {team}: openly on {team}'s side, joy or anguish, but strictly factual.",
    "player_lens": "follows individual players: plain words, focus on the players involved; at most 2 numbers.",
}


def normalise_question(question: str) -> str:
    """Cache key form: case-folded, whitespace collapsed, surrounding punctuation stripped."""
    return " ".join(question.split()).casefold().strip(" ?¿!¡.,;:")


def answer_language(language: str | None) -> str:
    """A supported answer language ('pt-BR' → 'pt-br'); anything we can't name falls back to 'en'."""
    lang = normalise_language(language or "en")
    return lang if base_language(lang) in LANGUAGE_NAMES else "en"


def _reply(table: dict[str, str], language: str) -> str:
    return table.get(base_language(language), table["en"])


# --------------------------------------------------------------------------- #
# The context pack
# --------------------------------------------------------------------------- #


def _num(value: float, digits: int) -> float | int:
    """Round for the pack (and therefore for the fact-checker): 0 digits → int."""
    return round(value) if digits == 0 else round(value, digits)


def _f(key: str, label: str, value: float | int | str, unit: str | None = None) -> Fact:
    return Fact(key=key, label=label, value=value, unit=unit)


# (field, label suffix, digits, unit) — the team stats a viewer is likely to ask about
_TEAM_STATS: tuple[tuple[str, str, int, str | None], ...] = (
    ("xg", "xG", 2, "xG"),
    ("shots", "shots", 0, None),
    ("shots_on_target", "shots on target", 0, None),
    ("possession_pct", "possession", 0, "%"),
    ("passes_completed", "completed passes", 0, None),
    ("pass_accuracy", "pass accuracy", 0, "%"),
    ("avg_pass_difficulty", "average pass difficulty (0-10)", 1, None),
    ("box_entries", "box entries", 0, None),
    ("corners", "corners", 0, None),
    ("fouls", "fouls", 0, None),
    ("yellow_cards", "yellow cards", 0, None),
    ("red_cards", "red cards", 0, None),
    ("pressure_index", "pressure index (0-100)", 0, None),
    ("control_index", "control index (last 5 min, 0-100)", 0, None),
)


def _team_facts(side: str, team: Team, stats: TeamLiveStats) -> list[Fact]:
    return [
        _f(f"{side}_{name}", f"{team.name} {label}", _num(float(getattr(stats, name)), digits), unit)
        for name, label, digits, unit in _TEAM_STATS
    ]


# Ranks as words: a "#3" in a label would make 3 a quotable number for the fact-checker.
_RANKS = {1: "first", 2: "second", 3: "third"}


def _leaders(
    players: Iterable[PlayerLiveStats],
    key: Callable[[PlayerLiveStats], float],
    n: int,
) -> list[PlayerLiveStats]:
    ranked = sorted((p for p in players if key(p) > 0), key=lambda p: (-key(p), p.player_id))
    return ranked[:n]


def _player_facts(state: MatchState, home: Team, away: Team) -> list[Fact]:
    """Top player stats: best rated, best passers, most xG, fastest, most running, best defender."""
    names = {p.player_id: f"{p.short_name} ({t.short_name})" for t in (home, away) for p in t.players}
    players = [p for p in state.players.values() if p.player_id in names and (p.touches or p.minutes)]
    facts: list[Fact] = []

    def add(
        prefix: str, label: str, chosen: list[PlayerLiveStats], stats: Sequence[tuple[str, str, int, str | None]]
    ) -> None:
        for i, p in enumerate(chosen, start=1):
            facts.append(_f(f"{prefix}_{i}", f"{label} ({_RANKS[i]})", names[p.player_id]))
            for name, stat_label, digits, unit in stats:
                facts.append(
                    _f(
                        f"{prefix}_{i}_{name}",
                        f"{label} ({_RANKS[i]}) {stat_label}",
                        _num(getattr(p, name), digits),
                        unit,
                    )
                )

    add("top_rated", "Top rated player", _leaders(players, lambda p: p.rating, 3), [("rating", "rating", 1, None)])
    add(
        "top_passer",
        "Most completed passes",
        _leaders(players, lambda p: p.passes_completed, 2),
        [
            ("passes_completed", "completed passes", 0, None),
            ("pass_accuracy", "pass accuracy", 0, "%"),
            ("avg_pass_difficulty", "average pass difficulty (0-10)", 1, None),
            ("key_passes", "key passes", 0, None),
        ],
    )
    add(
        "top_xg",
        "Most xG",
        _leaders(players, lambda p: p.xg, 2),
        [("xg", "xG", 2, "xG"), ("shots", "shots", 0, None), ("goals", "goals", 0, None)],
    )
    add(
        "fastest",
        "Fastest player",
        _leaders(players, lambda p: p.top_speed_kmh, 1),
        [("top_speed_kmh", "top speed", 1, "km/h")],
    )
    add(
        "most_distance",
        "Most distance covered",
        _leaders(players, lambda p: p.distance_km, 1),
        [("distance_km", "distance", 1, "km")],
    )
    add(
        "top_defender",
        "Most tackles won + interceptions",
        _leaders(players, lambda p: p.tackles_won + p.interceptions, 1),
        [("tackles_won", "tackles won", 0, None), ("interceptions", "interceptions", 0, None)],
    )
    return facts


def state_facts(state: MatchState | None, home: Team, away: Team, score: str) -> list[Fact]:
    """The match-level facts of the pack: score, minute, team stats, win probability, indices, top players."""
    facts = [_f("score", f"Score ({home.name} - {away.name})", score)]
    if state is None:
        return facts
    wp = state.win_prob
    facts += [
        _f("minute", "Minute", minute_label(state.clock_ms)),
        _f("wp_home", f"{home.name} win probability", _num(wp.home * 100, 0), "%"),
        _f("wp_draw", "Draw probability", _num(wp.draw * 100, 0), "%"),
        _f("wp_away", f"{away.name} win probability", _num(wp.away * 100, 0), "%"),
        _f("phase", "Match phase", str(state.phase)),
        _f("momentum", f"Momentum (-100 {away.name} on top, +100 {home.name} on top)", _num(state.momentum, 0)),
        _f("tempo_ppm", "Tempo, passes per minute (last 5 min)", _num(state.tempo_ppm, 1), "passes/min"),
        _f("chaos_index", "Chaos index (0-100)", _num(state.chaos_index, 0)),
        *_team_facts("home", home, state.home),
        *_team_facts("away", away, state.away),
        *_player_facts(state, home, away),
    ]
    return facts


def select_pack_moments(
    history: Sequence[tuple[Moment, Insight | None]],
    n: int = MAX_MOMENTS,
    recent: int = RECENT_MOMENTS,
) -> list[tuple[Moment, Insight | None]]:
    """The most significant moments plus the latest `recent`, at most `n`, in match order."""
    latest = list(history[-recent:]) if recent else []
    ids = {m.moment_id for m, _ in latest}
    rest = sorted(
        (mi for mi in history if mi[0].moment_id not in ids), key=lambda mi: (-mi[0].significance, mi[0].clock_ms)
    )
    chosen = latest + rest[: max(0, n - len(latest))]
    return sorted(chosen, key=lambda mi: (mi[0].clock_ms, mi[0].seq_end))


@dataclass(frozen=True)
class PackMoment:
    """One moment as the Studio Analyst sees it: id, minute, type, headline and (trimmed) facts."""

    moment: Moment
    headline: str
    facts: list[Fact]

    @property
    def minute(self) -> str:
        return moment_minute(self.moment)


@dataclass(frozen=True)
class StudioPack:
    """Everything the Studio Analyst may use to answer, and nothing else (built by `build_pack`)."""

    match_id: str
    home: Team
    away: Team
    score: str
    clock_ms: int | None
    facts: list[Fact]
    moments: list[PackMoment]
    foreign_names: tuple[str, ...] = ()
    version: int = 0  # number of moments so far: part of the cache key

    @property
    def moment_ids(self) -> set[str]:
        return {pm.moment.moment_id for pm in self.moments}

    @property
    def fact_keys(self) -> set[str]:
        return {f.key for f in self.facts} | {f.key for pm in self.moments for f in pm.facts}

    @property
    def player_ids(self) -> set[str]:
        return {p.player_id for t in (self.home, self.away) for p in t.players}

    @cached_property
    def sheet(self) -> FactSheet:
        """Allowed numbers: every pack fact, the score, the current minute and every moment's minute."""
        return FactSheet.build(
            [*self.facts, *(f for pm in self.moments for f in pm.facts)],
            score=self.score,
            clock_ms=self.clock_ms,
            foreign_names=self.foreign_names,
            extra_numbers=[pm.minute for pm in self.moments],
        )


def build_pack(
    match_id: str,
    home: Team,
    away: Team,
    *,
    state: MatchState | None,
    history: Sequence[tuple[Moment, Insight | None]],
    score: str,
    foreign_names: Sequence[str] = (),
) -> StudioPack:
    """Assemble the context pack deterministically from the match so far."""
    moments = [
        PackMoment(
            moment=m,
            headline=i.explanation.headline if i is not None else "",
            facts=[f for f in m.facts if f.key not in SKIP_MOMENT_FACTS][:FACTS_PER_MOMENT],
        )
        for m, i in select_pack_moments(history)
    ]
    return StudioPack(
        match_id=match_id,
        home=home,
        away=away,
        score=score,
        clock_ms=state.clock_ms if state is not None else None,
        facts=state_facts(state, home, away, score),
        moments=moments,
        foreign_names=tuple(foreign_names),
        version=len(history),
    )


def studio_prompt(
    pack: StudioPack,
    question: str,
    *,
    persona: str,
    language: str,
    favourite_team: Team | None = None,
    issues: Sequence[str] = (),
) -> str:
    """User prompt: QUESTION · LANGUAGE · PERSONA · MATCH · FACTS · MOMENTS · ROSTER (+ repair issues)."""
    from .prompts import REPAIR_INSTRUCTION

    if persona == "club_fan" and favourite_team is not None:
        tone = PERSONA_TONE["club_fan"].format(team=favourite_team.name)
    else:
        tone = PERSONA_TONE.get(persona, PERSONA_TONE["casual"])
    lines = []
    for pm in pack.moments:
        lines.append(f"- id {pm.moment.moment_id} · {pm.minute} · {pm.moment.type} · {pm.headline}".rstrip(" ·"))
        lines += [f"    {f.key} | {f.label} | {fmt_value(f.value)} | {f.unit or ''}".rstrip() for f in pm.facts]
    lang_name = LANGUAGE_NAMES.get(base_language(language), "English")
    sections = [
        "QUESTION (from a viewer)\n" + " ".join(question.split()),
        f"LANGUAGE\n{lang_name} ({language})",
        f"PERSONA\n{tone}",
        f"MATCH\n{pack.home.name} (home) vs {pack.away.name} (away), score {pack.score}",
        "FACTS (the match so far)\n" + format_facts_table(pack.facts),
        "MOMENTS (chronological: id · minute · type · headline, then that moment's facts)\n"
        + ("\n".join(lines) or "(none yet)"),
        "ROSTER\n" + format_roster(pack.home, pack.away),
    ]
    if issues:
        sections.append(REPAIR_INSTRUCTION + "\n" + "\n".join(f"- {i}" for i in issues))
    return "\n\n".join(sections)


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

_EMPTY_SHEET = FactSheet.build([])


def screen_question(question: str, language: str = "en") -> list[Issue]:
    """Cheap pre-LLM screen: betting language or a real club/competition in the question → refuse."""
    return [
        i
        for i in _EMPTY_SHEET.check_text(question, "question", language=language)
        if i.code in ("banned_term", "denylisted_name")
    ]


def check_answer(out: StudioAnswerOutput, pack: StudioPack, language: str) -> list[Issue]:
    """Fact-check rules 1, 3, 4, 5 on the answer, plus cut-off copy, leaked ids, unknown moment ids and keys."""
    text = out.answer.strip()
    if not text:
        return [Issue("empty", "answer", "the answer is empty")]
    issues = pack.sheet.check_text(text, "answer", language=language)
    if len(text) > MAX_ANSWER:
        issues.append(Issue("too_long", "answer", f"{len(text)} chars, max {MAX_ANSWER}"))
    issues += cut_off(text, "answer")
    leaked = sorted(pid for pid in pack.player_ids | pack.moment_ids if pid in text)
    if leaked:
        issues.append(Issue("id_in_text", "answer", f"use names, not ids: {', '.join(leaked)}"))
    for mid in out.cited_moment_ids:
        if mid not in pack.moment_ids:
            issues.append(Issue("unknown_moment", "cited_moment_ids", f"'{mid}' is not one of the MOMENTS"))
    keys = pack.fact_keys
    for key in out.fact_keys:
        if key not in keys:
            issues.append(Issue("unknown_fact_key", "fact_keys", f"'{key}' is not a FACT key"))
    return issues


# --------------------------------------------------------------------------- #
# The agent
# --------------------------------------------------------------------------- #

type _Key = tuple[str, str, str, str, str, int]


class StudioAnalyst:
    """Answers viewer questions from a `StudioPack`, with a bounded LRU cache and in-flight sharing."""

    def __init__(
        self, llm: LLMClient, config: OrchestratorConfig | None = None, *, cache_size: int = CACHE_SIZE
    ) -> None:
        self.llm = llm
        self.config = config or OrchestratorConfig()
        self.cache_size = cache_size
        self._cache: OrderedDict[_Key, AskAnswer] = OrderedDict()
        self._inflight: dict[_Key, asyncio.Task[AskAnswer]] = {}

    def reply(
        self,
        match_id: str,
        question: str,
        *,
        persona: str = "casual",
        language: str = "en",
        out_of_scope: bool = False,
        latency_ms: int | None = None,
    ) -> AskAnswer:
        """The safe deterministic reply (`answerable=false`, `fallback`): no LLM, nothing to fact-check."""
        lang = answer_language(language)
        return AskAnswer(
            match_id=match_id,
            question=question,
            answer=_reply(OUT_OF_SCOPE_REPLY if out_of_scope else SAFE_REPLY, lang),
            answerable=False,
            check_status=CheckStatus.FALLBACK,
            persona=Persona(persona),
            language=lang,
            latency_ms=latency_ms,
        )

    async def ask(
        self,
        pack: StudioPack,
        question: str,
        *,
        persona: Persona | str = Persona.CASUAL,
        language: str = "en",
        favourite_team_id: str | None = None,
    ) -> AskAnswer:
        """Answer `question` from `pack` in `language` and the persona's tone. Never raises."""
        started = time.monotonic()
        persona = str(Persona(persona))
        lang = answer_language(language)
        question = " ".join(question.split())
        if screen_question(question, lang):
            log.info("studio %s: refused without the LLM: %r", pack.match_id, question[:80])
            return self.reply(pack.match_id, question, persona=persona, language=lang, out_of_scope=True, latency_ms=0)
        fav = next((t for t in (pack.home, pack.away) if t.team_id == favourite_team_id), None)
        fav_key = fav.team_id if fav is not None and persona == "club_fan" else ""
        key: _Key = (pack.match_id, normalise_question(question), lang, persona, fav_key, pack.version)
        if (hit := self._cache.get(key)) is not None:
            self._cache.move_to_end(key)
            return hit.model_copy(update={"question": question, "latency_ms": _ms(started)})
        task = self._inflight.get(key)
        if task is None:  # concurrent requests for the same key share one LLM call
            task = asyncio.create_task(self._answer(key, pack, question, persona, lang, fav))
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        answer = await asyncio.shield(task)
        return answer.model_copy(update={"question": question, "latency_ms": _ms(started)})

    async def _answer(
        self, key: _Key, pack: StudioPack, question: str, persona: str, lang: str, fav: Team | None
    ) -> AskAnswer:
        deadline = time.monotonic() + self.config.total_budget_s
        issues: list[str] = []
        status = CheckStatus.PASSED
        for attempt in (1, 2):
            try:
                out = await call_llm(
                    self.llm,
                    STUDIO_ANALYST,
                    user=studio_prompt(
                        pack, question, persona=persona, language=lang, favourite_team=fav, issues=issues
                    ),
                    schema=StudioAnswerOutput,
                    config=self.config,
                    deadline=deadline,
                )
            except LLMTimeout as e:
                log.warning("studio %s timed out: %s", pack.match_id, e)
                break
            except Exception as e:  # noqa: BLE001
                log.warning("studio %s failed (attempt %d): %s", pack.match_id, attempt, e)
                issues, status = [], CheckStatus.REPAIRED
                continue
            problems = check_answer(out, pack, lang)
            if not problems:
                answer = AskAnswer(
                    match_id=pack.match_id,
                    question=question,
                    answer=out.answer.strip(),
                    answerable=out.answerable,
                    check_status=status,
                    cited_moment_ids=list(dict.fromkeys(out.cited_moment_ids)),
                    fact_keys=list(dict.fromkeys(out.fact_keys)),
                    persona=Persona(persona),
                    language=lang,
                )
                self._remember(key, answer)
                return answer
            issues, status = [str(p) for p in problems], CheckStatus.REPAIRED
            log.info("studio %s attempt %d failed checks: %s", pack.match_id, attempt, issues)
        log.warning(
            "studio %s: safe reply for %r (not cached, so a later request retries)", pack.match_id, question[:80]
        )
        return self.reply(pack.match_id, question, persona=persona, language=lang)

    def _remember(self, key: _Key, answer: AskAnswer) -> None:
        self._cache[key] = answer
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
