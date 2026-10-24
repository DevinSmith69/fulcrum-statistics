"""`python -m fulcrum.engine` — run the engine service against the configured bus (FULCRUM_BUS)."""

import asyncio
import logging

from .service import run

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
