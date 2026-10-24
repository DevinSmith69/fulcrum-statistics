"""Engine service: `match-events` + `tracking-frames` in, `match-state` + `moments` out.

Run standalone with `python -m fulcrum.engine`, or in-process (single-process dev mode) via
`EngineService(bus).start()` from the API lifespan.
"""

from __future__ import annotations

import asyncio
import logging

from fulcrum.bus import Bus, get_bus
from fulcrum.contracts import (
    TOPIC_EVENTS,
    TOPIC_MOMENTS,
    TOPIC_STATE,
    TOPIC_TRACKING,
    MatchConfig,
    MatchEvent,
    Team,
    TrackingFrame,
)

from .match import EngineOutput, MatchEngine
from .params import DEFAULT_PARAMS, EngineParams
from .teams import load_teams

log = logging.getLogger("fulcrum.engine")
GROUP = "engine"


class EngineService:
    """Owns one `MatchEngine` per match_id and pumps the bus."""

    def __init__(
        self, bus: Bus | None = None, teams: dict[str, Team] | None = None, params: EngineParams = DEFAULT_PARAMS
    ) -> None:
        self.bus = bus or get_bus()
        self.teams = teams if teams is not None else load_teams()
        self.params = params
        self.engines: dict[str, MatchEngine] = {}
        self._configs: dict[str, MatchConfig] = {}
        self._tasks: list[asyncio.Task[None]] = []

    def register_match(self, config: MatchConfig) -> None:
        """Tell the engine which teams play (otherwise learnt from `extra.home_team_id` or the first events)."""
        self._configs[config.match_id] = config

    def engine_for(self, match_id: str) -> MatchEngine:
        """The match's engine, created on first use."""
        if match_id not in self.engines:
            cfg = self._configs.get(match_id)
            home = self.teams.get(cfg.home_team_id) if cfg else None
            away = self.teams.get(cfg.away_team_id) if cfg else None
            self.engines[match_id] = MatchEngine(match_id, home, away, self.teams, self.params)
        return self.engines[match_id]

    async def handle_event(self, ev: MatchEvent) -> EngineOutput:
        """Process one event and publish the resulting state / moments."""
        out = self.engine_for(ev.match_id).process_event(ev)
        await self._publish(ev.match_id, out)
        return out

    async def handle_frame(self, frame: TrackingFrame) -> EngineOutput:
        """Process one tracking frame and publish any resulting moments."""
        out = self.engine_for(frame.match_id).process_frame(frame)
        await self._publish(frame.match_id, out)
        return out

    async def _publish(self, match_id: str, out: EngineOutput) -> None:
        # State first, so a consumer reacting to a moment already has the matching state.
        if out.state is not None:
            await self.bus.publish(TOPIC_STATE, match_id, out.state)
        for m in out.moments:
            await self.bus.publish(TOPIC_MOMENTS, match_id, m)

    async def _consume(self, topic: str, model: type[MatchEvent] | type[TrackingFrame], handler) -> None:  # noqa: ANN001
        while True:  # re-subscribe if the consumer itself fails (e.g. a message that doesn't parse)
            try:
                async for msg in self.bus.subscribe(topic, model, GROUP):
                    try:
                        await handler(msg)
                    except Exception:  # one bad message must not kill the engine
                        log.exception("engine failed on %s message for %s", topic, msg.match_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("engine consumer for %s failed; re-subscribing", topic)
                await asyncio.sleep(0.5)

    async def _pump_events(self) -> None:
        await self._consume(TOPIC_EVENTS, MatchEvent, self.handle_event)

    async def _pump_frames(self) -> None:
        await self._consume(TOPIC_TRACKING, TrackingFrame, self.handle_frame)

    def start(self) -> list[asyncio.Task[None]]:
        """Register consumer groups (so nothing published meanwhile is lost) and start the pump tasks."""
        if self._tasks:
            return self._tasks
        register = getattr(self.bus, "register", None)
        if register is not None:
            register(TOPIC_EVENTS, GROUP)
            register(TOPIC_TRACKING, GROUP)
        self._tasks = [
            asyncio.create_task(self._pump_events(), name="engine-events"),
            asyncio.create_task(self._pump_frames(), name="engine-frames"),
        ]
        return self._tasks

    async def stop(self) -> None:
        """Cancel the pump tasks."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def run(self) -> None:
        """Run until cancelled."""
        await asyncio.gather(*self.start())


async def run(bus: Bus | None = None) -> None:
    """Entry point used by `python -m fulcrum.engine` and the container."""
    await EngineService(bus).run()
