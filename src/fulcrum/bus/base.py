from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Protocol, TypeVar

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)


class Bus(Protocol):
    async def publish(self, topic: str, key: str, message: BaseModel) -> None:
        """Publish a contract model. `key` = match_id (keeps per-match ordering)."""

    def subscribe(self, topic: str, model: type[M], group: str) -> AsyncIterator[M]:
        """Yield parsed contract models. Each `group` receives every message once."""

    async def close(self) -> None: ...


class MemoryBus:
    """In-process fan-out bus. One queue per (topic, group). Unbounded on purpose: dev only."""

    def __init__(self) -> None:
        self._queues: dict[str, dict[str, asyncio.Queue[str]]] = defaultdict(dict)

    async def publish(self, topic: str, key: str, message: BaseModel) -> None:
        raw = message.model_dump_json(by_alias=True)
        for q in self._queues[topic].values():
            q.put_nowait(raw)

    def _queue(self, topic: str, group: str) -> asyncio.Queue[str]:
        return self._queues[topic].setdefault(group, asyncio.Queue())

    async def subscribe(self, topic: str, model: type[M], group: str) -> AsyncIterator[M]:  # type: ignore[override]
        q = self._queue(topic, group)
        while True:
            raw = await q.get()
            yield model.model_validate_json(raw)

    def register(self, topic: str, group: str) -> None:
        """Create the group's queue up front so nothing published before the first await is lost."""
        self._queue(topic, group)

    async def close(self) -> None:
        self._queues.clear()


_bus: Bus | None = None


def get_bus() -> Bus:
    global _bus
    if _bus is None:
        kind = os.getenv("FULCRUM_BUS", "memory")
        if kind == "memory":
            _bus = MemoryBus()
        elif kind == "eventhubs":
            from .eventhubs import EventHubsBus  # implemented in WS5

            _bus = EventHubsBus.from_env()
        else:
            raise ValueError(f"Unknown FULCRUM_BUS={kind}")
    return _bus
