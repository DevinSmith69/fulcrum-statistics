"""Opt-in live round trip through the real Event Hubs namespace (Kafka endpoint).

    FULCRUM_LIVE_EVENTHUBS=1 uv run pytest -q tests/bus/test_eventhubs_live.py

Needs `EVENTHUBS_CONNECTION_STRING` (read from the environment or `.env`) and the `azure` extra (aiokafka).
Skipped otherwise, so `make test` stays offline. It publishes one valid `MatchEvent` for a throwaway match id
to `match-events` and reads it back with the consumer group from `FULCRUM_LIVE_EVENTHUBS_GROUP` (default
`$Default`, which every Event Hub has, so the test creates nothing in Azure).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from dotenv import load_dotenv

from fulcrum.bus.eventhubs import EventHubsBus, EventHubsConfig
from fulcrum.contracts import TOPIC_EVENTS, EventType, MatchEvent

pytestmark = pytest.mark.skipif(
    os.getenv("FULCRUM_LIVE_EVENTHUBS") != "1", reason="live Event Hubs test; set FULCRUM_LIVE_EVENTHUBS=1"
)


async def test_live_round_trip():
    pytest.importorskip("aiokafka")
    load_dotenv()
    if not os.getenv("EVENTHUBS_CONNECTION_STRING") and not os.getenv("EVENTHUBS_NAMESPACE"):
        pytest.skip("no EVENTHUBS_CONNECTION_STRING / EVENTHUBS_NAMESPACE")
    group = os.getenv("FULCRUM_LIVE_EVENTHUBS_GROUP", "$Default")
    bus = EventHubsBus(EventHubsConfig.from_env())
    match_id = f"live-test-{uuid.uuid4().hex[:8]}"
    ev = MatchEvent(
        event_id="live0",
        match_id=match_id,
        seq=0,
        ts=datetime.now(UTC),
        period=1,
        clock_ms=0,
        type=EventType.MATCH_START,
        extra={"purpose": "fulcrum live bus test"},
    )
    received: asyncio.Future[MatchEvent] = asyncio.get_running_loop().create_future()

    async def consume() -> None:
        async for msg in bus.subscribe(TOPIC_EVENTS, MatchEvent, group):
            if msg.match_id == match_id:
                received.set_result(msg)
                return

    consumer = asyncio.create_task(consume())
    try:
        # A new group starts at the live edge, and joining it takes a few seconds, so keep publishing (same
        # event, same key) until the consumer has been assigned its partition and sees one.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 90
        t0 = loop.time()
        while not received.done():
            assert loop.time() < deadline, "no round trip within 90 s"
            await bus.publish(TOPIC_EVENTS, match_id, ev)
            try:
                await asyncio.wait_for(asyncio.shield(received), 3)
            except TimeoutError:
                pass
        got = received.result()
        print(f"round trip OK via {bus.config.bootstrap_servers} group={group} in {loop.time() - t0:.1f}s")
        assert got == ev
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        await bus.close()
