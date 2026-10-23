import json
from pathlib import Path

import pytest

from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import (
    TOPIC_EVENTS,
    TOPIC_INSIGHTS,
    CheckStatus,
    EventType,
    Explanation,
    Fact,
    Insight,
    InsightsEnvelope,
    MatchEvent,
    Moment,
    MomentType,
    OverlayCue,
    OverlayTemplate,
    PassAttributes,
    Persona,
    Recap,
    Team,
    WinProbability,
)
from fulcrum.llm import FakeLLM

ROOT = Path(__file__).resolve().parents[2]


def test_teams_fixture_valid_and_fictional():
    teams = [Team.model_validate(t) for t in json.loads((ROOT / "data/teams.json").read_text())]
    assert len(teams) == 4
    for t in teams:
        assert len(t.players) == 18
        assert len({p.player_id for p in t.players}) == 18
    names = {p.short_name for t in teams for p in t.players}
    assert len(names) == 72, "surnames must be unique across the league (fact-checker relies on it)"


def test_pass_event_roundtrip_uses_alias():
    ev = MatchEvent(
        match_id="m1",
        seq=1,
        period=1,
        clock_ms=1000,
        type=EventType.PASS,
        team_id="NBA",
        player_id="NBA-08",
        x=40,
        y=30,
        end_x=60,
        end_y=20,
        **{
            "pass": PassAttributes(
                length_m=22.4, progressive_m=20, ball_speed_kmh=58, expected_completion=0.71, difficulty=2.9
            )
        },
    )
    raw = ev.model_dump_json(by_alias=True)
    assert '"pass"' in raw and "pass_" not in raw
    assert MatchEvent.model_validate_json(raw).pass_.difficulty == 2.9


def test_out_of_bounds_rejected():
    with pytest.raises(ValueError):
        MatchEvent(match_id="m1", seq=1, period=1, clock_ms=0, type=EventType.PASS, x=120, y=10)


async def test_memory_bus_fanout_per_group():
    bus = MemoryBus()
    bus.register(TOPIC_EVENTS, "engine")
    bus.register(TOPIC_EVENTS, "api")
    ev = MatchEvent(match_id="m1", seq=0, period=1, clock_ms=0, type=EventType.KICK_OFF)
    await bus.publish(TOPIC_EVENTS, "m1", ev)
    got_engine = await anext(bus.subscribe(TOPIC_EVENTS, MatchEvent, "engine"))
    got_api = await anext(bus.subscribe(TOPIC_EVENTS, MatchEvent, "api"))
    assert got_engine.event_id == got_api.event_id == ev.event_id


async def test_fake_llm_canned_and_failures():
    canned = {
        "moment_analyst": {
            "headline": "Swing",
            "why_it_matters": "x",
            "tactical_tag": "turning_point",
            "key_fact_keys": ["wp_home_after"],
        }
    }
    llm = FakeLLM(canned, fail_times=1)
    with pytest.raises(RuntimeError):
        await llm.structured(system="s", user="u", schema=Explanation, agent_name="moment_analyst")
    out = await llm.structured(system="s", user="u", schema=Explanation, agent_name="moment_analyst")
    assert out.tactical_tag == "turning_point"


def test_moment_requires_facts():
    wp = WinProbability(home=0.4, draw=0.3, away=0.3)
    m = Moment(
        match_id="m1",
        type=MomentType.GOAL,
        significance=0.9,
        period=2,
        clock_ms=4_000_000,
        seq_start=900,
        seq_end=905,
        wp_before=wp,
        wp_after=wp,
        facts=[Fact(key="minute", label="Minute", value="67'")],
        trigger="goal",
    )
    assert m.facts[0].value == "67'"


def _envelope_items() -> list[Insight | OverlayCue | Recap]:
    insight = Insight(
        match_id="m1",
        moment_id="mo1",
        clock_ms=4_000_000,
        explanation=Explanation(
            headline="Swing", why_it_matters="x", tactical_tag="turning_point", key_fact_keys=["minute"]
        ),
        variants=[],
        check_status=CheckStatus.PASSED,
    )
    cue = OverlayCue(
        match_id="m1",
        template=OverlayTemplate.LOWER_THIRD,
        priority=5,
        sync_clock_ms=4_000_000,
        sync_seq=900,
        show_at_clock_ms=4_002_000,
        duration_ms=8000,
        persona=Persona.CASUAL,
        headline="Goal",
    )
    recap = Recap(
        match_id="m1",
        persona=Persona.CASUAL,
        title="Full time",
        summary="x",
        key_moment_ids=["mo1"],
        check_status=CheckStatus.FALLBACK,
    )
    return [insight, cue, recap]


async def test_insights_envelope_roundtrip_over_bus():
    bus = MemoryBus()
    bus.register(TOPIC_INSIGHTS, "api")
    items = _envelope_items()
    for item in items:
        await bus.publish(TOPIC_INSIGHTS, "m1", InsightsEnvelope.wrap(item))
    sub = bus.subscribe(TOPIC_INSIGHTS, InsightsEnvelope, "api")
    got = [await anext(sub) for _ in items]
    assert [e.kind for e in got] == ["insight", "cue", "recap"]
    assert [type(e.data) for e in got] == [Insight, OverlayCue, Recap]
    assert got[1].data.cue_id == items[1].cue_id


def test_insights_envelope_rejects_mismatched_kind():
    cue = _envelope_items()[1]
    with pytest.raises(ValueError):
        InsightsEnvelope(kind="insight", data=cue)
