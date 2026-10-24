"""Single-process dev mode: run sim + engine + agents as asyncio tasks next to the API (FULCRUM_BUS=memory).

The simulator (WS1) and agents (WS3) are imported lazily and every call is guarded, so the API keeps
working while those packages are still stubs:
- no simulator → replay a recorded match (FULCRUM_REPLAY_FILE, else data/matches/sample_seed7.jsonl,
  else the hand-made test fixture), re-keyed to the new match_id and paced by `config.speed`. Setting
  FULCRUM_REPLAY_FILE forces the replay even when the simulator exists (deterministic demos);
- no agents → moments still flow, there are just no insights (and translation serves English).
Agents: one `fulcrum.agents.AgentService(bus, get_llm())`, started on the first match; it also provides the
translate / recap hooks used by `translation.py`. Entry points probed are listed in docs/HANDOFFS.md.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from fulcrum.bus import Bus
from fulcrum.contracts import TOPIC_EVENTS, TOPIC_TRACKING, MatchConfig, MatchEvent, TrackingFrame
from fulcrum.engine.replay import read_jsonl
from fulcrum.engine.service import EngineService

from .readmodel import Registry
from .translation import Translator

log = logging.getLogger("fulcrum.api.pipeline")
ROOT = Path(__file__).resolve().parents[3]
REPLAY_CANDIDATES = [
    ROOT / "data" / "matches" / "sample_seed7.jsonl",
    ROOT / "data" / "matches" / "seed7.jsonl",
    ROOT / "tests" / "fixtures" / "handmade_nba_rvr.jsonl",
]
SIM_ENTRYPOINTS = [("fulcrum.sim.runner", "run_match"), ("fulcrum.sim.runner", "run"), ("fulcrum.sim", "run_match")]
AGENT_SERVICE = [("fulcrum.agents.service", "AgentService"), ("fulcrum.agents", "AgentService")]
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
    """The recording to replay when no simulator is importable."""
    env = os.getenv("FULCRUM_REPLAY_FILE")
    if env:
        return Path(env)
    return next((p for p in REPLAY_CANDIDATES if p.exists()), None)


def recording_teams(path: Path) -> tuple[str, str] | None:
    """(home, away) of a recording: `extra.home_team_id/away_team_id`, else the P1 tracking frame
    (home attacks towards x = 105 in P1, so its players' mean x is lower)."""
    for item in read_jsonl(path):
        if isinstance(item, MatchEvent):
            h, a = item.extra.get("home_team_id"), item.extra.get("away_team_id")
            if h and a:
                return str(h), str(a)
        elif isinstance(item, TrackingFrame) and item.period == 1 and item.players:
            xs: dict[str, list[float]] = {}
            for p in item.players:
                xs.setdefault(p.team_id, []).append(p.x)
            if len(xs) == 2:
                home, away = sorted(xs, key=lambda t: sum(xs[t]) / len(xs[t]))
                return home, away
    return None


def team_remapper(src: tuple[str, str] | None, dst: tuple[str, str]) -> Callable[[str], str] | None:
    """Rewrites team ids and `TEAM-NN` player ids in a raw JSON line (simultaneous, so swaps work)."""
    if src is None or src == dst:
        return None
    mapping = dict(zip(src, dst, strict=True))
    pattern = re.compile(r'"(' + "|".join(map(re.escape, src)) + r')(-|")')
    return lambda line: pattern.sub(lambda m: f'"{mapping[m.group(1)]}{m.group(2)}', line)


def _read_remapped(path: Path, remap: Callable[[str], str] | None) -> Iterator[MatchEvent | TrackingFrame]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(remap(line) if remap else line)
            if row["topic"] == TOPIC_EVENTS:
                yield MatchEvent.model_validate(row["data"])
            elif row["topic"] == TOPIC_TRACKING:
                yield TrackingFrame.model_validate(row["data"])


async def replay(path: Path, bus: Bus, match_id: str, speed: float, teams: tuple[str, str] | None = None) -> None:
    """Publish a recorded match onto the bus under `match_id`, paced to `speed`× sim time.

    With `teams=(home, away)` the recording's clubs are re-labelled as those teams (ids and player ids), so a
    replay always matches the match the viewer asked for.
    """
    remap = team_remapper(recording_teams(path), teams) if teams else None
    last_clock: int | None = None
    for item in _read_remapped(path, remap):
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

    def __init__(
        self,
        bus: Bus,
        registry: Registry,
        engine: EngineService,
        translator: Translator | None = None,
        *,
        run_sim: bool = True,
        run_agents: bool = True,
    ) -> None:
        self.bus = bus
        self.registry = registry
        self.engine = engine
        self.translator = translator
        self.run_sim = run_sim
        self.run_agents = run_agents
        self.tasks: set[asyncio.Task[Any]] = set()
        self.agents: Any = None
        self._agents_tried = False
        self.sim_available = find_entrypoint(SIM_ENTRYPOINTS) is not None
        self.agents_available = run_agents and find_entrypoint(AGENT_SERVICE) is not None

    def _spawn(self, coro: Any, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def ensure_services(self) -> None:
        """Engine (always ours) and agents (if enabled and importable). Idempotent."""
        self.engine.start()
        if self._agents_tried or not self.run_agents:
            return
        self._agents_tried = True
        cls = find_entrypoint(AGENT_SERVICE)
        if cls is None:
            log.warning("fulcrum.agents service not available yet - moments will have no insights")
            return
        try:
            from fulcrum.llm import get_llm

            svc = cls(self.bus, get_llm())
            await svc.start()  # registers its consumer groups before returning
        except Exception:
            log.exception("could not start the agents service - moments will have no insights")
            return
        self.agents = svc
        if self.translator is not None:
            self.translator.agents = svc

    async def _guard(self, coro: Any, what: str, match_id: str | None = None) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("%s task crashed", what)
            if match_id and (rm := self.registry.get(match_id)) is not None:
                rm.status, rm.error = "error", f"{what}: {exc}"

    async def start_match(self, config: MatchConfig) -> None:
        """Register the match with engine + agents, ensure services, and start the simulator (or a replay)."""
        self.engine.register_match(config)
        await self.ensure_services()
        if self.agents is not None:
            try:
                self.agents.register_match(config)
            except Exception:
                log.exception("agents.register_match failed for %s", config.match_id)
        if self.run_sim:
            self._spawn(self._guard(self._simulate(config), "sim", config.match_id), f"sim-{config.match_id}")

    async def _simulate(self, config: MatchConfig) -> None:
        fn = find_entrypoint(SIM_ENTRYPOINTS)
        if fn is not None and not os.getenv("FULCRUM_REPLAY_FILE"):  # an explicit recording wins (demos)
            await call_flexible(fn, config, bus=self.bus)
            return
        path = replay_file()
        if path is None:
            raise RuntimeError("no simulator and no recorded match to replay")
        log.warning("fulcrum.sim not available - replaying %s as match %s", path, config.match_id)
        await replay(path, self.bus, config.match_id, config.speed, (config.home_team_id, config.away_team_id))

    async def stop(self) -> None:
        """Cancel the sim task(s) and stop agents and engine."""
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.agents is not None:
            try:
                await self.agents.stop()
            except Exception:
                log.exception("agents.stop failed")
        await self.engine.stop()
