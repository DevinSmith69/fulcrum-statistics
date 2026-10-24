"""Synthetic match simulator (WS1, MODEL_SPEC §1).

`simulate()` is the pure, seeded generator of contract models; `run_match()` paces it to the wall clock
and publishes to the bus; `python -m fulcrum.sim` is the CLI.
"""

from .engine import MatchSimulator, simulate  # noqa: F401
from .params import DEFAULT_PARAMS, SimParams  # noqa: F401
from .players import load_teams  # noqa: F401
from .runner import record_match, run_match  # noqa: F401
