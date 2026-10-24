import asyncio

from fulcrum.agents.service import AgentService
from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import (
    TOPIC_EVENTS,
    TOPIC_INSIGHTS,
    TOPIC_MOMENTS,
    CheckStatus,
    EventType,
    InsightsEnvelope,
    MatchConfig,
    MatchEvent,
    MomentType,
)
from fulcrum.llm import FakeLLM
from tests.agents.factories import AWAY, HOME, MATCH_ID, league, make_moment
from tests.agents.fakes import GOAL_ANALYST, ScriptedLLM, goal_variants

GOOD = {"moment_analyst": GOAL_ANALYST, "persona_editor": goal_variants()}


def drain_topic(bus: MemoryBus, topic: str, group: str) -> list[InsightsEnvelope]:
    q = bus._queue(topic, group)
    out = []
    while not q.empty():
        out.append(InsightsEnvelope.model_validate_json(q.get_nowait()))
    return out


async def started(llm, **kw) -> tuple[MemoryBus, AgentService]:
    bus = MemoryBus()
    bus.register(TOPIC_INSIGHTS, "test")
    svc = AgentService(bus, llm, league=league(), **kw)
    await svc.start()
    return bus, svc


async def settle(svc: AgentService) -> None:
    for _ in range(5):  # let subscription tasks pick up messages, then wait for workers
        await asyncio.sleep(0)
    await asyncio.wait_for(svc.drain(), 5)


async def test_moment_in_insight_and_cues_out():
    bus, svc = await started(ScriptedLLM(GOOD))
    moment = make_moment(MomentType.GOAL)
    await bus.publish(TOPIC_MOMENTS, MATCH_ID, moment)
    await settle(svc)
    envs = drain_topic(bus, TOPIC_INSIGHTS, "test")
    await svc.stop()
    assert envs[0].kind == "insight" and envs[0].data.moment_id == moment.moment_id
    assert envs[0].data.check_status == CheckStatus.PASSED
    assert {e.kind for e in envs[1:]} == {"cue"} and len(envs) == 1 + 8 + 2  # 2 cue types × 4 personas + 2 tags
    assert svc.insight(envs[0].data.insight_id) == envs[0].data


async def test_duplicate_moments_are_processed_once():
    bus, svc = await started(ScriptedLLM(GOOD))
    moment = make_moment()
    await bus.publish(TOPIC_MOMENTS, MATCH_ID, moment)
    await bus.publish(TOPIC_MOMENTS, MATCH_ID, moment)
    await settle(svc)
    insights = [e for e in drain_topic(bus, TOPIC_INSIGHTS, "test") if e.kind == "insight"]
    await svc.stop()
    assert len(insights) == 1


async def test_events_feed_recent_sequence_and_registered_teams():
    llm = ScriptedLLM(GOOD)
    bus, svc = await started(llm)
    svc.register_match(MatchConfig(match_id="m-reg", home_team_id=HOME, away_team_id=AWAY))
    ev = MatchEvent(
        match_id="m-reg",
        seq=99,
        period=2,
        clock_ms=66 * 60_000,
        type=EventType.SHOT,
        team_id=HOME,
        player_id="NBA-10",
        score_home=1,
    )
    await bus.publish(TOPIC_EVENTS, "m-reg", ev)
    await settle(svc)
    base = make_moment(MomentType.GOAL)
    facts = [f for f in base.facts if f.key not in ("home_team", "away_team", "score")]
    await bus.publish(TOPIC_MOMENTS, "m-reg", make_moment(MomentType.GOAL, match_id="m-reg", facts=facts))
    await settle(svc)
    await svc.stop()
    user = llm.calls[0]["user"]
    assert "67' Brennick (NBA) shot" in user
    assert "Northbridge Athletic 1-0 Riverside Rovers" in user  # score from the event stream


async def test_unknown_teams_are_skipped_not_crashed(caplog):
    bus, svc = await started(FakeLLM())
    moment = make_moment(match_id="m-unknown", facts=[])
    await bus.publish(TOPIC_MOMENTS, "m-unknown", moment)
    await settle(svc)
    await svc.stop()
    assert drain_topic(bus, TOPIC_INSIGHTS, "test") == []
    assert "unknown teams for match m-unknown" in caplog.text


async def test_full_time_publishes_recap_and_on_demand_paths_work():
    bus, svc = await started(FakeLLM())  # no canned output → template fallback everywhere
    goal = make_moment(MomentType.GOAL, significance=0.9)
    ft = make_moment(MomentType.FULL_TIME, significance=1.0)
    assert await svc.recap(MATCH_ID) is None  # not before full time
    await bus.publish(TOPIC_MOMENTS, MATCH_ID, goal)
    await bus.publish(TOPIC_MOMENTS, MATCH_ID, ft)
    await settle(svc)
    envs = drain_topic(bus, TOPIC_INSIGHTS, "test")
    kinds = [e.kind for e in envs]
    assert kinds.count("insight") == 2 and kinds[-1] == "recap"
    recap = envs[-1].data
    assert recap.persona == "casual" and recap.check_status == CheckStatus.FALLBACK
    assert goal.moment_id in recap.key_moment_ids
    assert await svc.recap(MATCH_ID, "casual", "en") == recap  # cached, same content as published
    analyst = await svc.recap(MATCH_ID, "analyst", "en")
    assert analyst is not None and analyst.persona == "analyst"

    insight = envs[0].data
    variants = await svc.translate(insight.insight_id, "es")
    assert variants == insight.variants  # fake LLM cannot translate → English fallback
    assert await svc.translate("missing", "es") == []
    cues = await svc.translate_cues(insight.insight_id, "es")
    assert cues and all(c.insight_id == insight.insight_id for c in cues)
    await svc.stop()


async def test_events_can_be_disabled():
    bus, svc = await started(FakeLLM(), consume_events=False)
    assert len(svc._tasks) == 1
    await svc.stop()
