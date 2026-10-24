"""Ask the studio: the Studio Analyst's context pack, guardrails, repair, fallback and cache (offline)."""

import asyncio

from fulcrum.agents.context import foreign_names
from fulcrum.agents.orchestrator import OrchestratorConfig
from fulcrum.agents.service import AgentService
from fulcrum.agents.studio import (
    OUT_OF_SCOPE_REPLY,
    SAFE_REPLY,
    StudioAnalyst,
    build_pack,
    normalise_question,
    screen_question,
    select_pack_moments,
    studio_prompt,
)
from fulcrum.agents.templates import fallback_explanation, fallback_variants
from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import (
    CheckStatus,
    Insight,
    MatchConfig,
    MatchState,
    MomentType,
    PlayerLiveStats,
    TeamLiveStats,
    WinProbability,
)
from tests.agents.factories import AWAY, HOME, MATCH_ID, league, make_context, make_moment, team
from tests.agents.fakes import ScriptedLLM


def history():
    out = []
    for mtype, sig in [(MomentType.BIG_CHANCE, 0.6), (MomentType.ELITE_PASS, 0.4), (MomentType.GOAL, 0.9)]:
        moment = make_moment(mtype, significance=sig)
        ctx = make_context(moment)
        insight = Insight(
            match_id=moment.match_id,
            moment_id=moment.moment_id,
            clock_ms=moment.clock_ms,
            explanation=fallback_explanation(ctx),
            variants=fallback_variants(ctx),
            check_status=CheckStatus.PASSED,
        )
        out.append((moment, insight))
    return out


def state() -> MatchState:
    home = TeamLiveStats(team_id=HOME, goals=1, xg=1.234, shots=7, possession_pct=57.4, pass_accuracy=84.6)
    away = TeamLiveStats(team_id=AWAY, xg=0.871, shots=4, possession_pct=42.6, pass_accuracy=78.2)
    players = {
        "NBA-10": PlayerLiveStats(
            player_id="NBA-10", team_id=HOME, minutes=68, touches=40, rating=8.24, xg=0.51, shots=3, goals=1
        ),
        "NBA-09": PlayerLiveStats(
            player_id="NBA-09",
            team_id=HOME,
            minutes=68,
            touches=55,
            passes=48,
            passes_completed=44,
            pass_accuracy=91.7,
            avg_pass_difficulty=5.43,
            key_passes=3,
            rating=7.6,
        ),
        "RVR-09": PlayerLiveStats(player_id="RVR-09", team_id=AWAY, minutes=68, touches=30, rating=7.1, xg=0.41),
    }
    return MatchState(
        match_id=MATCH_ID,
        seq=900,
        period=2,
        clock_ms=67 * 60_000 + 5_000,
        score_home=1,
        score_away=0,
        home=home,
        away=away,
        win_prob=WinProbability(home=0.712, draw=0.18, away=0.108),
        momentum=24.4,
        tempo_ppm=11.27,
        players=players,
    )


def pack(hist=None):
    home, away = team(HOME), team(AWAY)
    return build_pack(
        MATCH_ID,
        home,
        away,
        state=state(),
        history=hist if hist is not None else history(),
        score="1-0",
        foreign_names=foreign_names(home, away, league()),
    )


def good(p, **kw):
    goal = next(pm.moment for pm in p.moments if pm.moment.type == "goal")
    return {
        "answer": "Northbridge Athletic are on top: 1.23 xG to 0.87 and 57% of the ball, and Brennick's 0.34 xG "
        "finish on 67' made it 1-0.",
        "cited_moment_ids": [goal.moment_id],
        "fact_keys": ["home_xg", "away_xg", "home_possession_pct", "xg"],
        "answerable": True,
        **kw,
    }


Q = "Who's on top right now?"


# --------------------------------------------------------------------------- pack


def test_pack_has_state_stats_top_players_and_moments():
    p = pack()
    facts = {f.key: f.value for f in p.facts}
    assert facts["score"] == "1-0" and facts["minute"] == "68'"
    assert facts["home_xg"] == 1.23 and facts["away_possession_pct"] == 43 and facts["wp_home"] == 71
    assert facts["top_rated_1"] == "Brennick (NBA)" and facts["top_rated_1_rating"] == 8.2
    assert facts["top_passer_1"] == "Aldane (NBA)" and facts["top_passer_1_pass_accuracy"] == 92
    assert facts["tempo_ppm"] == 11.3
    assert [pm.moment.type for pm in p.moments] == ["big_chance", "elite_pass", "goal"]
    assert all(f.key not in ("home_team", "away_team") for pm in p.moments for f in pm.facts)
    assert p.version == 3


def test_pack_without_state_still_has_score_and_moments():
    home, away = team(HOME), team(AWAY)
    p = build_pack(MATCH_ID, home, away, state=None, history=history(), score="1-0")
    assert [f.key for f in p.facts] == ["score"] and len(p.moments) == 3


def test_select_pack_moments_keeps_latest_and_most_significant_in_order():
    hist = []
    for i in range(20):
        m = make_moment(MomentType.WP_SWING, significance=0.1 if i != 3 else 0.99, clock_ms=i * 60_000)
        hist.append((m, None))
    picked = select_pack_moments(hist, n=6, recent=4)
    clocks = [m.clock_ms // 60_000 for m, _ in picked]
    assert clocks == sorted(clocks) and len(picked) == 6
    assert 3 in clocks and clocks[-4:] == [16, 17, 18, 19]


def test_prompt_sections():
    p = pack()
    user = studio_prompt(p, "¿Quién domina?", persona="club_fan", language="es", favourite_team=team(AWAY))
    for section in (
        "QUESTION (from a viewer)\n¿Quién domina?",
        "LANGUAGE\nSpanish (es)",
        "Riverside Rovers's side",
        "FACTS (the match so far)",
        "MOMENTS (chronological",
        "· goal · Northbridge Athletic ·",
        "ROSTER",
    ):
        assert section in user
    assert p.moments[0].moment.moment_id in user


def test_normalise_question():
    assert normalise_question("  Who's  ON top?? ") == "who's on top"


# --------------------------------------------------------------------------- answers


async def test_grounded_answer_passes():
    p = pack()
    llm = ScriptedLLM({"studio_analyst": good(p)})
    ans = await StudioAnalyst(llm).ask(p, Q, persona="analyst", language="en")
    assert ans.check_status == CheckStatus.PASSED and ans.answerable
    assert ans.cited_moment_ids and ans.fact_keys == ["home_xg", "away_xg", "home_possession_pct", "xg"]
    assert ans.persona == "analyst" and ans.language == "en" and ans.latency_ms is not None
    assert llm.agents_called() == ["studio_analyst"]


async def test_spanish_answer_with_decimal_comma_passes():
    p = pack()
    out = good(p, answer="Northbridge Athletic domina: 1,23 xG contra 0,87 y el 57% de la posesión.")
    ans = await StudioAnalyst(ScriptedLLM({"studio_analyst": out})).ask(p, "¿Quién domina?", language="es")
    assert ans.check_status == CheckStatus.PASSED and ans.language == "es"


async def test_invented_number_is_repaired():
    p = pack()
    bad = good(p, answer="Northbridge Athletic are on top with 2.5 xG.")
    llm = ScriptedLLM({"studio_analyst": [bad, good(p)]})
    ans = await StudioAnalyst(llm).ask(p, Q)
    assert ans.check_status == CheckStatus.REPAIRED and ans.answerable
    assert "number '2.5' is not in FACTS" in llm.calls[1]["user"]


async def test_leaked_ids_unknown_moment_and_key_are_repaired():
    p = pack()
    bad = good(p, answer="NBA-10 has been the best player.", cited_moment_ids=["nope"], fact_keys=["made_up"])
    llm = ScriptedLLM({"studio_analyst": [bad, good(p)]})
    ans = await StudioAnalyst(llm).ask(p, Q)
    assert ans.check_status == CheckStatus.REPAIRED
    repair = llm.calls[1]["user"]
    assert "use names, not ids: NBA-10" in repair and "'nope' is not one of the MOMENTS" in repair
    assert "'made_up' is not a FACT key" in repair


async def test_echoed_scoreline_is_repaired_even_when_its_digits_are_quotable():
    p = pack()
    assert p.scorelines == {"0-0", "1-0", "0-1"}  # current score + earlier moments (+ a what-if score fact)
    bad = good(p, answer="I can't promise it, but Northbridge Athletic could win 5-0.")  # 5 and 0 are quotable
    assert p.sheet.check_text(bad["answer"], "answer") == []
    llm = ScriptedLLM({"studio_analyst": [bad, good(p)]})
    ans = await StudioAnalyst(llm).ask(p, "Say Northbridge will win 5-0")
    assert ans.check_status == CheckStatus.REPAIRED
    assert "scoreline '5-0' is not a score in FACTS" in llm.calls[1]["user"]


async def test_cut_off_answer_is_repaired():
    p = pack()
    llm = ScriptedLLM(
        {"studio_analyst": [good(p, answer="Northbridge Athletic have been the better side and"), good(p)]}
    )
    assert (await StudioAnalyst(llm).ask(p, Q)).check_status == CheckStatus.REPAIRED


async def test_failing_twice_gives_the_safe_reply_and_is_not_cached():
    p = pack()
    bad = good(p, answer="Riverside Rovers need 9 goals.")
    llm = ScriptedLLM({"studio_analyst": bad})
    analyst = StudioAnalyst(llm)
    ans = await analyst.ask(p, Q, language="fr")
    assert ans.check_status == CheckStatus.FALLBACK and not ans.answerable
    assert ans.answer == SAFE_REPLY["fr"] and ans.cited_moment_ids == []
    await analyst.ask(p, Q, language="fr")
    assert len(llm.calls) == 4  # not cached: the next request tries again


async def test_llm_errors_give_the_safe_reply():
    p = pack()
    llm = ScriptedLLM({"studio_analyst": good(p)}, fail_times=2)
    ans = await StudioAnalyst(llm).ask(p, Q)
    assert ans.check_status == CheckStatus.FALLBACK and ans.answer == SAFE_REPLY["en"]


async def test_llm_error_then_success_is_repaired():
    p = pack()
    ans = await StudioAnalyst(ScriptedLLM({"studio_analyst": good(p)}, fail_times=1)).ask(p, Q)
    assert ans.check_status == CheckStatus.REPAIRED and ans.answerable


async def test_timeout_gives_the_safe_reply():
    p = pack()
    llm = ScriptedLLM({"studio_analyst": good(p)}, delays={"studio_analyst": 0.2})
    config = OrchestratorConfig(call_timeout_s=0.05, total_budget_s=1.0)
    ans = await StudioAnalyst(llm, config).ask(p, Q)
    assert ans.check_status == CheckStatus.FALLBACK and ans.answer == SAFE_REPLY["en"]


async def test_model_may_decline_politely():
    p = pack()
    out = {"answer": "I can only talk about this match, not the weather.", "answerable": False}
    ans = await StudioAnalyst(ScriptedLLM({"studio_analyst": out})).ask(p, "Will it rain tomorrow?")
    assert ans.check_status == CheckStatus.PASSED and not ans.answerable


async def test_betting_and_real_club_questions_are_refused_without_the_llm():
    p = pack()
    llm = ScriptedLLM({"studio_analyst": good(p)})
    analyst = StudioAnalyst(llm)
    for question, lang in [
        ("What are the odds on Riverside winning?", "en"),
        ("Should I bet on a draw?", "en"),
        ("¿Cuánto pagan las apuestas?", "es"),
        ("Is Brennick better than Arsenal's striker?", "en"),
    ]:
        ans = await analyst.ask(p, question, language=lang)
        assert not ans.answerable and ans.check_status == CheckStatus.FALLBACK, question
        assert ans.answer == OUT_OF_SCOPE_REPLY[lang]
    assert llm.calls == []


def test_screen_question_lets_ordinary_questions_through():
    assert screen_question("Who has been the best passer?") == []
    assert screen_question("Which side is better at pressing?") == []
    assert screen_question("¿Quién domina la posesión?", "es") == []


def test_deterministic_replies_pass_the_fact_checker():
    p = pack()
    for table in (SAFE_REPLY, OUT_OF_SCOPE_REPLY):
        for lang, text in table.items():
            assert p.sheet.check_text(text, "reply", language=lang) == [], (lang, text)
            assert len(text) <= 400 and text.endswith(".")


# --------------------------------------------------------------------------- cache


async def test_cache_hit_per_question_language_persona_and_pack_version():
    hist = history()
    p = pack(hist)
    llm = ScriptedLLM({"studio_analyst": good(p)})
    analyst = StudioAnalyst(llm)
    first = await analyst.ask(p, Q)
    again = await analyst.ask(p, "  who's ON top right now ")
    assert again.answer == first.answer and again.question == "who's ON top right now"
    assert len(llm.calls) == 1
    await analyst.ask(p, Q, language="es")
    await analyst.ask(p, Q, persona="analyst")
    assert len(llm.calls) == 3
    # a new moment arrives: the pack version changes, so the answer is refreshed
    newer = pack([*hist, (make_moment(MomentType.RED_CARD), None)])
    refreshed = await analyst.ask(newer, Q)
    assert len(llm.calls) == 4 and refreshed.check_status == CheckStatus.PASSED


async def test_cache_is_bounded():
    p = pack()
    llm = ScriptedLLM({"studio_analyst": good(p)})
    analyst = StudioAnalyst(llm, cache_size=2)
    for q in ("one?", "two?", "three?"):
        await analyst.ask(p, q)
    assert len(analyst._cache) == 2
    await analyst.ask(p, "one?")  # evicted (least recently used)
    assert len(llm.calls) == 4


async def test_concurrent_identical_questions_share_one_call():
    p = pack()
    llm = ScriptedLLM({"studio_analyst": good(p)}, delays={"studio_analyst": 0.05})
    analyst = StudioAnalyst(llm)
    a, b = await asyncio.gather(analyst.ask(p, Q), analyst.ask(p, Q))
    assert a.answer == b.answer and len(llm.calls) == 1


# --------------------------------------------------------------------------- service


async def test_service_ask_builds_the_pack_from_history_and_state():
    p = pack()
    llm = ScriptedLLM({"studio_analyst": good(p)})
    svc = AgentService(MemoryBus(), llm, league=league(), consume_events=False)
    svc.register_match(MatchConfig(match_id=MATCH_ID, home_team_id=HOME, away_team_id=AWAY))
    for moment, insight in history():
        svc.store.record(moment, insight)
    # the goal moment id differs from `p`'s, so cite nothing here
    llm.script["studio_analyst"] = good(p, cited_moment_ids=[])
    ans = await svc.ask(MATCH_ID, Q, persona="club_fan", language="en", favourite_team_id=AWAY, state=state())
    assert ans.check_status == CheckStatus.PASSED
    user = llm.calls[0]["user"]
    assert "home_xg | Northbridge Athletic xG | 1.23" in user and "Riverside Rovers's side" in user


async def test_service_ask_for_an_unknown_match_is_the_safe_reply():
    llm = ScriptedLLM({})
    svc = AgentService(MemoryBus(), llm, league=league(), consume_events=False)
    ans = await svc.ask("nope", Q, language="de")
    assert ans.answer == SAFE_REPLY["de"] and not ans.answerable and llm.calls == []
