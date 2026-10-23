"""LLM access abstraction so the agent layer is testable without Azure.

FULCRUM_LLM=fake     deterministic canned JSON (tests, overnight builds, CI)
FULCRUM_LLM=foundry  Microsoft Foundry model deployment via Microsoft Agent Framework (WS3)
"""

from .base import FakeLLM, LLMClient, get_llm  # noqa: F401
