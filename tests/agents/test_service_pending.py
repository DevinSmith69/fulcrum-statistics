"""Review fixes: moments that beat their teams, translation score parity, recap fallback details."""

from fulcrum.agents.recap import RecapService
from fulcrum.contracts import (
    TOPIC_EVENTS,
    TOPIC_INSIGHTS,
    TOPIC_MOMENTS,
    CheckStatus,
    EventType,
    MatchConfig,
    MatchEvent,
    MomentType,
)
from fulcrum.llm import FakeLLM
from tests.agents.factories import AWAY, HOME, F, make_context, make_moment
from tests.agents.fakes import ScriptedLLM
from tests.agents.test_service import drain_topic, settle, started


def teamless(match_id: str, mtype=MomentType.GOAL):
    base = make_moment(mtype)
    facts = [f for f in base.facts if f.key not in ("home_team", "away_team")]
    return make_moment(mtype, match_id=match_id, facts=facts)


async def test_moment_before_register_match_is_held_then_processed():
    bus, svc = await started(FakeLLM())
    moment = teamless("m-late")
    await bus.publish(TOPIC_MOMENTS, "m-late", moment)
    await settle(svc)
    assert drain_topic(bus, TOPIC_INSIGHTS, "test") == [] and svc.pending("m-late") == [moment]
    svc.register_match(MatchConfig(match_id="m-late", home_team_id=HOME, away_team_id=AWAY))
    await settle(svc)
    insights = [e for e in drain_topic(bus, TOPIC_INSIGHTS, "test") if e.kind == "insight"]
    await svc.stop()
    assert [e.data.moment_id for e in insights] == [moment.moment_id] and svc.pending("m-late") == []


async def test_moment_before_match_start_event_is_released_by_it():
    bus, svc = await started(FakeLLM())
    await bus.publish(TOPIC_MOMENTS, "m-ev", teamless("m-ev"))
    await settle(svc)
    start = MatchEvent(
        match_id="m-ev",
        seq=0,
        period=1,
        clock_ms=0,
        type=EventType.MATCH_START,
        extra={"home_team_id": HOME, "away_team_id": AWAY},
    )
    await bus.publish(TOPIC_EVENTS, "m-ev", start)
    await settle(svc)
    kinds = [e.kind for e in drain_topic(bus, TOPIC_INSIGHTS, "test")]
    await svc.stop()
    assert kinds.count("insight") == 1


async def test_translation_is_checked_with_the_explain_time_score():
    # a wp_swing with no score fact at 4-1: the English copy may quote 4-1, so the translation may too
    llm = ScriptedLLM({})
    bus, svc = await started(llm)
    svc.register_match(MatchConfig(match_id="m-s", home_team_id=HOME, away_team_id=AWAY))
    goal = MatchEvent(
        match_id="m-s", seq=5, period=2, clock_ms=50 * 60_000, type=EventType.GOAL, score_home=4, score_away=1
    )
    await bus.publish(TOPIC_EVENTS, "m-s", goal)
    await settle(svc)
    base = make_moment(MomentType.WP_SWING)
    facts = [f for f in base.facts if f.key not in ("score", "home_team", "away_team")]
    await bus.publish(TOPIC_MOMENTS, "m-s", make_moment(MomentType.WP_SWING, match_id="m-s", facts=facts))
    await settle(svc)
    insight = next(e.data for e in drain_topic(bus, TOPIC_INSIGHTS, "test") if e.kind == "insight")
    es = [
        {**v.model_dump(mode="json"), "language": "es", "body": "Va 4-1 y Riverside Rovers sube al 33%."}
        for v in insight.variants
    ]
    llm.script["translator"] = {"variants": es}
    out = await svc.translate(insight.insight_id, "es")
    await svc.stop()
    assert out[0].language == "es"


async def test_recap_fallback_names_player_from_id_and_is_not_cached():
    ft = make_moment(MomentType.FULL_TIME)
    facts = [F("top_player_1", "NBA-10") if f.key == "top_player_1" else f for f in ft.facts]
    ctx = make_context(make_moment(MomentType.FULL_TIME, facts=facts))
    svc = RecapService(FakeLLM())
    kwargs = dict(
        full_time=ctx.moment, history=[], home=ctx.home, away=ctx.away, score=ctx.score, foreign_names=ctx.foreign_names
    )
    recap = await svc.recap(**kwargs)
    assert recap.check_status == CheckStatus.FALLBACK
    assert "Standout performer: Brennick." in recap.summary and "NBA-10" not in recap.summary
    assert svc.cached(ctx.moment.match_id, "casual", "en") is None
