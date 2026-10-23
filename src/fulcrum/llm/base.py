from __future__ import annotations

import json
import os
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    async def structured(self, *, system: str, user: str, schema: type[T], agent_name: str) -> T:
        """Return a validated instance of `schema`. Raise on unrecoverable failure."""


class FakeLLM:
    """Returns `schema` filled from canned responses keyed by agent_name, else a minimal valid object.

    Tests register canned payloads: FakeLLM({"moment_analyst": {...}}).
    Set `fail_times` to simulate failures for repair/fallback tests.
    """

    def __init__(self, canned: dict[str, dict[str, Any]] | None = None, fail_times: int = 0) -> None:
        self.canned = canned or {}
        self.fail_times = fail_times
        self.calls: list[dict[str, Any]] = []

    async def structured(self, *, system: str, user: str, schema: type[T], agent_name: str) -> T:
        self.calls.append({"agent": agent_name, "system": system, "user": user})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError(f"FakeLLM simulated failure for {agent_name}")
        if agent_name in self.canned:
            return schema.model_validate(self.canned[agent_name])
        raise KeyError(
            f"FakeLLM has no canned response for agent '{agent_name}'. "
            f"Register one: FakeLLM({{'{agent_name}': {{...}}}}). "
            f"Schema: {json.dumps(schema.model_json_schema())[:300]}"
        )


def get_llm() -> LLMClient:
    kind = os.getenv("FULCRUM_LLM", "fake")
    if kind == "fake":
        return FakeLLM()
    if kind == "foundry":
        from .foundry import FoundryLLM  # implemented in WS3

        return FoundryLLM.from_env()
    raise ValueError(f"Unknown FULCRUM_LLM={kind}")
