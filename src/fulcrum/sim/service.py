"""Simulator as a long-running service, for the out-of-process deployment (the `sim` Container App).

    uvicorn fulcrum.sim.service:app --port 8001          (FULCRUM_SERVICE=sim in the Docker image)

* `POST /matches` with a `MatchConfig` starts that match on the bus (202). Internal ingress only; the API calls
  it for `POST /api/matches` when it runs out of process (`FULCRUM_SIM_URL`, see docs/HANDOFFS.md).
* `GET /matches` lists running/finished matches, `GET /health` is the probe.
* Demo director in the split / hybrid topologies: `GET|POST /matches/{id}/control` (same body and snapshot as
  the API's `…/control`, which forwards here), and `POST /matches/{id}/moment`, through which the API passes the
  engine's moments while a "next moment" skip runs (the engine is not in this process).
* `FULCRUM_SIM_AUTOSTART=1` starts one match on boot, so a fresh deployment has a live match without anyone
  calling the API. `FULCRUM_SIM_HOME` / `_AWAY` / `_SEED` / `_SPEED` choose it (defaults NBA, RVR, 7, 10).
  `FULCRUM_SIM_LOOP=1` keeps going: the next seed starts `FULCRUM_SIM_GAP_S` (default 60) after full time.
  Each match costs Foundry tokens, so looping is off by default.

Every `match_start` this service publishes carries `extra.home_team_id` / `extra.away_team_id`, so the engine,
agents and API (separate processes that never saw the `MatchConfig`) know which side is home.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from fulcrum.bus import Bus, get_bus
from fulcrum.contracts import EventType, MatchConfig, MatchEvent, Moment

from .control import MAX_SPEED, MIN_SPEED, ControlError, ControlRequest, MatchControl, apply
from .players import load_teams
from .runner import run_match

log = logging.getLogger("fulcrum.sim.service")


class TeamTaggingBus:
    """Bus wrapper that stamps `home_team_id` / `away_team_id` into `match_start.extra` (the deterministic
    simulator itself leaves them out; out-of-process consumers need them)."""

    def __init__(self, inner: Bus, config: MatchConfig) -> None:
        self.inner = inner
        self.config = config

    async def publish(self, topic: str, key: str, message: BaseModel) -> None:
        if isinstance(message, MatchEvent) and message.type == EventType.MATCH_START:
            extra = {
                **message.extra,
                "home_team_id": self.config.home_team_id,
                "away_team_id": self.config.away_team_id,
            }
            message = message.model_copy(update={"extra": extra})
        await self.inner.publish(topic, key, message)

    def subscribe(self, topic: str, model: Any, group: str) -> Any:
        return self.inner.subscribe(topic, model, group)

    async def close(self) -> None:
        await self.inner.close()


class SimService:
    """Runs matches as asyncio tasks against the configured bus."""

    def __init__(self, bus: Bus | None = None) -> None:
        self.bus = bus or get_bus()
        self.teams = load_teams()
        self.tasks: dict[str, asyncio.Task[tuple[int, int]]] = {}
        self.controls: dict[str, MatchControl] = {}  # demo director: per-match speed / pause / skip

    def start(self, config: MatchConfig) -> None:
        """Start `config` in the background. Raises ValueError for unknown teams or a duplicate running match."""
        for team_id in (config.home_team_id, config.away_team_id):
            if team_id not in self.teams:
                raise ValueError(f"unknown team '{team_id}'")
        running = self.tasks.get(config.match_id)
        if running is not None and not running.done():
            raise ValueError(f"match '{config.match_id}' is already running")
        bus = TeamTaggingBus(self.bus, config)
        home, away = self.teams[config.home_team_id], self.teams[config.away_team_id]
        # Controllable pacing for real-time-ish matches; speed 0 ("as fast as possible", tests/replays) stays raw.
        control = MatchControl(config.match_id, config.speed) if MIN_SPEED <= config.speed <= MAX_SPEED else None
        if control is not None:
            self.controls[config.match_id] = control
        task = asyncio.create_task(
            run_match(config, home=home, away=away, bus=bus, control=control), name=f"sim-{config.match_id}"
        )
        task.add_done_callback(lambda t, mid=config.match_id: self._done(mid, t))
        self.tasks[config.match_id] = task
        log.info("sim: started %s (%s v %s, seed %s)", config.match_id, home.team_id, away.team_id, config.seed)

    def _done(self, match_id: str, task: asyncio.Task[tuple[int, int]]) -> None:
        if task.cancelled():
            log.info("sim: %s cancelled", match_id)
        elif task.exception() is not None:
            log.error("sim: %s failed", match_id, exc_info=task.exception())
        else:
            log.info("sim: %s finished %s-%s", match_id, *task.result())

    def summary(self) -> list[dict[str, Any]]:
        """`[{match_id, status}]` for every match this process started."""
        return [{"match_id": mid, "status": _status(t)} for mid, t in self.tasks.items()]

    async def autostart(self, env: dict[str, str] | None = None) -> None:
        """The boot-time match (and, with FULCRUM_SIM_LOOP=1, the ones after it)."""
        env = dict(os.environ) if env is None else env
        home, away = env.get("FULCRUM_SIM_HOME", "NBA"), env.get("FULCRUM_SIM_AWAY", "RVR")
        seed = int(env.get("FULCRUM_SIM_SEED", "7"))
        speed = float(env.get("FULCRUM_SIM_SPEED", "10"))
        loop, gap = env.get("FULCRUM_SIM_LOOP") == "1", float(env.get("FULCRUM_SIM_GAP_S", "60"))
        while True:
            cfg = MatchConfig(
                match_id=f"{home}-{away}-s{seed}".lower(), home_team_id=home, away_team_id=away, seed=seed, speed=speed
            )
            self.start(cfg)
            if not loop:
                return
            await asyncio.gather(self.tasks[cfg.match_id], return_exceptions=True)
            await asyncio.sleep(gap)
            seed += 1

    async def stop(self) -> None:
        for t in self.tasks.values():
            t.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


def _status(task: asyncio.Task[Any]) -> str:
    if not task.done():
        return "running"
    return "failed" if task.cancelled() or task.exception() is not None else "finished"


def create_app(bus: Bus | None = None) -> FastAPI:
    """The sim service's FastAPI app (module-level `app` uses the env-selected bus)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = SimService(bus)
        app.state.sim = svc
        boot = None
        if os.getenv("FULCRUM_SIM_AUTOSTART") == "1":
            boot = asyncio.create_task(svc.autostart(), name="sim-autostart")
        try:
            yield
        finally:
            if boot is not None:
                boot.cancel()
            await svc.stop()

    app = FastAPI(title="Fulcrum simulator", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "service": "sim", "bus": os.getenv("FULCRUM_BUS", "memory")}

    @app.get("/matches")
    async def matches() -> list[dict[str, Any]]:
        return app.state.sim.summary()

    @app.post("/matches", status_code=202)
    async def start(config: MatchConfig) -> dict[str, str]:
        try:
            app.state.sim.start(config)
        except ValueError as exc:
            raise HTTPException(409 if "already running" in str(exc) else 422, str(exc)) from exc
        return {"match_id": config.match_id}

    def control(match_id: str) -> MatchControl:
        ctl = app.state.sim.controls.get(match_id)
        if ctl is None:
            raise HTTPException(404, f"no match '{match_id}' in this simulator")
        return ctl

    @app.get("/matches/{match_id}/control")
    async def get_control(match_id: str) -> dict[str, Any]:
        return control(match_id).snapshot()

    @app.post("/matches/{match_id}/control")
    async def post_control(match_id: str, body: ControlRequest) -> dict[str, Any]:
        try:
            return apply(control(match_id), body)
        except ControlError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @app.post("/matches/{match_id}/moment")
    async def moment(match_id: str, body: Moment) -> dict[str, Any]:
        """The API's engine saw `body`: ends a running skip if it qualifies. Returns the control snapshot."""
        ctl = control(match_id)
        ctl.on_moment(body)
        return ctl.snapshot()

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
