from pathlib import Path

import pytest

from fulcrum.engine.match import MatchEngine
from fulcrum.engine.replay import read_jsonl, run_file

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "handmade_nba_rvr.jsonl"


@pytest.fixture(scope="session")
def fixture_path() -> Path:
    return FIXTURE


@pytest.fixture(scope="session")
def fixture_items() -> list:
    return list(read_jsonl(FIXTURE))


@pytest.fixture(scope="session")
def replayed() -> MatchEngine:
    """The whole hand-made match run through one engine (read-only — don't mutate in tests)."""
    return run_file(FIXTURE)
