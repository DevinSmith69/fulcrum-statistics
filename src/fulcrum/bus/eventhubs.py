"""Azure Event Hubs bus over the Kafka endpoint (`<namespace>.servicebus.windows.net:9093`).

Selected with `FULCRUM_BUS=eventhubs`. Same semantics as `MemoryBus`:

* topic = Event Hub name (`match-events`, `tracking-frames`, `match-state`, `moments`, `insights`);
* partition key = `key` (the match_id), so each match stays ordered within one partition;
* payload = the contract model as pydantic JSON (`model_dump_json(by_alias=True)`, UTF-8);
* `subscribe(topic, model, group)` uses `group` as the Kafka consumer group: every group receives every
  message once, and replicas sharing a group split the partitions between them.

Authentication (see docs/DEPLOY.md):

* **Connection string (dev, and the default in Azure via Key Vault):** SASL_SSL + PLAIN with the literal
  username `$ConnectionString` and the namespace connection string as the password.
  Env: `EVENTHUBS_CONNECTION_STRING`.
* **Managed identity (production path):** SASL_SSL + OAUTHBEARER with an Entra ID token for
  `https://<namespace>.servicebus.windows.net/.default` from `azure.identity` (`DefaultAzureCredential`;
  set `AZURE_CLIENT_ID` for a user-assigned identity). The identity needs "Azure Event Hubs Data Sender" and
  "Azure Event Hubs Data Receiver". Env: `EVENTHUBS_NAMESPACE` and no connection string, or force it with
  `FULCRUM_EVENTHUBS_AUTH=managed_identity`.

Other env: `FULCRUM_EVENTHUBS_OFFSET_RESET` (`latest` default: a brand-new group starts at the live edge, like
a MemoryBus queue created on subscribe; `earliest` replays the hub's retention), `FULCRUM_SERVICE` (used in the
Kafka client id).

`aiokafka` is imported lazily (it lives in the `azure` extra), so this module and its config parsing can be
imported and unit-tested without it.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:  # pragma: no cover
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

log = logging.getLogger("fulcrum.bus.eventhubs")

M = TypeVar("M", bound=BaseModel)

KAFKA_PORT = 9093
NAMESPACE_SUFFIX = ".servicebus.windows.net"
AuthMode = Literal["connection_string", "managed_identity"]

# Event Hubs Kafka recommendations (learn.microsoft.com "Apache Kafka client configurations for Event Hubs"):
# the service closes idle connections after 240 s, so keep metadata refresh / idle close below that.
_COMMON_KAFKA_CONFIG: dict[str, Any] = {
    "request_timeout_ms": 60_000,
    "metadata_max_age_ms": 180_000,
    "connections_max_idle_ms": 180_000,
}
_PRODUCER_CONFIG: dict[str, Any] = {
    "linger_ms": 5,  # tiny batching window; keeps per-match latency low
    "max_request_size": 1_000_000,  # Event Hubs Standard caps a message/batch at 1 MB
    "acks": "all",
}
_CONSUMER_CONFIG: dict[str, Any] = {
    "session_timeout_ms": 30_000,
    "heartbeat_interval_ms": 3_000,
    "enable_auto_commit": True,
    "auto_commit_interval_ms": 2_000,
}


# --------------------------------------------------------------------------- config


def parse_connection_string(conn: str) -> dict[str, str]:
    """Split an Event Hubs connection string (`Endpoint=sb://…/;SharedAccessKeyName=…;…`) into its parts.

    Keys keep their original case. Values may contain `=` (base64 keys), so only the first `=` splits.
    """
    parts: dict[str, str] = {}
    for chunk in conn.strip().split(";"):
        if not chunk.strip():
            continue
        key, sep, value = chunk.partition("=")
        if not sep:
            raise ValueError("malformed Event Hubs connection string (expected key=value pairs)")
        parts[key.strip()] = value.strip()
    if "Endpoint" not in parts:
        raise ValueError("Event Hubs connection string has no Endpoint=sb://<namespace>.servicebus.windows.net/")
    return parts


def namespace_fqdn(value: str) -> str:
    """Normalise `evh-x`, `evh-x.servicebus.windows.net`, `sb://evh-x.servicebus.windows.net/` → FQDN."""
    host = value.strip()
    for prefix in ("sb://", "https://", "amqps://"):
        if host.lower().startswith(prefix):
            host = host[len(prefix) :]
    host = host.split("/", 1)[0].split(":", 1)[0]
    if not host:
        raise ValueError("empty Event Hubs namespace")
    if "." not in host:
        host += NAMESPACE_SUFFIX
    return host


@dataclass(frozen=True)
class EventHubsConfig:
    """Everything needed to talk to one Event Hubs namespace over Kafka."""

    fqdn: str
    auth: AuthMode
    connection_string: str | None = field(default=None, repr=False)  # never printed
    client_id: str = "fulcrum"
    offset_reset: Literal["latest", "earliest"] = "latest"

    @property
    def bootstrap_servers(self) -> str:
        """`<namespace>.servicebus.windows.net:9093`."""
        return f"{self.fqdn}:{KAFKA_PORT}"

    @property
    def token_scope(self) -> str:
        """Entra ID scope for OAUTHBEARER."""
        return f"https://{self.fqdn}/.default"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> EventHubsConfig:
        """Build from environment variables (see module docstring). Raises ValueError when unusable."""
        env = dict(os.environ) if env is None else env
        conn = (env.get("EVENTHUBS_CONNECTION_STRING") or "").strip() or None
        ns = (env.get("EVENTHUBS_NAMESPACE") or "").strip() or None
        mode = (env.get("FULCRUM_EVENTHUBS_AUTH") or "auto").strip().lower()
        if mode not in ("auto", "connection_string", "managed_identity"):
            raise ValueError(f"FULCRUM_EVENTHUBS_AUTH must be auto|connection_string|managed_identity, not {mode!r}")
        if mode == "auto":
            mode = "connection_string" if conn else "managed_identity"
        if mode == "connection_string":
            if not conn:
                raise ValueError("FULCRUM_BUS=eventhubs needs EVENTHUBS_CONNECTION_STRING or EVENTHUBS_NAMESPACE")
            fqdn = namespace_fqdn(parse_connection_string(conn)["Endpoint"])
        else:
            if not ns and conn:
                ns = parse_connection_string(conn)["Endpoint"]
            if not ns:
                raise ValueError("managed-identity auth needs EVENTHUBS_NAMESPACE")
            fqdn = namespace_fqdn(ns)
            conn = None
        reset = (env.get("FULCRUM_EVENTHUBS_OFFSET_RESET") or "latest").strip().lower()
        if reset not in ("latest", "earliest"):
            raise ValueError(f"FULCRUM_EVENTHUBS_OFFSET_RESET must be latest|earliest, not {reset!r}")
        service = (env.get("FULCRUM_SERVICE") or "").strip()
        client_id = f"fulcrum-{service}" if service else "fulcrum"
        return cls(fqdn=fqdn, auth=mode, connection_string=conn, client_id=client_id, offset_reset=reset)  # type: ignore[arg-type]

    def kafka_kwargs(self, token_provider: Any = None) -> dict[str, Any]:
        """Security + Event Hubs tuning kwargs shared by producer and consumers.

        For managed identity pass one shared `token_provider` (else a new one, with its own credential, is made).
        """
        from aiokafka.helpers import create_ssl_context

        kw: dict[str, Any] = {
            "bootstrap_servers": self.bootstrap_servers,
            "client_id": self.client_id,
            "security_protocol": "SASL_SSL",
            "ssl_context": create_ssl_context(),
            **_COMMON_KAFKA_CONFIG,
        }
        if self.auth == "connection_string":
            kw |= {
                "sasl_mechanism": "PLAIN",
                "sasl_plain_username": "$ConnectionString",
                "sasl_plain_password": self.connection_string,
            }
        else:
            provider = token_provider or entra_token_provider(self.token_scope)
            kw |= {"sasl_mechanism": "OAUTHBEARER", "sasl_oauth_token_provider": provider}
        return kw


# --------------------------------------------------------------------------- (de)serialisation


def encode(key: str, message: BaseModel) -> tuple[bytes, bytes]:
    """Kafka (key, value) bytes for a contract model: key = match_id, value = pydantic JSON by alias."""
    return key.encode("utf-8"), message.model_dump_json(by_alias=True).encode("utf-8")


def decode[T: BaseModel](value: bytes | None, model: type[T]) -> T:
    """Parse a Kafka value back into `model` (raises pydantic.ValidationError on bad payloads)."""
    if value is None:
        raise ValueError("empty Kafka message value")
    return model.model_validate_json(value)


def headers_for(message: BaseModel) -> list[tuple[str, bytes]]:
    """Diagnostic headers (not used for routing): content type and the contract model's name."""
    return [("content-type", b"application/json"), ("fulcrum-model", type(message).__name__.encode())]


# --------------------------------------------------------------------------- managed identity


def entra_token_provider(scope: str) -> Any:
    """aiokafka OAUTHBEARER token provider backed by azure.identity (production path, see module docstring).

    Built lazily because aiokafka requires a subclass of its `AbstractTokenProvider` and lives in the azure extra.
    """
    from aiokafka.abc import AbstractTokenProvider

    class EntraTokenProvider(AbstractTokenProvider):
        def __init__(self) -> None:
            self.scope = scope
            self._credential: Any = None

        async def token(self) -> str:
            if self._credential is None:
                from azure.identity.aio import DefaultAzureCredential

                self._credential = DefaultAzureCredential()  # honours AZURE_CLIENT_ID (user-assigned MI)
            return (await self._credential.get_token(self.scope)).token

        async def close(self) -> None:
            if self._credential is not None:
                await self._credential.close()

    return EntraTokenProvider()


# --------------------------------------------------------------------------- the bus


class EventHubsBus:
    """`Bus` implementation over the Event Hubs Kafka endpoint (one shared producer, one consumer per subscribe)."""

    def __init__(self, config: EventHubsConfig) -> None:
        self.config = config
        self._producer: AIOKafkaProducer | None = None
        self._producer_lock = asyncio.Lock()
        self._consumers: set[AIOKafkaConsumer] = set()
        self._closed = False
        self._token_provider: Any = None

    def _kafka_kwargs(self) -> dict[str, Any]:
        if self.config.auth == "managed_identity" and self._token_provider is None:
            self._token_provider = entra_token_provider(self.config.token_scope)
        return self.config.kafka_kwargs(self._token_provider)

    @classmethod
    def from_env(cls) -> EventHubsBus:
        """Construct from environment variables (`get_bus()` calls this for FULCRUM_BUS=eventhubs)."""
        return cls(EventHubsConfig.from_env())

    async def _get_producer(self) -> AIOKafkaProducer:
        if self._producer is not None:
            return self._producer
        async with self._producer_lock:
            if self._producer is None:
                from aiokafka import AIOKafkaProducer

                producer = AIOKafkaProducer(**self._kafka_kwargs(), **_PRODUCER_CONFIG)
                await producer.start()
                log.info("eventhubs producer connected to %s (%s)", self.config.bootstrap_servers, self.config.auth)
                self._producer = producer
        return self._producer

    async def publish(self, topic: str, key: str, message: BaseModel) -> None:
        """Publish a contract model to Event Hub `topic`, partitioned by `key` (match_id). Waits for the ack."""
        if self._closed:
            raise RuntimeError("EventHubsBus is closed")
        k, v = encode(key, message)
        producer = await self._get_producer()
        await producer.send_and_wait(topic, value=v, key=k, headers=headers_for(message))

    async def subscribe(self, topic: str, model: type[M], group: str) -> AsyncIterator[M]:  # type: ignore[override]
        """Yield parsed models from `topic` for consumer group `group`. Unparseable messages are logged and skipped
        (re-raising would make the caller re-subscribe and re-read the same poison message forever)."""
        from aiokafka import AIOKafkaConsumer

        consumer = AIOKafkaConsumer(
            topic,
            group_id=group,
            auto_offset_reset=self.config.offset_reset,
            **self._kafka_kwargs(),
            **_CONSUMER_CONFIG,
        )
        await consumer.start()
        self._consumers.add(consumer)
        log.info("eventhubs consumer %s/%s started", topic, group)
        try:
            async for record in consumer:
                try:
                    yield decode(record.value, model)
                except (ValidationError, ValueError) as exc:
                    log.warning(
                        "skipping unparseable %s message (partition %s offset %s): %s",
                        topic,
                        record.partition,
                        record.offset,
                        str(exc).splitlines()[0],
                    )
        finally:
            self._consumers.discard(consumer)
            await consumer.stop()

    async def close(self) -> None:
        """Flush and stop the producer and every open consumer."""
        self._closed = True
        consumers, self._consumers = list(self._consumers), set()
        for c in consumers:
            try:
                await c.stop()
            except Exception:  # pragma: no cover - best effort shutdown
                log.exception("error stopping consumer")
        if self._producer is not None:
            producer, self._producer = self._producer, None
            await producer.stop()
        if self._token_provider is not None:
            await self._token_provider.close()
            self._token_provider = None
