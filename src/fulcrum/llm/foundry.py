"""Microsoft Foundry backend for `LLMClient`, via Microsoft Agent Framework (WS3b).

Every structured call is one Agent Framework `Agent` (named after the calling role, e.g. `moment_analyst`)
running on a `FoundryChatClient` bound to a Foundry model deployment, with the pydantic schema as
`response_format`. Agent Framework emits an OpenTelemetry span per agent run, so with tracing configured
(`fulcrum.telemetry`) each role shows up by name in Application Insights / the Foundry portal.

Deployments (env):
    FOUNDRY_PROJECT_ENDPOINT        https://<resource>.services.ai.azure.com/api/projects/<project>
    FOUNDRY_MODEL_DEPLOYMENT        fast model for every role except the recap (default "fast")
    FOUNDRY_MODEL_DEPLOYMENT_RECAP  stronger model for the Match Reporter (default "strong")

Auth is `DefaultAzureCredential`: `az login` locally, the user-assigned managed identity in Container Apps
(`AZURE_CLIENT_ID`). The identity needs the "Foundry User" role on the Foundry account/project.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

log = logging.getLogger("fulcrum.llm")

T = TypeVar("T", bound=BaseModel)

RECAP_AGENTS = frozenset({"match_reporter"})
# AI_PROMPT_DESIGN §2: 0.4 for the Editor, 0.2 for everything else (fast model only; see `ModelProfile`).
TEMPERATURES: dict[str, float] = {"persona_editor": 0.4}
DEFAULT_TEMPERATURE = 0.2
TOKEN_SCOPE = "https://ai.azure.com/.default"  # what the Foundry project client requests


@dataclass(frozen=True)
class ModelProfile:
    """One Foundry deployment and what it accepts."""

    deployment: str
    supports_temperature: bool = True  # gpt-5-mini (reasoning) rejects any temperature other than 1


@dataclass(frozen=True)
class CallStat:
    """One LLM call, for `scripts/eval_live.py` (latency and token counts as reported by the service)."""

    agent: str
    deployment: str
    seconds: float
    input_tokens: int = 0
    output_tokens: int = 0
    ok: bool = True


@dataclass
class FoundryLLM:
    """`LLMClient` over Foundry deployments; one shared chat client per deployment."""

    endpoint: str
    fast: ModelProfile
    recap: ModelProfile
    credential: Any = None
    calls: list[CallStat] = field(default_factory=list, repr=False)
    _clients: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_env(cls) -> FoundryLLM:
        """Build from `FOUNDRY_*` env vars (see module docstring)."""
        endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
        if not endpoint:
            raise RuntimeError("FULCRUM_LLM=foundry needs FOUNDRY_PROJECT_ENDPOINT (see docs/AZURE_SETUP.md)")
        fast = os.getenv("FOUNDRY_MODEL_DEPLOYMENT", "fast")
        recap = os.getenv("FOUNDRY_MODEL_DEPLOYMENT_RECAP", "strong")
        recap_temp = os.getenv("FOUNDRY_RECAP_SUPPORTS_TEMPERATURE", "0") == "1"
        return cls(endpoint=endpoint, fast=ModelProfile(fast), recap=ModelProfile(recap, recap_temp))

    def profile_for(self, agent_name: str) -> ModelProfile:
        """The deployment a role runs on: the Match Reporter on the stronger model, everyone else on fast."""
        return self.recap if agent_name in RECAP_AGENTS else self.fast

    def options_for(self, agent_name: str, schema: type[BaseModel]) -> dict[str, Any]:
        """Chat options for one call: the response schema, plus temperature where the model accepts it."""
        options: dict[str, Any] = {"response_format": schema}
        if self.profile_for(agent_name).supports_temperature:
            options["temperature"] = TEMPERATURES.get(agent_name, DEFAULT_TEMPERATURE)
        return options

    def _client(self, deployment: str) -> Any:
        if deployment not in self._clients:
            from agent_framework.foundry import FoundryChatClient
            from azure.identity.aio import DefaultAzureCredential

            if self.credential is None:
                self.credential = DefaultAzureCredential()
            self._clients[deployment] = FoundryChatClient(
                project_endpoint=self.endpoint, model=deployment, credential=self.credential
            )
        return self._clients[deployment]

    async def structured(self, *, system: str, user: str, schema: type[T], agent_name: str) -> T:
        """Run `agent_name` as an Agent Framework agent and return its output validated against `schema`."""
        from agent_framework import Agent

        profile = self.profile_for(agent_name)
        agent = Agent(client=self._client(profile.deployment), name=agent_name, instructions=system)
        started = time.monotonic()
        try:
            response = await agent.run(user, options=self.options_for(agent_name, schema))
        except BaseException:
            self.calls.append(CallStat(agent_name, profile.deployment, time.monotonic() - started, ok=False))
            raise
        usage = response.usage_details or {}
        self.calls.append(
            CallStat(
                agent_name,
                profile.deployment,
                time.monotonic() - started,
                int(usage.get("input_token_count") or 0),
                int(usage.get("output_token_count") or 0),
            )
        )
        value = response.value
        if value is None:  # the model returned text that did not parse as the schema
            raise ValueError(f"{agent_name}: no structured output from {profile.deployment}: {response.text[:200]!r}")
        if not isinstance(value, schema):
            value = schema.model_validate(value)
        return value

    async def warm_up(self) -> None:
        """Fetch the Entra token and open both clients before the first moment arrives.

        The first call otherwise pays for the credential chain and token fetch (several seconds locally), which
        is enough to blow the Analyst's 6 s call budget and send the first moment of a match to the fallback.
        """
        for profile in (self.fast, self.recap):
            self._client(profile.deployment)
        started = time.monotonic()
        await self.credential.get_token(TOKEN_SCOPE)
        log.info("foundry: token ready in %.1fs (%s, %s)", time.monotonic() - started, self.fast, self.recap)

    async def aclose(self) -> None:
        """Release the credential's HTTP session."""
        if self.credential is not None and hasattr(self.credential, "close"):
            await self.credential.close()
