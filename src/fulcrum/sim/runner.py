"""Runs a simulated match against the wall clock and publishes it to the bus.

`speed` is the sim-time multiplier (1 = real time, 10 = a 90-minute match in ~9.5 min, 0 = as fast as
possible). Events go to `match-events`, tracking frames to `tracking-frames`, keyed by match_id.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from fulcrum.bus import Bus, get_bus
from fulcrum.contracts import TOPIC_EVENTS, TOPIC_TRACKING, MatchConfig, MatchEvent, Team

from .control import MatchControl
from .engine import SimOutput, simulate
from .params import DEFAULT_PARAMS, SimParams
from .players import load_teams

YIELD_EVERY = 100  # at speed 0, hand control back to the event loop this often


def topic_for(item: SimOutput) -> str:
    """Bus topic for a simulator output model."""
    return TOPIC_EVENTS if isinstance(item, MatchEvent) else TOPIC_TRACKING


def _resolve_teams(config: MatchConfig, home: Team | None, away: Team | None) -> tuple[Team, Team]:
    if home is None or away is None:
        teams = load_teams()
        home = home or teams[config.home_team_id]
        away = away or teams[config.away_team_id]
    return home, away


async def run_match(
    config: MatchConfig,
    *,
    home: Team | None = None,
    away: Team | None = None,
    speed: float | None = None,
    bus: Bus | None = None,
    params: SimParams = DEFAULT_PARAMS,
    control: MatchControl | None = None,
) -> tuple[int, int]:
    """Simulate `config` and publish every model to the bus, paced by `speed` (default `config.speed`).

    When paced (speed > 0) each event's `ts` is restamped with the real publish time; at speed 0 the
    deterministic synthetic `ts` is kept. With a `control` (demo director: live speed, pause, skip to the next
    moment) the control paces the stream instead of `speed`; the items themselves are unchanged.
    Returns the final score (home, away).
    """
    home, away = _resolve_teams(config, home, away)
    speed = config.speed if speed is None else speed
    bus = bus or get_bus()
    loop = asyncio.get_running_loop()
    start = loop.time()
    score = (0, 0)
    try:
        for i, item in enumerate(simulate(config, home, away, params)):
            if control is not None:
                await control.pace(item.clock_ms)
                if isinstance(item, MatchEvent):
                    item = item.model_copy(update={"ts": datetime.now(UTC)})
            elif speed > 0:
                delay = start + item.clock_ms / 1000.0 / speed - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                if isinstance(item, MatchEvent):
                    item = item.model_copy(update={"ts": datetime.now(UTC)})
            elif i % YIELD_EVERY == 0:
                await asyncio.sleep(0)
            if isinstance(item, MatchEvent):
                score = (item.score_home, item.score_away)
            await bus.publish(topic_for(item), config.match_id, item)
    finally:
        if control is not None:
            control.finish()
    return score


def to_jsonl_line(item: SimOutput) -> str:
    """One `{"topic", "data"}` JSONL record (pass attributes under their wire alias `pass`)."""
    return f'{{"topic":{json.dumps(topic_for(item))},"data":{item.model_dump_json(by_alias=True)}}}'


def write_jsonl(path: Path | str, items: Iterable[SimOutput]) -> int:
    """Write models as JSONL (LF line endings). Returns the number of records written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for item in items:
            fh.write(to_jsonl_line(item))
            fh.write("\n")
            n += 1
    return n


def record_match(
    config: MatchConfig,
    path: Path | str,
    *,
    home: Team | None = None,
    away: Team | None = None,
    params: SimParams = DEFAULT_PARAMS,
) -> int:
    """Simulate as fast as possible straight into a JSONL fixture (no bus). Returns the record count."""
    home, away = _resolve_teams(config, home, away)
    return write_jsonl(path, simulate(config, home, away, params))
