"""Event bus abstraction. Every service talks to the bus ONLY through `get_bus()`.

FULCRUM_BUS=memory   (default) in-process asyncio fan-out — tests & single-process dev
FULCRUM_BUS=eventhubs           Azure Event Hubs via its Kafka endpoint (WS5 implements)
"""

from .base import Bus, get_bus  # noqa: F401
