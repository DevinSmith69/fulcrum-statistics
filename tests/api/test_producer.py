"""Producer console endpoints: the editorial gate (hold / approve / edit / kill / auto-approve) and graphics on air."""

import pytest
from fastapi.testclient import TestClient

from fulcrum.agents.context import load_teams
from fulcrum.agents.cues import build_cues
from fulcrum.agents.service import AgentService
from fulcrum.agents.templates import fallback_explanation, fallback_variants
from fulcrum.api.app import create_app
from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import (
    CheckStatus,
    Insight,
    InsightsEnvelope,
    MatchConfig,
    MomentType,
    OverlayCue,
)
from tests.agents.factories import AWAY, HOME, MATCH_ID, make_context, make_moment, team
from tests.agents.fakes import ScriptedLLM
from tests.agents.test_studio import state

MID = MATCH_ID
CONFIG = {"match_id": MID, "home_team_id": HOME, "away_team_id": AWAY, "seed": 7}
BASE = f"/api/matches/{MID}/producer"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("FULCRUM_PRODUCER_KEY", raising=False)
    app = create_app(MemoryBus(), in_process=True, run_sim=False, run_agents=False)
    with TestClient(app) as c:
        assert c.post("/api/matches", json=CONFIG).status_code == 201
        yield c


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Viewer:
    """Stands in for a WebSocket connection: records what the hub fans out."""

    match_id = MID

    def __init__(self) -> None:
        self.got: list[tuple[str, object]] = []

    def push(self, kind, item):
        self.got.append((kind, item))

    def kinds(self):
        return [k for k, _ in self.got]


def publish_goal(c: TestClient, mtype: MomentType = MomentType.GOAL) -> tuple[Insight, list[OverlayCue]]:
    """What the agents publish for one moment: the moment, its insight, then its cues."""
    hub = c.app.state.hub
    moment = make_moment(mtype)
    ctx = make_context(moment)
    insight = Insight(
        match_id=MID,
        moment_id=moment.moment_id,
        clock_ms=moment.clock_ms,
        explanation=fallback_explanation(ctx),
        variants=fallback_variants(ctx),
        check_status=CheckStatus.PASSED,
    )
    cues = build_cues(insight, moment, team(HOME), team(AWAY))
    hub.on_moment(moment)
    hub.on_envelope(InsightsEnvelope.wrap(insight))
    for cue in cues:
        hub.on_envelope(InsightsEnvelope.wrap(cue))
    return insight, cues


def watch(c: TestClient) -> Viewer:
    viewer = Viewer()
    c.app.state.hub.subscribers.setdefault(MID, set()).add(viewer)
    return viewer


def approval_on(c: TestClient, auto: int | None = None) -> dict:
    body = {"approval_required": True, **({"auto_approve_s": auto} if auto is not None else {})}
    r = c.post(f"{BASE}/settings", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def insight_ids(c: TestClient) -> list[str]:
    return [i["insight_id"] for i in c.get(f"/api/matches/{MID}/insights").json()]


# --------------------------------------------------------------------------- the gate


def test_default_is_unchanged_nothing_is_held(client):
    viewer = watch(client)
    insight, cues = publish_goal(client)
    assert insight_ids(client) == [insight.insight_id]
    assert len(client.get(f"/api/matches/{MID}/overlay-cues").json()) == len(cues)
    assert viewer.kinds().count("insight") == 1 and viewer.kinds().count("cue") == len(cues)
    state_ = client.get(BASE).json()
    assert state_["approval_required"] is False and state_["held"] == [] and state_["graphics_available"] is False


def test_approval_holds_insight_and_cues_until_approved(client):
    approval_on(client)
    viewer = watch(client)
    insight, cues = publish_goal(client)
    assert insight_ids(client) == [] and client.get(f"/api/matches/{MID}/overlay-cues").json() == []
    assert "insight" not in viewer.kinds() and "cue" not in viewer.kinds()

    held = client.get(f"{BASE}/held").json()
    assert len(held) == 1 and held[0]["insight"]["insight_id"] == insight.insight_id
    assert len(held[0]["cues"]) == len(cues) and held[0]["moment"]["type"] == "goal"
    assert held[0]["age_s"] >= 0 and held[0]["auto_release_in_s"] is None

    r = client.post(f"{BASE}/held/{insight.insight_id}/approve")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["cues_released"] == len(cues)
    assert insight_ids(client) == [insight.insight_id]
    out = client.get(f"/api/matches/{MID}/insights").json()[0]
    assert out["check_notes"][-1] == "approved by producer"
    assert len(client.get(f"/api/matches/{MID}/overlay-cues").json()) == len(cues)
    assert viewer.kinds().count("insight") == 1 and viewer.kinds().count("cue") == len(cues)
    assert client.get(f"{BASE}/held").json() == []
    assert client.get(BASE).json()["log"][0]["action"] == "approve"
    assert client.post(f"{BASE}/held/{insight.insight_id}/approve").status_code == 404


def test_kill_drops_the_item_and_its_cues(client):
    approval_on(client)
    viewer = watch(client)
    insight, cues = publish_goal(client)
    r = client.post(f"{BASE}/held/{insight.insight_id}/kill")
    assert r.status_code == 200 and r.json()["action"] == "kill"
    # a late copy of a cue for the killed insight is dropped too
    client.app.state.hub.on_envelope(InsightsEnvelope.wrap(cues[0].model_copy(update={"cue_id": "late"})))
    assert insight_ids(client) == [] and client.get(f"/api/matches/{MID}/overlay-cues").json() == []
    assert viewer.got == [k for k in viewer.got if k[0] == "moment"]
    assert client.get(BASE).json()["log"][0]["action"] == "kill"


def edit_body(insight: Insight, **update) -> dict:
    v = next(v for v in insight.variants if v.persona == "casual").model_copy(update=update)
    return {"variants": [v.model_dump(mode="json")]}


def test_edit_with_an_invented_number_is_rejected_and_stays_held(client):
    approval_on(client)
    insight, _ = publish_goal(client)
    r = client.post(
        f"{BASE}/held/{insight.insight_id}/edit", json=edit_body(insight, body="A 30-yard screamer! 1-0 to them.")
    )
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/problem+json")
    assert any("'30' is not in FACTS" in i for i in r.json()["issues"])
    assert len(client.get(f"{BASE}/held").json()) == 1 and insight_ids(client) == []


def test_valid_edit_goes_out_marked_with_rebuilt_cues(client):
    approval_on(client)
    viewer = watch(client)
    insight, _ = publish_goal(client)
    headline = "Brennick strikes on 67' and it's 1-0"
    r = client.post(f"{BASE}/held/{insight.insight_id}/edit", json=edit_body(insight, headline=headline))
    assert r.status_code == 200, r.text
    assert r.json()["ok"] and r.json()["insight"]["check_notes"][-1] == "edited by producer"
    out = client.get(f"/api/matches/{MID}/insights", params={"persona": "casual", "language": "en"}).json()[0]
    assert out["variants"][0]["headline"] == headline
    casual = client.get(f"/api/matches/{MID}/overlay-cues", params={"persona": "casual", "language": "en"}).json()
    assert any(c["headline"] == headline for c in casual)
    sent = [i for k, i in viewer.got if k == "insight"]
    assert len(sent) == 1 and "edited by producer" in sent[0].check_notes


def test_edit_reaches_the_in_process_agents(client):
    approval_on(client)
    svc = AgentService(client.app.state.bus, ScriptedLLM({}), league=load_teams(), consume_events=False)
    client.app.state.translator.agents = svc
    insight, _ = publish_goal(client)
    moment = next(m for m in client.app.state.registry.get(MID).moments if m.moment_id == insight.moment_id)
    svc._insights[insight.insight_id] = (moment, insight, "1-0")  # as if this service had published it
    r = client.post(f"{BASE}/held/{insight.insight_id}/edit", json=edit_body(insight, headline="Brennick, 1-0!"))
    assert r.status_code == 200
    assert "edited by producer" in svc.insight(insight.insight_id).check_notes


def test_auto_approve_releases_after_n_seconds(client):
    clock = Clock()
    desk = client.app.state.producer
    desk.clock = clock
    approval_on(client, auto=5)
    insight, _ = publish_goal(client)
    clock.t += 2
    held = client.get(f"{BASE}/held").json()
    assert held[0]["auto_release_in_s"] == 3.0 and held[0]["age_s"] == 2.0
    assert desk.tick() == 0
    clock.t += 3
    assert desk.tick() == 1
    assert insight_ids(client) == [insight.insight_id]
    out = client.get(f"/api/matches/{MID}/insights").json()[0]
    assert out["check_notes"][-1] == "auto-approved after 5 s"
    assert client.get(BASE).json()["log"][0]["action"] == "auto_approve"
    assert client.post(f"{BASE}/settings", json={"auto_approve_s": 0}).json()["auto_approve_s"] is None


def test_switching_approval_off_releases_everything_in_order(client):
    approval_on(client)
    first, _ = publish_goal(client)
    second, _ = publish_goal(client, MomentType.ELITE_PASS)
    assert insight_ids(client) == []
    r = client.post(f"{BASE}/settings", json={"approval_required": False})
    assert r.json()["approval_required"] is False and r.json()["held"] == []
    assert insight_ids(client) == [first.insight_id, second.insight_id]


def test_producer_key_when_configured(client, monkeypatch):
    monkeypatch.setenv("FULCRUM_PRODUCER_KEY", "s3cret")
    assert client.get(BASE).status_code == 401
    assert client.get(BASE, headers={"X-Producer-Key": "nope"}).status_code == 401
    assert client.get(BASE, headers={"X-Producer-Key": "s3cret"}).status_code == 200


def test_unknown_match_and_bad_settings(client):
    assert client.get("/api/matches/nope/producer").status_code == 404
    assert client.post(f"{BASE}/settings", json={"auto_approve_s": 9999}).status_code == 422


# --------------------------------------------------------------------------- graphics

STAT_CARD = {
    "template": "stat_card",
    "headline": "Brennick: the main threat",
    "stats": [{"fact_key": "focus_1_xg", "label": "xG"}, {"fact_key": "focus_1_shots", "label": "Shots"}],
    "team_id": HOME,
    "player_id": "NBA-10",
}


def with_agents(c: TestClient, canned=None) -> ScriptedLLM:
    llm = ScriptedLLM({"graphics_producer": canned or STAT_CARD})
    svc = AgentService(c.app.state.bus, llm, league=load_teams(), consume_events=False)
    svc.register_match(MatchConfig(**CONFIG))
    c.app.state.translator.agents = svc
    c.app.state.hub.on_state(state())
    return llm


def test_graphic_request_without_agents_is_503(client):
    r = client.post(f"{BASE}/graphics", json={"request": "pressure bar now"})
    assert r.status_code == 503


def test_graphic_preview_then_on_air_to_every_persona(client):
    with_agents(client)
    assert client.get(BASE).json()["graphics_available"] is True
    viewer = watch(client)
    r = client.post(f"{BASE}/graphics", json={"request": "stat card on Brennick's shooting"})
    assert r.status_code == 200, r.text
    prop = r.json()
    assert prop["ok"] and prop["cue"]["template"] == "stat_card" and prop["cue"]["origin"] == "producer"
    assert client.get(f"/api/matches/{MID}/overlay-cues").json() == []  # a preview is not on air

    r = client.post(f"{BASE}/graphics/{prop['proposal_id']}/air")
    assert r.status_code == 200
    cue = r.json()
    assert cue["cue_id"] != prop["cue"]["cue_id"] and cue["show_at_clock_ms"] == state().clock_ms
    for persona in ("analyst", "casual", "club_fan", "player_lens"):
        got = client.get(f"/api/matches/{MID}/overlay-cues", params={"persona": persona, "language": "en"}).json()
        assert [c["cue_id"] for c in got] == [cue["cue_id"]], persona
    assert viewer.kinds() == ["cue"]
    assert client.get(BASE).json()["log"][0]["action"] == "air"


def test_refused_graphic_cannot_go_on_air(client):
    llm = with_agents(client)
    r = client.post(f"{BASE}/graphics", json={"request": "graphic with the betting odds"})
    prop = r.json()
    assert r.status_code == 200 and not prop["ok"] and prop["refusal"] and llm.calls == []
    assert client.post(f"{BASE}/graphics/{prop['proposal_id']}/air").status_code == 409
    assert client.post(f"{BASE}/graphics/unknown/air").status_code == 404


def test_graphic_request_validation(client):
    with_agents(client)
    assert client.post(f"{BASE}/graphics", json={"request": ""}).status_code == 422
    assert client.post(f"{BASE}/graphics", json={"request": "x" * 201}).status_code == 422
