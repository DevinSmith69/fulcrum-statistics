"""Coach ("Coach's view"): on demand, cached, every point fact-checked against the coach_ facts it cites."""

import asyncio
import copy

from fulcrum.agents.coach import CoachAgent, check_coach, coach_eligible, coach_facts, fallback_view
from fulcrum.agents.prompts import COACH
from fulcrum.agents.schemas import CoachOutput
from fulcrum.agents.service import AgentService
from fulcrum.agents.templates import fallback_explanation, fallback_variants
from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import CheckStatus, Insight, MatchConfig, MomentType
from tests.agents.factories import AWAY, HOME, F, make_context, make_moment, make_store
from tests.agents.fakes import ScriptedLLM

# The goal in tests.agents.factories (Brennick, NBA, 67') conceded by Riverside Rovers, with its coach_ facts.
COACH_FACTS = [
    F("coach_defending_team", "Riverside Rovers", label="Defending team"),
    F("coach_move_origin", "misplaced pass", label="How the attacking move started"),
    F("coach_ball_won_by", "Mortlake", label="Northbridge Athletic player who won the ball"),
    F("coach_ball_lost_by", "Ravensworth", label="Riverside Rovers player who lost the ball"),
    F("coach_misplaced_pass_difficulty", 2.3, label="Difficulty of the misplaced pass (0-10)"),
    F("coach_misplaced_pass_expected_completion_pct", 77, "%", "Expected completion of the misplaced pass"),
    F("coach_move_start_minute", "66'", label="Minute the attacking move started"),
    F("coach_move_start_zone", "own third", label="Where the move started, seen from Riverside Rovers"),
    F("coach_move_passes", 7, label="Completed passes from the start of the move to the shot"),
    F("coach_move_seconds", 44, "s", "Seconds from the start of the move to the shot"),
    F("coach_nearest_defender_m", 6.2, "m", "Distance from the shooter to the nearest Riverside Rovers player"),
    F("coach_defenders_goal_side", 3, label="Riverside Rovers outfield players goal-side of the ball at the shot"),
    F("coach_chance_xg", 0.34, "xG", "Expected goals of the chance conceded"),
    F("coach_shot_distance_m", 14.2, "m", "Shot distance"),
    F("coach_chance_quality", "clear chance", label="Chance quality band"),
    F("coach_goalkeeper", "Venn", label="Riverside Rovers goalkeeper"),
    F("coach_shot_on_target", "yes", label="Shot on target"),
    F("coach_shot_speed_kmh", 98, "km/h", "Shot speed"),
]

GOOD = {
    "points": [
        {
            "text": "The move began with a misplaced pass by Ravensworth in their own third, a pass that is "
            "completed 77% of the time.",
            "fact_keys": [
                "coach_move_origin",
                "coach_ball_lost_by",
                "coach_move_start_zone",
                "coach_misplaced_pass_expected_completion_pct",
            ],
        },
        {
            "text": "Northbridge Athletic needed 7 passes and 44 seconds to turn it into a shot.",
            "fact_keys": ["coach_move_passes", "coach_move_seconds"],
        },
        {
            "text": "Brennick shot with the nearest Riverside Rovers player 6.2 m away.",
            "fact_keys": ["coach_nearest_defender_m"],
        },
    ],
    "takeaway": "When the ball is lost in the own third, getting close to the shooter quickly is what limits "
    "a chance like this one.",
}


def goal_with_coach_facts():
    moment = make_moment(MomentType.GOAL)
    moment = moment.model_copy(update={"facts": [*moment.facts, *COACH_FACTS]})
    ctx = make_context(moment)
    insight = Insight(
        match_id=ctx.moment.match_id,
        moment_id=ctx.moment.moment_id,
        clock_ms=ctx.moment.clock_ms,
        explanation=fallback_explanation(ctx),
        variants=fallback_variants(ctx),
        check_status=CheckStatus.PASSED,
    )
    return ctx, insight


def with_point(i: int, **changes) -> dict:
    out = copy.deepcopy(GOOD)
    out["points"][i].update(changes)
    return out


GOAL_CTX, GOAL_INSIGHT = goal_with_coach_facts()


async def run(llm, language="en", agent=None):
    ctx, insight = GOAL_CTX, GOAL_INSIGHT
    agent = agent or CoachAgent(llm)
    view = await agent.view(
        insight, ctx.moment, ctx.home, ctx.away, language, foreign_names=ctx.foreign_names, score="1-0"
    )
    return view, agent


async def test_checked_view_passes_and_is_cached():
    llm = ScriptedLLM({"coach": GOOD})
    view, agent = await run(llm)
    assert view is not None and view.check_status == CheckStatus.PASSED
    assert [p.text for p in view.points] == [p["text"] for p in GOOD["points"]]
    assert view.takeaway == GOOD["takeaway"] and view.language == "en"
    assert view.defending_team_id == AWAY and view.moment_type == "goal"
    assert [f.key for f in view.facts] == [f.key for f in COACH_FACTS]
    again, _ = await run(llm, agent=agent)
    assert again == view and len(llm.calls) == 1  # cached per (insight, language)
    call = llm.calls[0]
    assert call["agent"] == "coach" and call["system"] == COACH.system
    assert "LANGUAGE\nEnglish (en)" in call["user"] and "coach_misplaced_pass_expected_completion_pct" in call["user"]
    assert "Riverside Rovers defending" in call["user"] and "ROSTER" in call["user"]
    assert "wp_scorer_after" not in call["user"]  # only context + coach_ facts go to the Coach


def test_fixed_prompt_has_no_placeholders():
    assert not any(f"{{{p}}}" in COACH.system for p in ("language", "persona", "player"))


async def test_invented_number_is_repaired_then_passes():
    bad = with_point(1, text="Northbridge Athletic needed 8 passes and 44 seconds to turn it into a shot.")
    llm = ScriptedLLM({"coach": [bad, GOOD]})
    view, _ = await run(llm)
    assert view is not None and view.check_status == CheckStatus.REPAIRED
    assert "points[1].text: number '8' is not in FACTS" in llm.calls[1]["user"]


async def test_number_must_come_from_the_facts_the_point_cites():
    # 6.2 is a real fact, but this point only cites the passes/seconds facts.
    bad = with_point(1, text="Northbridge Athletic needed 7 passes and 44 seconds, finishing 6.2 m from a defender.")
    llm = ScriptedLLM({"coach": [bad, GOOD]})
    view, _ = await run(llm)
    assert view is not None and view.check_status == CheckStatus.REPAIRED
    assert "number '6.2' is not in the facts this point lists in fact_keys" in llm.calls[1]["user"]


async def test_blame_language_is_repaired():
    bad = copy.deepcopy(GOOD)
    bad["takeaway"] = "Ravensworth should have kept it simple: that mistake cost the goal."
    llm = ScriptedLLM({"coach": [bad, GOOD]})
    view, _ = await run(llm)
    assert view is not None and view.check_status == CheckStatus.REPAIRED
    repair = llm.calls[1]["user"]
    assert "'should have' judges a player" in repair and "'mistake' judges a player" in repair


async def test_unknown_or_missing_fact_keys_fail_the_check():
    ctx, _ = goal_with_coach_facts()
    out = CoachOutput.model_validate(with_point(2, fact_keys=["xg"]))
    issues = [str(i) for i in check_coach(out, ctx.moment, score="1-0", language="en")]
    assert "points[2].fact_keys: 'xg' is not a FACT key" in issues  # only context + coach_ facts are offered
    out = CoachOutput.model_validate(with_point(2, fact_keys=[]))
    issues = [str(i) for i in check_coach(out, ctx.moment, score="1-0", language="en")]
    assert any("list the coach_ fact keys" in i for i in issues)
    out = CoachOutput.model_validate(with_point(2, fact_keys=["scorer"]))  # a context key alone is not enough
    issues = [str(i) for i in check_coach(out, ctx.moment, score="1-0", language="en")]
    assert any("list the coach_ fact keys" in i for i in issues)
    out = CoachOutput.model_validate(with_point(2, fact_keys=["scorer", "coach_nearest_defender_m"]))
    assert check_coach(out, ctx.moment, score="1-0", language="en") == []


async def test_point_count_names_and_length_are_checked():
    ctx, _ = goal_with_coach_facts()
    one = {"points": GOOD["points"][:1], "takeaway": GOOD["takeaway"]}
    issues = [i.code for i in check_coach(CoachOutput.model_validate(one), ctx.moment, score="1-0", language="en")]
    assert "points" in issues
    foreign = with_point(0, text="The move began with a misplaced pass, as Arsenal often concede.")
    issues = [i.code for i in check_coach(CoachOutput.model_validate(foreign), ctx.moment, score="1-0", language="en")]
    assert "denylisted_name" in issues
    long = with_point(1, text="Northbridge Athletic needed 7 passes " + "and kept the ball " * 12 + "until the shot.")
    issues = [i.code for i in check_coach(CoachOutput.model_validate(long), ctx.moment, score="1-0", language="en")]
    assert "too_long" in issues


async def test_failing_twice_returns_none_and_is_not_cached():
    bad = with_point(1, text="Northbridge Athletic needed 8 passes and 44 seconds to turn it into a shot.")
    llm = ScriptedLLM({"coach": bad})
    view, agent = await run(llm)
    assert view is None and len(llm.calls) == 2
    llm.script["coach"] = GOOD
    view, _ = await run(llm, agent=agent)
    assert view is not None and view.check_status == CheckStatus.PASSED and len(llm.calls) == 3


async def test_llm_errors_return_none():
    view, _ = await run(ScriptedLLM({"coach": GOOD}, fail_times=2))
    assert view is None


async def test_translated_view_with_decimal_commas_is_checked_and_cached_per_language():
    es = {
        "points": [
            {
                "text": "La jugada empezó con un pase fallido de Ravensworth, "
                "un pase que se completa el 77% de las veces.",
                "fact_keys": [
                    "coach_move_origin",
                    "coach_ball_lost_by",
                    "coach_misplaced_pass_expected_completion_pct",
                ],
            },
            {
                "text": "Brennick remató con el rival más cercano a 6,2 m.",
                "fact_keys": ["coach_nearest_defender_m"],
            },
        ],
        "takeaway": "Cuando se pierde el balón en campo propio, presionar rápido al rematador limita ocasiones así.",
    }
    llm = ScriptedLLM({"coach": es})
    agent = CoachAgent(llm)
    view, _ = await run(llm, language="ES", agent=agent)
    assert view is not None and view.language == "es" and view.check_status == CheckStatus.PASSED
    assert "LANGUAGE\nSpanish (es)" in llm.calls[0]["user"]
    llm.script["coach"] = GOOD
    en, _ = await run(llm, language="en", agent=agent)
    assert en is not None and en.language == "en" and len(llm.calls) == 2


async def test_spanish_blame_words_are_caught():
    ctx, _ = goal_with_coach_facts()
    bad = copy.deepcopy(GOOD)
    bad["takeaway"] = "Ravensworth cometió un error que no debió cometer."
    issues = [i.code for i in check_coach(CoachOutput.model_validate(bad), ctx.moment, score="1-0", language="es")]
    assert issues.count("blame") == 2


async def test_concurrent_requests_share_one_call():
    llm = ScriptedLLM({"coach": GOOD}, delays={"coach": 0.05})
    ctx, insight = goal_with_coach_facts()
    agent = CoachAgent(llm)
    a, b = await asyncio.gather(
        agent.view(insight, ctx.moment, ctx.home, ctx.away, "en"),
        agent.view(insight, ctx.moment, ctx.home, ctx.away, "en"),
    )
    assert a == b and len(llm.calls) == 1


async def test_moments_without_coach_facts_cost_no_call():
    llm = ScriptedLLM({"coach": GOOD})
    ctx = make_context(make_moment(MomentType.GOAL))  # no coach_ facts
    insight = Insight(
        match_id=ctx.moment.match_id,
        moment_id=ctx.moment.moment_id,
        clock_ms=ctx.moment.clock_ms,
        explanation=fallback_explanation(ctx),
        variants=fallback_variants(ctx),
        check_status=CheckStatus.PASSED,
    )
    assert not coach_eligible(ctx.moment)
    assert await CoachAgent(llm).view(insight, ctx.moment, ctx.home, ctx.away) is None and llm.calls == []
    red = make_moment(MomentType.RED_CARD)
    assert not coach_eligible(red.model_copy(update={"facts": [*red.facts, *COACH_FACTS]}))


def test_fallback_view_lists_the_facts_without_copy():
    ctx, insight = goal_with_coach_facts()
    view = fallback_view(ctx.moment, insight, defending=AWAY)
    assert view.check_status == CheckStatus.FALLBACK and view.points == [] and view.takeaway is None
    assert view.facts == coach_facts(ctx.moment) and view.defending_team_id == AWAY


async def test_service_coach_serves_the_fallback_when_the_llm_fails():
    store = make_store()
    llm = ScriptedLLM({"coach": with_point(1, text="It took 99 passes.")})
    svc = AgentService(MemoryBus(), llm, league=store.league, consume_events=False)
    svc.register_match(MatchConfig(match_id="m1", home_team_id=HOME, away_team_id=AWAY))
    ctx, insight = goal_with_coach_facts()
    svc._insights[insight.insight_id] = (ctx.moment.model_copy(update={"match_id": "m1"}), insight, "1-0")
    view = await svc.coach(insight.insight_id, "en")
    assert view is not None and view.check_status == CheckStatus.FALLBACK and view.defending_team_id == AWAY
    assert [f.key for f in view.facts] == [f.key for f in COACH_FACTS]
    llm.script["coach"] = GOOD  # the miss was not cached: the next request gets checked copy
    view = await svc.coach(insight.insight_id, "en")
    assert view is not None and view.check_status == CheckStatus.PASSED
    assert await svc.coach("unknown", "en") is None
