"""API gateway tests (API_CONTRACT §2–3) with TestClient on an in-process memory bus.

Simulator and agents are not run (`run_sim=False`, `run_agents=False`); tests publish the hand-made fixture
onto the bus themselves, so the real engine → hub → REST/WS path is exercised end to end.
"""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fulcrum.api.app import create_app
from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import (
    TOPIC_EVENTS,
    TOPIC_INSIGHTS,
    TOPIC_TRACKING,
    CheckStatus,
    Explanation,
    Insight,
    InsightsEnvelope,
    InsightVariant,
    MatchEvent,
    OverlayCue,
    OverlayTemplate,
    Persona,
    Recap,
)
from fulcrum.engine.replay import read_jsonl

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "handmade_nba_rvr.jsonl"
MID = "fixture-nba-rvr"
CONFIG = {"match_id": MID, "home_team_id": "NBA", "away_team_id": "RVR", "seed": 7}


@pytest.fixture
def client():
    bus = MemoryBus()
    app = create_app(bus, in_process=True, run_sim=False, run_agents=False)
    with TestClient(app) as c:
        c.bus = bus
        yield c


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def publish(c: TestClient, topic: str, msg) -> None:
    c.portal.call(c.bus.publish, topic, MID, msg)


def publish_fixture(c: TestClient, upto: int | None = None) -> list:
    items = list(read_jsonl(FIXTURE))[:upto]
    for item in items:
        publish(c, TOPIC_EVENTS if isinstance(item, MatchEvent) else TOPIC_TRACKING, item)
    return items


def make_insight(moment_id: str = "m-1", clock_ms: int = 900_000) -> Insight:
    def v(persona, lang="en", team=None, player=None, tag=""):
        return InsightVariant(
            persona=persona,
            language=lang,
            perspective_team_id=team,
            perspective_player_id=player,
            headline=f"{persona}{tag} headline",
            body=f"{persona}{tag} body",
            commentary_line=f"{persona}{tag} line",
        )

    return Insight(
        match_id=MID,
        moment_id=moment_id,
        clock_ms=clock_ms,
        explanation=Explanation(
            headline="Brennick breaks the deadlock",
            why_it_matters="x",
            tactical_tag="turning_point",
            key_fact_keys=["xg"],
        ),
        variants=[
            v(Persona.ANALYST),
            v(Persona.CASUAL),
            v(Persona.CASUAL, "fr", tag="-fr"),
            v(Persona.CLUB_FAN, team="NBA", tag="-nba"),
            v(Persona.CLUB_FAN, team="RVR", tag="-rvr"),
            v(Persona.PLAYER_LENS, player="NBA-10"),
        ],
        check_status=CheckStatus.PASSED,
    )


def make_cue(persona: Persona, insight: Insight, lang: str = "en") -> OverlayCue:
    return OverlayCue(
        match_id=MID,
        moment_id=insight.moment_id,
        insight_id=insight.insight_id,
        template=OverlayTemplate.LOWER_THIRD,
        priority=5,
        sync_clock_ms=insight.clock_ms,
        sync_seq=10,
        show_at_clock_ms=insight.clock_ms + 1000,
        duration_ms=8000,
        persona=persona,
        language=lang,
        headline=f"{persona}-{lang} cue",
    )


def create_match(c: TestClient) -> None:
    r = c.post("/api/matches", json=CONFIG)
    assert r.status_code == 201, r.text
    assert r.json() == {"match_id": MID}


# --------------------------------------------------------------------------- REST


def test_health_and_teams(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["bus"] == "memory" and h["llm"] == "fake"
    teams = client.get("/api/teams").json()
    assert {t["team_id"] for t in teams} == {"NBA", "RVR", "HLC", "MDU"}


def test_create_match_validation_and_listing(client):
    r = client.post("/api/matches", json={"home_team_id": "NBA", "away_team_id": "XXX"})
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["status"] == 422 and "XXX" in r.json()["detail"]
    assert client.post("/api/matches", json={"home_team_id": "NBA"}).status_code == 422  # pydantic validation
    r = client.post("/api/matches", json={"home_team_id": "NBA", "away_team_id": "RVR"})
    assert r.status_code == 201 and len(r.json()["match_id"]) == 32  # generated id
    create_match(client)
    assert client.post("/api/matches", json=CONFIG).status_code == 409
    rows = {m["match_id"]: m for m in client.get("/api/matches").json()}
    assert rows[MID] == {
        "match_id": MID,
        "home_team_id": "NBA",
        "away_team_id": "RVR",
        "status": "scheduled",
        "clock_ms": 0,
        "score": {"home": 0, "away": 0},
    }


def test_unknown_match_is_problem_404(client):
    r = client.get("/api/matches/nope/state")
    assert r.status_code == 404
    assert r.json()["title"] == "Not Found" and "nope" in r.json()["detail"]


def test_full_match_through_rest(client):
    create_match(client)
    assert client.get(f"/api/matches/{MID}/state").status_code == 404  # not started yet
    items = publish_fixture(client)
    wait_for(lambda: client.get("/api/matches").json()[0]["status"] == "finished")
    wait_for(lambda: len(client.get(f"/api/matches/{MID}/moments").json()) >= 12)

    state = client.get(f"/api/matches/{MID}/state").json()
    assert (state["score_home"], state["score_away"]) == (1, 1)
    assert state["home"]["team_id"] == "NBA"

    events = client.get(f"/api/matches/{MID}/events").json()
    assert len(events) == sum(isinstance(i, MatchEvent) for i in items)
    tail = client.get(f"/api/matches/{MID}/events", params={"since_seq": len(events) - 3}).json()
    assert [e["seq"] for e in tail] == [len(events) - 2, len(events) - 1]
    assert any("pass" in e and e["pass"] for e in events)  # alias preserved

    wp = client.get(f"/api/matches/{MID}/winprob").json()
    assert len(wp) > 100 and all(abs(p["home"] + p["draw"] + p["away"] - 1) < 1e-6 for p in wp)
    types = {m["type"] for m in client.get(f"/api/matches/{MID}/moments").json()}
    assert {"goal", "red_card", "big_chance", "full_time"} <= types

    # recap: finished but nothing generated yet → 404; then the agents publish one
    assert client.get(f"/api/matches/{MID}/recap").status_code == 404
    recap = Recap(
        match_id=MID,
        persona=Persona.CASUAL,
        title="Honours even",
        summary="1-1",
        key_moment_ids=[],
        check_status=CheckStatus.FALLBACK,
    )
    publish(client, TOPIC_INSIGHTS, InsightsEnvelope.wrap(recap))
    wait_for(lambda: client.get(f"/api/matches/{MID}/recap").status_code == 200)
    got = client.get(f"/api/matches/{MID}/recap", params={"persona": "analyst", "language": "de"}).json()
    assert got["title"] == "Honours even"  # falls back to the casual English recap


def test_insight_and_cue_filtering(client):
    create_match(client)
    ins = make_insight()
    publish(client, TOPIC_INSIGHTS, InsightsEnvelope.wrap(ins))
    for persona in Persona:
        publish(client, TOPIC_INSIGHTS, InsightsEnvelope.wrap(make_cue(persona, ins)))
    publish(client, TOPIC_INSIGHTS, InsightsEnvelope.wrap(make_cue(Persona.CASUAL, ins, "fr")))
    wait_for(lambda: len(client.get(f"/api/matches/{MID}/overlay-cues").json()) == 5)

    url = f"/api/matches/{MID}/insights"
    assert len(client.get(url).json()[0]["variants"]) == 6  # unfiltered feed
    one = client.get(url, params={"persona": "analyst", "language": "en"}).json()[0]["variants"]
    assert [(v["persona"], v["language"]) for v in one] == [("analyst", "en")]
    fr = client.get(url, params={"persona": "casual", "language": "fr"}).json()[0]["variants"]
    assert fr[0]["headline"] == "casual-fr headline"
    es = client.get(url, params={"persona": "casual", "language": "es"}).json()[0]["variants"]
    assert es[0]["headline"] == "casual headline"  # translation stub → English
    fan = client.get(url, params={"persona": "club_fan", "favourite_team_id": "RVR"}).json()[0]["variants"]
    assert fan[0]["perspective_team_id"] == "RVR"
    lens = client.get(url, params={"persona": "player_lens", "followed_player_id": "NBA-10"}).json()
    assert lens[0]["variants"][0]["perspective_player_id"] == "NBA-10"

    cues = f"/api/matches/{MID}/overlay-cues"
    assert [c["headline"] for c in client.get(cues, params={"persona": "casual", "language": "fr"}).json()] == [
        "casual-fr cue"
    ]
    assert [c["headline"] for c in client.get(cues, params={"persona": "analyst", "language": "es"}).json()] == [
        "analyst-en cue"
    ]
    assert client.get(cues, params={"persona": "analyst", "since_clock_ms": 10_000_000}).json() == []

    tr = client.post(f"/api/matches/{MID}/translate", json={"insight_id": ins.insight_id, "language": "de"})
    assert tr.status_code == 200 and len(tr.json()) == 5  # stub returns the 5 English variants
    tr = client.post(f"/api/matches/{MID}/translate", json={"insight_id": ins.insight_id, "language": "fr"})
    assert [v["headline"] for v in tr.json()] == ["casual-fr headline"]
    assert client.post(f"/api/matches/{MID}/translate", json={"insight_id": "x", "language": "fr"}).status_code == 404


# --------------------------------------------------------------------------- WebSocket


def drain(ws) -> list[dict]:
    """Read everything queued so far: send a ping and collect until its pong."""
    ws.send_json({"type": "ping"})
    out = []
    while True:
        msg = ws.receive_json()
        if msg["type"] == "pong":
            return out
        out.append(msg)


def test_websocket_subscribe_flow(client):
    create_match(client)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "subscribe", "match_id": "nope"})
        assert ws.receive_json()["payload"]["code"] == "unknown_match"
        ws.send_text("not json")
        assert ws.receive_json()["payload"]["code"] == "bad_request"

        prefs = {"persona": "casual", "language": "en", "favourite_team_id": "NBA", "followed_player_id": "NBA-10"}
        ws.send_json({"type": "subscribe", "match_id": MID, "prefs": prefs})
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["match_id"] == MID
        assert hello["payload"]["home"]["team_id"] == "NBA" and hello["payload"]["state"] is None

        items = publish_fixture(client, upto=500)
        last_seq = max(i.seq for i in items if isinstance(i, MatchEvent))
        wait_for(lambda: client.get(f"/api/matches/{MID}/events", params={"since_seq": last_seq - 1}).json())
        ins = make_insight()
        publish(client, TOPIC_INSIGHTS, InsightsEnvelope.wrap(ins))
        for persona in Persona:
            publish(client, TOPIC_INSIGHTS, InsightsEnvelope.wrap(make_cue(persona, ins)))
        wait_for(lambda: len(client.get(f"/api/matches/{MID}/overlay-cues").json()) == 4)

        msgs = drain(ws)
        kinds = [m["type"] for m in msgs]
        assert kinds.count("event") == sum(isinstance(i, MatchEvent) for i in items)
        assert "state" in kinds and "moment" in kinds
        insight = next(m for m in msgs if m["type"] == "insight")["payload"]
        assert [v["persona"] for v in insight["variants"]] == ["casual"]
        cues = [m["payload"] for m in msgs if m["type"] == "cue"]
        assert {c["template"] for c in cues} == {"lower_third", "player_tag"}
        assert all(c["persona"] == "casual" for c in cues)
        tags = [c for c in cues if c["template"] == "player_tag"]
        assert tags and all(c["player_id"] == "NBA-10" and "Brennick" in c["headline"] for c in tags)
        states = [m["payload"] for m in msgs if m["type"] == "state"]
        assert all(b["clock_ms"] >= a["clock_ms"] for a, b in zip(states, states[1:], strict=False))

        # switching persona re-sends the snapshot, filtered for the new persona
        ws.send_json({"type": "update_prefs", "prefs": {"persona": "analyst", "language": "es"}})
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["payload"]["state"]["seq"] > 0 and hello["payload"]["moments"]
        assert [v["persona"] for v in hello["payload"]["insights"][0]["variants"]] == ["analyst"]

        publish_fixture_rest(client, start=500)
        wait_for(lambda: client.get("/api/matches").json()[0]["status"] == "finished")
        msgs = drain(ws)
        end = next(m for m in msgs if m["type"] == "match_end")
        assert end["payload"] == {"score_home": 1, "score_away": 1}
        assert not [m for m in msgs if m["type"] == "cue" and m["payload"]["template"] == "player_tag"]


def publish_fixture_rest(c: TestClient, start: int) -> None:
    for item in list(read_jsonl(FIXTURE))[start:]:
        publish(c, TOPIC_EVENTS if isinstance(item, MatchEvent) else TOPIC_TRACKING, item)


def test_dev_mode_post_starts_pipeline_with_replay_fallback(monkeypatch):
    """No simulator importable yet → POST /api/matches replays a recorded match through the real engine."""
    monkeypatch.setenv("FULCRUM_REPLAY_FILE", str(FIXTURE))
    app = create_app(MemoryBus(), in_process=True, run_sim=True, run_agents=False)
    with TestClient(app) as c:
        health = c.get("/api/health").json()
        assert health["in_process"] is True
        r = c.post("/api/matches", json={**CONFIG, "match_id": "dev-1", "speed": 100000})
        assert r.status_code == 201
        wait_for(lambda: c.get("/api/matches").json()[0]["status"] == "finished", timeout=30)
        wait_for(lambda: c.get("/api/matches/dev-1/state").json()["clock_ms"] > 90 * 60_000)
        state = c.get("/api/matches/dev-1/state").json()
        assert (state["match_id"], state["score_home"], state["score_away"]) == ("dev-1", 1, 1)
        assert len(c.get("/api/matches/dev-1/moments").json()) >= 12


class CountingAgents:
    """Stand-in for AgentService's on-demand hooks."""

    def __init__(self) -> None:
        self.translate_calls = 0

    async def translate(self, insight_id, language):
        self.translate_calls += 1
        return []  # → English fallback

    async def translate_cues(self, insight_id, language):
        return []

    async def recap(self, match_id, persona, language):
        return Recap(
            match_id=match_id,
            persona=persona,
            language=language,
            title=f"{persona}-{language} recap",
            summary="s",
            key_moment_ids=[],
            check_status=CheckStatus.PASSED,
        )


def test_hello_after_full_time_bounds_translation_and_sends_recap(client):
    create_match(client)
    agents = CountingAgents()
    client.app.state.translator.agents = agents
    for k in range(8):
        publish(client, TOPIC_INSIGHTS, InsightsEnvelope.wrap(make_insight(f"m-{k}", 60_000 * k)))
    casual = Recap(
        match_id=MID,
        persona=Persona.CASUAL,
        title="casual-en recap",
        summary="s",
        key_moment_ids=[],
        check_status=CheckStatus.FALLBACK,
    )
    publish(client, TOPIC_INSIGHTS, InsightsEnvelope.wrap(casual))
    publish(client, TOPIC_EVENTS, MatchEvent(match_id=MID, seq=0, period=2, clock_ms=5_700_000, type="match_end"))
    wait_for(lambda: client.get("/api/matches").json()[0]["status"] == "finished")
    wait_for(lambda: len(client.get(f"/api/matches/{MID}/insights").json()) == 8)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "subscribe", "match_id": MID, "prefs": {"persona": "analyst", "language": "es"}})
        hello = ws.receive_json()
        assert hello["type"] == "hello" and len(hello["payload"]["insights"]) == 8
        assert agents.translate_calls == 5  # only the newest insights are translated on demand
        recap = ws.receive_json()
        assert recap["type"] == "recap" and recap["payload"]["title"] == "analyst-es recap"  # lazily generated
    assert (
        client.get(f"/api/matches/{MID}/recap", params={"persona": "analyst", "language": "es"}).json()["title"]
        == "analyst-es recap"
    )


def test_replay_relabels_recording_to_requested_teams(monkeypatch):
    """Replaying the NBA v RVR recording for an HLC v MDU match gives HLC/MDU ids and the right score."""
    monkeypatch.setenv("FULCRUM_REPLAY_FILE", str(FIXTURE))
    app = create_app(MemoryBus(), in_process=True, run_sim=True, run_agents=False)
    with TestClient(app) as c:
        cfg = {"match_id": "swap", "home_team_id": "HLC", "away_team_id": "MDU", "speed": 100000}
        assert c.post("/api/matches", json=cfg).status_code == 201
        wait_for(lambda: c.get("/api/matches").json()[0]["status"] == "finished", timeout=30)
        wait_for(lambda: c.get("/api/matches/swap/state").json()["clock_ms"] > 90 * 60_000)
        s = c.get("/api/matches/swap/state").json()
        assert (s["home"]["team_id"], s["away"]["team_id"], s["score_home"], s["score_away"]) == ("HLC", "MDU", 1, 1)
        assert s["home"]["shots"] == 8 and s["away"]["shots"] == 2
        assert "HLC-10" in s["players"] and not any(p.startswith("NBA") for p in s["players"])


def test_recording_teams_detected_from_tracking(tmp_path):
    from fulcrum.api.pipeline import recording_teams

    assert recording_teams(FIXTURE) == ("NBA", "RVR")  # from match_start.extra
    frames_only = tmp_path / "frames.jsonl"
    frames_only.write_text("\n".join(row for row in FIXTURE.read_text().splitlines() if '"tracking-frames"' in row))
    assert recording_teams(frames_only) == ("NBA", "RVR")  # from P1 positions
