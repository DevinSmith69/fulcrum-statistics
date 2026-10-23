"""Fictional team reference data (`data/teams.json`)."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from fulcrum.contracts import Team

DEFAULT_TEAMS_PATH = Path(__file__).resolve().parents[3] / "data" / "teams.json"


@lru_cache(maxsize=4)
def load_teams(path: str | None = None) -> dict[str, Team]:
    """Teams keyed by team_id. Path from the argument, `FULCRUM_TEAMS_PATH`, or the repo's data/teams.json."""
    p = Path(path or os.getenv("FULCRUM_TEAMS_PATH") or DEFAULT_TEAMS_PATH)
    if not p.exists():
        return {}
    return {t["team_id"]: Team.model_validate(t) for t in json.loads(p.read_text(encoding="utf-8"))}
