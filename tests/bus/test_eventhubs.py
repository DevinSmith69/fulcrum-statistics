"""Offline tests for the Event Hubs bus: config parsing, (de)serialisation and bus semantics against a fake
aiokafka module. No network, no aiokafka install, no Azure credentials needed."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

import fulcrum.bus.base as bus_base
from fulcrum.bus.eventhubs import (
    EventHubsBus,
    EventHubsConfig,
    decode,
    encode,
    headers_for,
    namespace_fqdn,
    parse_connection_string,
)
from fulcrum.contracts import (
    TOPIC_EVENTS,
    TOPIC_INSIGHTS,
    InsightsEnvelope,
    MatchEvent,
    OverlayCue,
    OverlayTemplate,
    Persona,
    TrackingFrame,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "handmade_nba_rvr.jsonl"
# Obviously fake key: the format matches a real connection string, the secret does not.
CONN = (
    "Endpoint=sb://evh-unit-test.servicebus.windows.net/;SharedAccessKeyName=RootManageSharedAccessKey;"
    "SharedAccessKey=ZmFrZS1rZXktZm9yLXRlc3RzLW9ubHk=="
)


def _records() -> list[dict[str, Any]]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _first_pass_event() -> MatchEvent:
    rec = next(r for r in _records() if r["topic"] == TOPIC_EVENTS and r["data"].get("pass"))
    return MatchEvent.model_validate(rec["data"])


def _first_frame() -> TrackingFrame:
    rec = next(r for r in _records() if r["topic"] == "tracking-frames")
    return TrackingFrame.model_validate(rec["data"])


def _cue() -> OverlayCue:
    return OverlayCue(
        match_id="m1",
        template=OverlayTemplate.LOWER_THIRD,
        priority=5,
        sync_clock_ms=4_000_000,
        sync_seq=900,
        show_at_clock_ms=4_002_000,
        duration_ms=8000,
        persona=Persona.CASUAL,
        headline="Goal",
    )


# --------------------------------------------------------------------------- config parsing


def test_parse_connection_string_keeps_base64_padding():
    parts = parse_connection_string(CONN)
    assert parts["Endpoint"] == "sb://evh-unit-test.servicebus.windows.net/"
    assert parts["SharedAccessKeyName"] == "RootManageSharedAccessKey"
    assert parts["SharedAccessKey"].endswith("==")


@pytest.mark.parametrize("bad", ["", "nonsense", "SharedAccessKeyName=x;SharedAccessKey=y"])
def test_parse_connection_string_rejects_garbage(bad: str):
    with pytest.raises(ValueError):
        parse_connection_string(bad)


@pytest.mark.parametrize(
    "value",
    [
        "evh-x",
        "evh-x.servicebus.windows.net",
        "sb://evh-x.servicebus.windows.net/",
        "evh-x.servicebus.windows.net:9093",
        "  EVH-x  ".strip().lower(),
    ],
)
def test_namespace_fqdn_normalises(value: str):
    assert namespace_fqdn(value) == "evh-x.servicebus.windows.net"


def test_config_from_connection_string():
    cfg = EventHubsConfig.from_env({"EVENTHUBS_CONNECTION_STRING": CONN, "FULCRUM_SERVICE": "engine"})
    assert cfg.auth == "connection_string"
    assert cfg.bootstrap_servers == "evh-unit-test.servicebus.windows.net:9093"
    assert cfg.client_id == "fulcrum-engine"
    assert cfg.offset_reset == "latest"
    assert "ZmFrZS1rZXk" not in repr(cfg)  # the secret never shows up in logs


def test_config_managed_identity_when_only_namespace():
    cfg = EventHubsConfig.from_env({"EVENTHUBS_NAMESPACE": "evh-fulcrum-ds534"})
    assert cfg.auth == "managed_identity"
    assert cfg.connection_string is None
    assert cfg.bootstrap_servers == "evh-fulcrum-ds534.servicebus.windows.net:9093"
    assert cfg.token_scope == "https://evh-fulcrum-ds534.servicebus.windows.net/.default"


def test_config_forced_managed_identity_drops_the_secret():
    env = {"EVENTHUBS_CONNECTION_STRING": CONN, "FULCRUM_EVENTHUBS_AUTH": "managed_identity"}
    cfg = EventHubsConfig.from_env(env)
    assert cfg.auth == "managed_identity"
    assert cfg.connection_string is None
    assert cfg.fqdn == "evh-unit-test.servicebus.windows.net"


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"FULCRUM_EVENTHUBS_AUTH": "connection_string", "EVENTHUBS_NAMESPACE": "evh-x"},
        {"EVENTHUBS_CONNECTION_STRING": CONN, "FULCRUM_EVENTHUBS_AUTH": "kerberos"},
        {"EVENTHUBS_CONNECTION_STRING": CONN, "FULCRUM_EVENTHUBS_OFFSET_RESET": "middle"},
        {"EVENTHUBS_CONNECTION_STRING": "Endpoint=sb://evh-x.servicebus.windows.net/"},  # cut at the first ';'
    ],
)
def test_config_rejects_unusable_env(env: dict[str, str]):
    with pytest.raises(ValueError):
        EventHubsConfig.from_env(env)


def test_config_offset_reset_earliest():
    cfg = EventHubsConfig.from_env({"EVENTHUBS_CONNECTION_STRING": CONN, "FULCRUM_EVENTHUBS_OFFSET_RESET": "Earliest"})
    assert cfg.offset_reset == "earliest"


def test_kafka_kwargs_plain_sasl():
    pytest.importorskip("aiokafka")
    kw = EventHubsConfig.from_env({"EVENTHUBS_CONNECTION_STRING": CONN}).kafka_kwargs()
    assert kw["security_protocol"] == "SASL_SSL"
    assert kw["sasl_mechanism"] == "PLAIN"
    assert kw["sasl_plain_username"] == "$ConnectionString"
    assert kw["sasl_plain_password"] == CONN
    assert kw["bootstrap_servers"].endswith(".servicebus.windows.net:9093")
    assert kw["connections_max_idle_ms"] < 240_000  # Event Hubs drops idle connections at 240 s


def test_kafka_kwargs_oauthbearer():
    pytest.importorskip("aiokafka")
    from aiokafka.abc import AbstractTokenProvider

    kw = EventHubsConfig.from_env({"EVENTHUBS_NAMESPACE": "evh-x"}).kafka_kwargs()
    assert kw["sasl_mechanism"] == "OAUTHBEARER"
    assert isinstance(kw["sasl_oauth_token_provider"], AbstractTokenProvider)
    assert "sasl_plain_password" not in kw


# --------------------------------------------------------------------------- serialisation


def test_encode_decode_round_trips_match_event_with_pass_alias():
    ev = _first_pass_event()
    key, value = encode(ev.match_id, ev)
    assert key == ev.match_id.encode()
    assert b'"pass":' in value  # wire alias, same as MemoryBus / JSONL fixtures
    assert value == ev.model_dump_json(by_alias=True).encode()
    assert decode(value, MatchEvent) == ev


def test_encode_decode_tracking_frame_and_envelope():
    frame = _first_frame()
    assert decode(encode(frame.match_id, frame)[1], TrackingFrame) == frame
    env = InsightsEnvelope.wrap(_cue())
    back = decode(encode("m1", env)[1], InsightsEnvelope)
    assert back.kind == "cue" and isinstance(back.data, OverlayCue) and back.data == env.data


def test_decode_rejects_empty_and_wrong_model():
    with pytest.raises(ValueError):
        decode(None, MatchEvent)
    with pytest.raises(ValueError):  # pydantic.ValidationError is a ValueError
        decode(b'{"not": "an event"}', MatchEvent)


def test_headers_name_the_model():
    assert ("fulcrum-model", b"OverlayCue") in headers_for(_cue())


# --------------------------------------------------------------------------- bus semantics on a fake aiokafka


class _FakeCluster:
    """Tiny in-memory Kafka: per-topic log, per-(topic, group) read offset, partition = hash(key) % 2."""

    def __init__(self) -> None:
        self.log: dict[str, list[tuple[bytes, bytes, int]]] = defaultdict(list)
        self.offsets: dict[tuple[str, str], int] = {}
        self.new_data = asyncio.Event()
        self.producers_started = 0
        self.sync_sends = 0
        self.kwargs: list[dict[str, Any]] = []


def _fake_aiokafka(cluster: _FakeCluster) -> types.ModuleType:
    class Record:
        def __init__(self, key: bytes, value: bytes, partition: int, offset: int) -> None:
            self.key, self.value, self.partition, self.offset = key, value, partition, offset

    class AIOKafkaProducer:
        def __init__(self, **kw: Any) -> None:
            cluster.kwargs.append(kw)

        async def start(self) -> None:
            cluster.producers_started += 1

        async def send_and_wait(self, topic: str, value: bytes, key: bytes, headers: Any = None) -> None:
            cluster.sync_sends += 1
            cluster.log[topic].append((key, value, hash(key) % 2))
            cluster.new_data.set()

        async def send(self, topic: str, value: bytes, key: bytes, headers: Any = None) -> asyncio.Future[None]:
            fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            if topic == "fail-me":
                fut.set_exception(RuntimeError("broker said no"))
            else:
                cluster.log[topic].append((key, value, hash(key) % 2))
                cluster.new_data.set()
                fut.set_result(None)
            return fut

        async def stop(self) -> None:
            pass

    class AIOKafkaConsumer:
        def __init__(self, topic: str, group_id: str, auto_offset_reset: str, **kw: Any) -> None:
            self.topic, self.group = topic, group_id
            self.reset = auto_offset_reset
            cluster.kwargs.append({"group_id": group_id, **kw})
            self.stopped = False

        async def start(self) -> None:
            k = (self.topic, self.group)
            if k not in cluster.offsets:
                cluster.offsets[k] = 0 if self.reset == "earliest" else len(cluster.log[self.topic])

        async def stop(self) -> None:
            self.stopped = True

        def __aiter__(self) -> AIOKafkaConsumer:
            return self

        async def __anext__(self) -> Record:
            k = (self.topic, self.group)
            while cluster.offsets[k] >= len(cluster.log[self.topic]):
                cluster.new_data.clear()
                await cluster.new_data.wait()
            i = cluster.offsets[k]
            cluster.offsets[k] = i + 1
            key, value, part = cluster.log[self.topic][i]
            return Record(key, value, part, i)

    mod = types.ModuleType("aiokafka")
    mod.AIOKafkaProducer = AIOKafkaProducer  # type: ignore[attr-defined]
    mod.AIOKafkaConsumer = AIOKafkaConsumer  # type: ignore[attr-defined]
    helpers = types.ModuleType("aiokafka.helpers")
    helpers.create_ssl_context = lambda: "ssl-context"  # type: ignore[attr-defined]
    return mod, helpers


@pytest.fixture
def fake_kafka(monkeypatch: pytest.MonkeyPatch) -> _FakeCluster:
    cluster = _FakeCluster()
    mod, helpers = _fake_aiokafka(cluster)
    monkeypatch.setitem(sys.modules, "aiokafka", mod)
    monkeypatch.setitem(sys.modules, "aiokafka.helpers", helpers)
    return cluster


async def _take(it: Any, n: int) -> list[Any]:
    out = []
    async for item in it:
        out.append(item)
        if len(out) == n:
            break
    return out


async def test_every_group_gets_every_message_in_order(fake_kafka: _FakeCluster):
    bus = EventHubsBus(EventHubsConfig.from_env({"EVENTHUBS_CONNECTION_STRING": CONN}))
    events = [MatchEvent.model_validate(r["data"]) for r in _records()[:5]]
    engine_it = bus.subscribe(TOPIC_EVENTS, MatchEvent, "engine")
    api_it = bus.subscribe(TOPIC_EVENTS, MatchEvent, "api-feed")
    engine_task = asyncio.create_task(_take(engine_it, 5))
    api_task = asyncio.create_task(_take(api_it, 5))
    await asyncio.sleep(0)  # let both consumers start (offset reset = latest)
    for ev in events:
        await bus.publish(TOPIC_EVENTS, ev.match_id, ev)
    got_engine, got_api = await asyncio.wait_for(asyncio.gather(engine_task, api_task), 2)
    assert got_engine == events and got_api == events
    assert fake_kafka.producers_started == 1  # one shared producer, started lazily
    assert {kw["group_id"] for kw in fake_kafka.kwargs if "group_id" in kw} == {"engine", "api-feed"}
    assert all(k == events[0].match_id.encode() for k, _, _ in fake_kafka.log[TOPIC_EVENTS])  # key = match_id
    await bus.close()


async def test_poison_message_is_skipped_not_fatal(fake_kafka: _FakeCluster):
    bus = EventHubsBus(EventHubsConfig.from_env({"EVENTHUBS_CONNECTION_STRING": CONN}))
    it = bus.subscribe(TOPIC_INSIGHTS, InsightsEnvelope, "api")
    task = asyncio.create_task(_take(it, 1))
    await asyncio.sleep(0)
    fake_kafka.log[TOPIC_INSIGHTS].append((b"m1", b"{broken json", 0))
    env = InsightsEnvelope.wrap(_cue())
    await bus.publish(TOPIC_INSIGHTS, "m1", env)
    (got,) = await asyncio.wait_for(task, 2)
    assert got == env
    await bus.close()


async def test_publish_after_close_fails(fake_kafka: _FakeCluster):
    bus = EventHubsBus(EventHubsConfig.from_env({"EVENTHUBS_CONNECTION_STRING": CONN}))
    await bus.close()
    with pytest.raises(RuntimeError):
        await bus.publish(TOPIC_INSIGHTS, "m1", InsightsEnvelope.wrap(_cue()))


def test_get_bus_selects_eventhubs(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bus_base, "_bus", None)
    monkeypatch.setenv("FULCRUM_BUS", "eventhubs")
    monkeypatch.setenv("EVENTHUBS_CONNECTION_STRING", CONN)
    monkeypatch.delenv("FULCRUM_EVENTHUBS_AUTH", raising=False)
    monkeypatch.delenv("FULCRUM_EVENTHUBS_OFFSET_RESET", raising=False)
    bus = bus_base.get_bus()
    assert isinstance(bus, EventHubsBus)
    assert bus.config.fqdn == "evh-unit-test.servicebus.windows.net"
    monkeypatch.setattr(bus_base, "_bus", None)  # don't leak into other tests


async def test_delivery_failures_are_counted_not_raised(fake_kafka: _FakeCluster):
    bus = EventHubsBus(EventHubsConfig.from_env({"EVENTHUBS_CONNECTION_STRING": CONN}))
    await bus.publish("fail-me", "m1", _cue())
    await asyncio.sleep(0)  # done callbacks run on the next loop iteration
    assert bus.delivery_errors == 1
    await bus.close()


async def test_sync_publish_waits_for_ack(fake_kafka: _FakeCluster):
    env = {"EVENTHUBS_CONNECTION_STRING": CONN, "FULCRUM_EVENTHUBS_SYNC": "1"}
    bus = EventHubsBus(EventHubsConfig.from_env(env))
    await bus.publish(TOPIC_INSIGHTS, "m1", InsightsEnvelope.wrap(_cue()))
    assert fake_kafka.sync_sends == 1 and len(fake_kafka.log[TOPIC_INSIGHTS]) == 1
    await bus.close()
