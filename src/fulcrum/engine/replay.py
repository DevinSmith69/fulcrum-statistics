"""Read recorded matches (`{"topic", "data"}` JSONL, the WS1 `--out` format) and run the engine over them."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from fulcrum.contracts import TOPIC_EVENTS, TOPIC_TRACKING, MatchEvent, TrackingFrame

from .match import MatchEngine
from .params import DEFAULT_PARAMS, EngineParams
from .teams import load_teams


def read_jsonl(path: str | Path) -> Iterator[MatchEvent | TrackingFrame]:
    """Yield events and frames in file order. Lines for other topics are skipped."""
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row["topic"] == TOPIC_EVENTS:
                yield MatchEvent.model_validate(row["data"])
            elif row["topic"] == TOPIC_TRACKING:
                yield TrackingFrame.model_validate(row["data"])


def run_file(path: str | Path, params: EngineParams = DEFAULT_PARAMS) -> MatchEngine:
    """Replay a whole recorded match through a fresh `MatchEngine` and return it."""
    teams = load_teams()
    engine: MatchEngine | None = None
    for item in read_jsonl(path):
        if engine is None:
            engine = MatchEngine(item.match_id, teams_by_id=teams, params=params)
        if isinstance(item, MatchEvent):
            engine.process_event(item)
        else:
            engine.process_frame(item)
    if engine is None:
        raise ValueError(f"{path} contains no match events")
    return engine
