"""`python -m fulcrum.agents` — run the agents service against the configured bus and LLM."""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv

from fulcrum.bus import get_bus
from fulcrum.llm import get_llm

from .service import AgentService


def main() -> None:
    """Entry point for the agents Container App (FULCRUM_SERVICE=agents)."""
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(AgentService(get_bus(), get_llm()).run())


if __name__ == "__main__":
    main()
