"""Microsoft Foundry backend for `LLMClient`, via Microsoft Agent Framework (WS3b).

Every structured call is one Agent Framework agent named after the calling role (e.g. `moment_analyst`)
on a Foundry model deployment. Agent Framework emits an OpenTelemetry span per agent run, so with tracing
configured (`fulcrum.telemetry`) each role shows up by name in Application Insights / the Foundry portal.

Prompt agents (FOUNDRY_PROMPT_AGENTS, default on): roles whose system prompt is fixed (Analyst, Editor,
Reviewer) are published to the Foundry project as versioned prompt agents (`fulcrum-moment-analyst`, ...) via
`to_prompt_agent` + `agents.create_version`, and called by name through `FoundryAgent`, so they are listed,
versioned and traced in the Foundry portal. Model, instructions, temperature and the output schema live in
the agent definition (the service rejects a per-call schema for a named agent), so the reply text is parsed
here. A new version is created only when that definition changes (a fingerprint in the version metadata).
Roles with per-call prompts (Reporter, Translator, Player Lens), and any role whose publish failed, run as
local Agent Framework `Agent`s on a `FoundryChatClient` with the pydantic schema as `response_format`.

Deployments (env):
    FOUNDRY_PROJECT_ENDPOINT        https://<resource>.services.ai.azure.com/api/projects/<project>
    FOUNDRY_MODEL_DEPLOYMENT        fast model for every role except the recap (default "fast")
    FOUNDRY_MODEL_DEPLOYMENT_RECAP  stronger model for the Match Reporter (default "strong")
    FOUNDRY_PROMPT_AGENTS           1 (default) = publish and use prompt agents; 0 = local agents only

Auth is `DefaultAzureCredential`: `az login` locally, the user-assigned managed identity in Container Apps
(`AZURE_CLIENT_ID`). The identity needs the "Foundry User" role on the Foundry account/project.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import warnings
from collections.abc import Sequence
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
AGENT_PREFIX = "fulcrum-"

# (agent_name, system prompt, prompt version, output schema): one role to publish as a prompt agent.
RoleSpec = tuple[str, str, str, type[BaseModel]]


def foundry_agent_name(agent_name: str) -> str:
    """The prompt agent's name in the Foundry project, e.g. `moment_analyst` → `fulcrum-moment-analyst`."""
    return AGENT_PREFIX + agent_name.replace("_", "-")


def definition_fingerprint(deployment: str, instructions: str, options: dict[str, Any]) -> str:
    """Short hash of everything that goes into a prompt agent definition (to skip unchanged publishes)."""
    schema = options.get("response_format")
    blob = json.dumps(
        {
            "model": deployment,
            "instructions": instructions,
            "temperature": options.get("temperature"),
            "schema": schema.model_json_schema() if schema is not None else None,
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


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
    via: str = "local"  # "prompt_agent" (published Foundry agent) | "local" (Agent on the chat client)


@dataclass(frozen=True)
class PublishedAgent:
    """A role running as a Foundry prompt agent."""

    name: str  # name in the Foundry project
    version: str
    instructions: str  # the system prompt it was published with; a call with any other prompt runs locally
    agent: Any  # agent_framework.foundry.FoundryAgent


@dataclass
class FoundryLLM:
    """`LLMClient` over Foundry deployments; one shared chat client per deployment."""

    endpoint: str
    fast: ModelProfile
    recap: ModelProfile
    credential: Any = None
    prompt_agents: bool = False
    calls: list[CallStat] = field(default_factory=list, repr=False)
    published: dict[str, PublishedAgent] = field(default_factory=dict, repr=False)
    _clients: dict[str, Any] = field(default_factory=dict, repr=False)
    _project: Any = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> FoundryLLM:
        """Build from `FOUNDRY_*` env vars (see module docstring)."""
        endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
        if not endpoint:
            raise RuntimeError("FULCRUM_LLM=foundry needs FOUNDRY_PROJECT_ENDPOINT (see docs/AZURE_SETUP.md)")
        fast = os.getenv("FOUNDRY_MODEL_DEPLOYMENT", "fast")
        recap = os.getenv("FOUNDRY_MODEL_DEPLOYMENT_RECAP", "strong")
        recap_temp = os.getenv("FOUNDRY_RECAP_SUPPORTS_TEMPERATURE", "0") == "1"
        return cls(
            endpoint=endpoint,
            fast=ModelProfile(fast),
            recap=ModelProfile(recap, recap_temp),
            prompt_agents=os.getenv("FOUNDRY_PROMPT_AGENTS", "1") == "1",
        )

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

    def _project_client(self) -> Any:
        if self._project is None:
            from azure.ai.projects.aio import AIProjectClient

            self._client(self.fast.deployment)  # creates the shared credential
            self._project = AIProjectClient(endpoint=self.endpoint, credential=self.credential)
        return self._project

    async def publish_agents(self, roles: Sequence[RoleSpec]) -> dict[str, str]:
        """Publish `roles` as Foundry prompt agents (a new version only if changed) and route their calls there.

        Returns {Foundry agent name: version}. A role that fails to publish is logged and stays local.
        """
        from agent_framework import Agent
        from agent_framework.foundry import FoundryAgent, to_prompt_agent
        from azure.core.exceptions import ResourceNotFoundError

        project = self._project_client()
        out: dict[str, str] = {}
        for agent_name, instructions, prompt_version, schema in roles:
            name = foundry_agent_name(agent_name)
            profile = self.profile_for(agent_name)
            options = self.options_for(agent_name, schema)
            fingerprint = definition_fingerprint(profile.deployment, instructions, options)
            try:
                version = None
                try:
                    latest = (await project.agents.get(name)).versions.latest
                    if (latest.metadata or {}).get("fingerprint") == fingerprint:
                        version = latest.version
                except ResourceNotFoundError:
                    pass
                if version is None:
                    local = Agent(
                        client=self._client(profile.deployment),
                        name=name,
                        instructions=instructions,
                        default_options=options,
                    )
                    with warnings.catch_warnings():  # to_prompt_agent is marked experimental in AF 1.19
                        warnings.simplefilter("ignore")
                        definition = to_prompt_agent(local)
                    created = await project.agents.create_version(
                        name,
                        definition=definition,
                        description=f"Fulcrum {agent_name.replace('_', ' ')} ({prompt_version})",
                        metadata={"fingerprint": fingerprint, "prompt_version": prompt_version},
                    )
                    version = created.version
                    log.info("foundry: published %s v%s (%s)", name, version, prompt_version)
            except Exception:  # noqa: BLE001 — publishing is an upgrade; the local path still works
                log.exception("foundry: could not publish %s; it runs as a local agent", name)
                continue
            agent = FoundryAgent(project_client=project, agent_name=name, agent_version=version)
            self.published[agent_name] = PublishedAgent(name, version, instructions, agent)
            out[name] = version
        return out

    async def structured(self, *, system: str, user: str, schema: type[T], agent_name: str) -> T:
        """Run `agent_name` as an Agent Framework agent and return its output validated against `schema`.

        A published role (called with the system prompt it was published with) runs as its Foundry prompt
        agent; anything else runs as a local `Agent` with the schema as `response_format`.
        """
        from agent_framework import Agent

        profile = self.profile_for(agent_name)
        published = self.published.get(agent_name)
        if published is not None and published.instructions != system:
            published = None  # a different prompt than the published one: never mix them
        via = "prompt_agent" if published is not None else "local"
        started = time.monotonic()
        try:
            if published is not None:
                response = await published.agent.run(user)
            else:
                agent = Agent(client=self._client(profile.deployment), name=agent_name, instructions=system)
                response = await agent.run(user, options=self.options_for(agent_name, schema))
        except BaseException:
            self.calls.append(CallStat(agent_name, profile.deployment, time.monotonic() - started, ok=False, via=via))
            raise
        usage = response.usage_details or {}
        self.calls.append(
            CallStat(
                agent_name,
                profile.deployment,
                time.monotonic() - started,
                int(usage.get("input_token_count") or 0),
                int(usage.get("output_token_count") or 0),
                via=via,
            )
        )
        if published is not None:  # the schema is enforced service-side; parse the JSON text
            return schema.model_validate_json(response.text)
        value = response.value
        if value is None:  # the model returned text that did not parse as the schema
            raise ValueError(f"{agent_name}: no structured output from {profile.deployment}: {response.text[:200]!r}")
        if not isinstance(value, schema):
            value = schema.model_validate(value)
        return value

    async def warm_up(self, roles: Sequence[RoleSpec] = ()) -> None:
        """Fetch the Entra token, open both clients and (if enabled) publish `roles`, before the first moment.

        The first call otherwise pays for the credential chain and token fetch (several seconds locally), which
        is enough to blow the Analyst's 6 s call budget and send the first moment of a match to the fallback.
        """
        for profile in (self.fast, self.recap):
            self._client(profile.deployment)
        started = time.monotonic()
        await self.credential.get_token(TOKEN_SCOPE)
        log.info("foundry: token ready in %.1fs (%s, %s)", time.monotonic() - started, self.fast, self.recap)
        if self.prompt_agents and roles:
            published = await self.publish_agents(roles)
            log.info("foundry: prompt agents %s", published or "none (all roles local)")

    async def aclose(self) -> None:
        """Release the project client's and the credential's HTTP sessions."""
        if self._project is not None:
            await self._project.close()
        if self.credential is not None and hasattr(self.credential, "close"):
            await self.credential.close()
