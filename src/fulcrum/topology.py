"""Deployment topology: which process runs the simulator, the engine and the agents.

    FULCRUM_TOPOLOGY=split      sim, engine, agents and api are separate processes on the bus (Event Hubs).
                                The api only reads the bus, so its on-demand agent features (translation,
                                Player lens, per-persona recaps, Ask the studio) fall back to English / 503.
    FULCRUM_TOPOLOGY=inprocess  everything inside the api process on the memory bus (`make dev`, cut-list demo).
    FULCRUM_TOPOLOGY=hybrid     the sim is its own process publishing to the bus (Event Hubs); the api hosts the
                                engine + agents, consuming the sim's topics with the engine/agents consumer
                                groups and publishing match-state / moments / insights back to the bus, so the
                                on-demand agent features work and Event Hubs carries every topic.

Without FULCRUM_TOPOLOGY the older switches decide (backwards compatible): FULCRUM_INPROCESS=1|0 picks
inprocess|split, and without that FULCRUM_BUS=memory (the default) means inprocess, anything else split.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum

TRUTHY = ("1", "true", "yes", "on")
_ALIASES = {"in-process": "inprocess", "in_process": "inprocess"}


class Topology(StrEnum):
    """How the services are spread over processes (see module docstring)."""

    SPLIT = "split"
    INPROCESS = "inprocess"
    HYBRID = "hybrid"

    @property
    def api_hosts_pipeline(self) -> bool:
        """The api process runs the engine and the agents (so the on-demand agent hooks are available)."""
        return self is not Topology.SPLIT

    @property
    def api_runs_sim(self) -> bool:
        """The api process runs the simulator itself (otherwise `POST /api/matches` goes to FULCRUM_SIM_URL)."""
        return self is Topology.INPROCESS

    @property
    def needs_shared_bus(self) -> bool:
        """Some service runs in another process, so the bus must cross processes (Event Hubs, not memory)."""
        return self is not Topology.INPROCESS


def parse_topology(value: str) -> Topology:
    """`split` | `inprocess` | `hybrid` (case-insensitive; `in-process` accepted). ValueError otherwise."""
    raw = value.strip().lower()
    try:
        return Topology(_ALIASES.get(raw, raw))
    except ValueError:
        allowed = "|".join(t.value for t in Topology)
        raise ValueError(f"FULCRUM_TOPOLOGY must be one of {allowed} (got {value!r})") from None


def resolve_topology(env: Mapping[str, str] | None = None) -> Topology:
    """The topology from FULCRUM_TOPOLOGY, else FULCRUM_INPROCESS, else FULCRUM_BUS (see module docstring)."""
    env = os.environ if env is None else env
    explicit = (env.get("FULCRUM_TOPOLOGY") or "").strip()
    if explicit:
        return parse_topology(explicit)
    inproc = env.get("FULCRUM_INPROCESS")
    if inproc is not None and inproc.strip():
        return Topology.INPROCESS if inproc.strip().lower() in TRUTHY else Topology.SPLIT
    return Topology.INPROCESS if env.get("FULCRUM_BUS", "memory") == "memory" else Topology.SPLIT
