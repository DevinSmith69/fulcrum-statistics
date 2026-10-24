"""Runner (bus publishing, pacing) and CLI."""

import json
from datetime import UTC, datetime, timedelta

from fulcrum.bus.base import MemoryBus
from fulcrum.contracts import TOPIC_EVENTS, TOPIC_TRACKING, MatchConfig, MatchEvent, TrackingFrame
from fulcrum.sim import load_teams, run_match, simulate
from fulcrum.sim.__main__ import main
from fulcrum.sim.engine import KICKOFF_TS

TEAMS = load_teams()


def _config(**kw) -> MatchConfig:
    return MatchConfig(match_id="runner-test", home_team_id="HLC", away_team_id="MDU", seed=11, **kw)


async def test_run_match_publishes_everything_in_order():
    bus = MemoryBus()
    bus.register(TOPIC_EVENTS, "t")
    bus.register(TOPIC_TRACKING, "t")
    cfg = _config(emit_tracking=True)
    score = await run_match(cfg, speed=0, bus=bus)

    expected = list(simulate(cfg, TEAMS["HLC"], TEAMS["MDU"]))
    n_events = sum(isinstance(m, MatchEvent) for m in expected)
    events_q, frames_q = bus._queue(TOPIC_EVENTS, "t"), bus._queue(TOPIC_TRACKING, "t")
    assert events_q.qsize() == n_events
    assert frames_q.qsize() == len(expected) - n_events

    got = [MatchEvent.model_validate_json(events_q.get_nowait()) for _ in range(n_events)]
    assert [e.seq for e in got] == list(range(n_events))
    assert (got[-1].score_home, got[-1].score_away) == score
    TrackingFrame.model_validate_json(frames_q.get_nowait())


async def test_paced_run_restamps_ts_with_wall_clock():
    bus = MemoryBus()
    bus.register(TOPIC_EVENTS, "t")
    await run_match(_config(emit_tracking=False), speed=1e6, bus=bus)  # ~6 ms of pacing
    first = MatchEvent.model_validate_json(bus._queue(TOPIC_EVENTS, "t").get_nowait())
    assert first.ts != KICKOFF_TS
    assert abs(datetime.now(UTC) - first.ts) < timedelta(minutes=1)


def test_cli_writes_jsonl(tmp_path):
    out = tmp_path / "m.jsonl"
    assert main(["--home", "HLC", "--away", "MDU", "--seed", "11", "--no-tracking", "--out", str(out)]) == 0
    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert {r["topic"] for r in records} == {TOPIC_EVENTS}
    first = MatchEvent.model_validate(records[0]["data"])
    assert first.match_id == "hlc-mdu-s11" and first.seq == 0
    passes = [r["data"] for r in records if r["data"]["type"] == "pass"]
    assert passes and "pass" in passes[0] and "pass_" not in passes[0]


def test_cli_rejects_unknown_team(capsys):
    assert main(["--home", "XXX", "--away", "MDU", "--out", "unused.jsonl"]) == 2
    assert "unknown team" in capsys.readouterr().err
