# `fulcrum.bus`: the event bus (WS5)

Every service publishes and consumes only through `get_bus()`. `FULCRUM_BUS` selects the implementation.

| `FULCRUM_BUS` | Class | Use |
|---|---|---|
| `memory` (default) | `MemoryBus` (`base.py`) | tests, `make dev`, the single-container fallback |
| `eventhubs` | `EventHubsBus` (`eventhubs.py`) | Azure: Event Hubs over its Kafka endpoint (`aiokafka`, `azure` extra) |

Semantics (identical for both): topic = Event Hub, key = `match_id` (per-match ordering), payload = the
contract model as pydantic JSON by alias, `subscribe(topic, model, group)`. Every consumer group receives every
message once.

## Event Hubs specifics
- **Auth.** `EVENTHUBS_CONNECTION_STRING` uses SASL_SSL PLAIN (user `$ConnectionString`); this is the tested
  path, and Azure reads the value from Key Vault. With only `EVENTHUBS_NAMESPACE` set, or with
  `FULCRUM_EVENTHUBS_AUTH=managed_identity`, it uses OAUTHBEARER with an Entra token from `DefaultAzureCredential`
  (`AZURE_CLIENT_ID` = user-assigned MI). That path is coded and unit-tested but has not been run live.
- **Publish** queues the record and returns. The producer batches for 5 ms, keeps per-partition order, logs
  delivery failures and counts them in `bus.delivery_errors`. `close()` flushes. `FULCRUM_EVENTHUBS_SYNC=1`
  waits for every ack instead, which limits a publisher to about one message per round trip (~25/s from the UK).
- **Subscribe**: a new group starts at the live edge (`FULCRUM_EVENTHUBS_OFFSET_RESET=latest`, the default),
  and a known group resumes from its committed offset. Offsets are auto-committed every 2 s. Unparseable
  messages are logged and skipped, so one poison message cannot cause an endless re-subscribe loop.
- Tuning follows the Event Hubs Kafka guidance: idle connections close at 180 s, below the service's 240 s;
  `request_timeout_ms` is 60 s and the session timeout 30 s.

## Tests
- `tests/bus/test_eventhubs.py`: offline. Covers config parsing, (de)serialisation (including the `pass`
  alias and the insights envelope), and fan-out, ordering and poison-skip against a fake `aiokafka`.
- `tests/bus/test_eventhubs_live.py`: opt-in round trip through the real namespace:
  `FULCRUM_LIVE_EVENTHUBS=1 uv run pytest -q -s tests/bus/test_eventhubs_live.py` (reads `.env`; group `$Default`).

## Run the services out of process locally (real Event Hubs)
```bash
uv sync --extra dev --extra azure
FULCRUM_BUS=eventhubs FULCRUM_SERVICE=engine uv run --env-file .env python -m fulcrum &
FULCRUM_BUS=eventhubs FULCRUM_SERVICE=agents uv run --env-file .env python -m fulcrum &
FULCRUM_BUS=eventhubs FULCRUM_SERVICE=api PORT=8000 uv run --env-file .env python -m fulcrum &
FULCRUM_BUS=eventhubs FULCRUM_SERVICE=sim PORT=8001 FULCRUM_SIM_AUTOSTART=1 uv run --env-file .env python -m fulcrum &
```
Don't `source .env` in bash: the unquoted connection string is cut at its first `;`, and the config then raises
"no SharedAccessKey".
