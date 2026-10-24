"""Single-process dev mode: run sim + engine + agents as asyncio tasks next to the API (FULCRUM_BUS=memory).

The simulator (WS1) and agents (WS3) are imported lazily and every call is guarded, so the API keeps
working while those packages are still stubs:
- no simulator → replay a recorded match (FULCRUM_REPLAY_FILE, else data/matches/sample_seed7.jsonl,
  else the hand-made test fixture), re-keyed to the new match_id and paced by `config.speed`;
- no agents → moments still flow, there are just no insights.
Entry points probed are listed in docs/HANDOFFS.md.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fulcrum.bus import Bus
from fulcrum.contracts import TOPIC_EVENTS, TOPIC_MOMENTS, TOPIC_TRACKING, MatchConfig, MatchEvent
from fulcrum.engine.replay import read_jsonl
from fulcrum.engine.service import EngineService

from .readmodel import Registry

log = logging.getLogger("fulcrum.api.pipeline")
ROOT = Path(__file__).resolve().parents[3]
REPLAY_CANDIDATES = [
    ROOT / "data" / "matches" / "sample_seed7.jsonl",
    ROOT / "data" / "matches" / "seed7.jsonl",
    ROOT / "tests" / "fixtures" / "handmade_nba_rvr.jsonl",
]
SIM_ENTRYPOINTS = [("fulcrum.sim.runner", "run_match"), ("fulcrum.sim.runner", "run"), ("fulcrum.sim", "run_match")]
AGENT_ENTRYPOINTS = [
    ("fulcrum.agents.service", "run"),
    ("fulcrum.agents.service", "run_agents"),
    ("fulcrum.agents", "run"),
]
MAX_REPLAY_SLEEP_S = 5.0


def find_entrypoint(candidates: list[tuple[str, str]]) -> Callable[..., Any] | None:
    """First importable callable among (module, attr) candidates; None if all are missing or broken."""
    for module, attr in candidates:
        try:
            fn = getattr(importlib.import_module(module), attr, None)
        except Exception:
            continue
        if callable(fn):
            return fn
    return None


async def call_flexible(fn: Callable[..., Any], *args: Any, bus: Bus) -> Any:
    """Call `fn(*args)` passing `bus` by keyword when its signature accepts it; await if needed."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    kwargs = {"bus": bus} if "bus" in params else {}
    out = fn(*args, **kwargs)
    return await out if inspect.isawaitable(out) else out


def replay_file() -> Path | None:
    env = os.getenv("FULCRUM_REPLAY_FILE")
    if env:
        return Path(env)
    return next((p for p in REPLAY_CANDIDATES if p.exists()), None)


async def replay(path: Path, bus: Bus, match_id: str, speed: float) -> None:
    """Publish a recorded match onto the bus under `match_id`, paced to `speed`× sim time."""
    last_clock: int | None = None
    for item in read_jsonl(path):
        clock = item.clock_ms
        if last_clock is not None and clock > last_clock:
            await asyncio.sleep(min(MAX_REPLAY_SLEEP_S, (clock - last_clock) / 1000.0 / speed))
        else:
            await asyncio.sleep(0)
        last_clock = clock
        item = item.model_copy(update={"match_id": match_id})
        topic = TOPIC_EVENTS if isinstance(item, MatchEvent) else TOPIC_TRACKING
        await bus.publish(topic, match_id, item)


class Pipeline:
    """Starts the in-process services on demand (first `POST /api/matches`)."""

    def __init__(self, bus: Bus, registry: Registry, engine: EngineService, *, run_sim: bool = True) -> None:
        self.bus = bus
        self.registry = registry
        self.engine = engine
        self.run_sim = run_sim
        self.tasks: set[asyncio.Task[Any]] = set()
        self._agents_started = False
        self.sim_available = find_entrypoint(SIM_ENTRYPOINTS) is not None
        self.agents_available = find_entrypoint(AGENT_ENTRYPOINTS) is not None

    def _spawn(self, coro: Any, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def ensure_services(self) -> None:
        """Engine (always ours) and agents (if importable). Idempotent."""
        self.engine.start()
        if self._agents_started:
            return
        self._agents_started = True
        fn = find_entrypoint(AGENT_ENTRYPOINTS)
        if fn is None:
            log.warning("fulcrum.agents service not available yet - moments will have no insights")
            return
        register = getattr(self.bus, "register", None)
        if register is not None:
            register(TOPIC_MOMENTS, "agents")
        self._spawn(self._guard(call_flexible(fn, bus=self.bus), "agents"), "agents")

    async def _guard(self, coro: Any, what: str, match_id: str | None = None) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("%s task crashed", what)
            if match_id and (rm := self.registry.get(match_id)) is not None:
                rm.status, rm.error = "error", f"{what}: {exc}"

    def start_match(self, config: MatchConfig) -> None:
        """Register the match with the engine, ensure services, and start the simulator (or a replay)."""
        self.engine.register_match(config)
        self.ensure_services()
        if self.run_sim:
            self._spawn(self._guard(self._simulate(config), "sim", config.match_id), f"sim-{config.match_id}")

    async def _simulate(self, config: MatchConfig) -> None:
        fn = find_entrypoint(SIM_ENTRYPOINTS)
        if fn is not None:
            await call_flexible(fn, config, bus=self.bus)
            return
        path = replay_file()
        if path is None:
            raise RuntimeError("no simulator and no recorded match to replay")
        log.warning("fulcrum.sim not available - replaying %s as match %s", path, config.match_id)
        await replay(path, self.bus, config.match_id, config.speed)

    async def stop(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.engine.stop()
