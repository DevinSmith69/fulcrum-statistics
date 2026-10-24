"""Test doubles and canned LLM outputs for the agent tests (no network, ever)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from fulcrum.agents.schemas import MomentContext
from fulcrum.agents.templates import fallback_explanation, fallback_variants
from fulcrum.llm import FakeLLM
from tests.agents.factories import AWAY, HOME


class ScriptedLLM(FakeLLM):
    """FakeLLM whose canned value may be a list (one item per call; the last one repeats).

    `delays` adds a sleep per agent; `hooks` run before a call (e.g. to await an event).
    """

    def __init__(
        self,
        canned: dict[str, Any] | None = None,
        fail_times: int = 0,
        delays: dict[str, float] | None = None,
        hooks: dict[str, Callable[[str], Awaitable[None]]] | None = None,
    ) -> None:
        super().__init__({}, fail_times=fail_times)
        self.script = canned or {}
        self.delays = delays or {}
        self.hooks = hooks or {}

    async def structured(self, *, system, user, schema, agent_name):  # type: ignore[override]
        if agent_name in self.hooks:
            await self.hooks[agent_name](user)
        if self.delays.get(agent_name):
            await asyncio.sleep(self.delays[agent_name])
        value = self.script.get(agent_name)
        if isinstance(value, list):
            value = value.pop(0) if len(value) > 1 else value[0]
        if value is not None:
            self.canned[agent_name] = value
        else:
            self.canned.pop(agent_name, None)
        return await super().structured(system=system, user=user, schema=schema, agent_name=agent_name)

    def agents_called(self) -> list[str]:
        return [c["agent"] for c in self.calls]


def template_canned(ctx: MomentContext) -> dict[str, Any]:
    """LLM outputs derived from templates: always valid, so the LLM path should PASS."""
    return {
        "moment_analyst": fallback_explanation(ctx).model_dump(mode="json"),
        "persona_editor": {"variants": [v.model_dump(mode="json") for v in fallback_variants(ctx)]},
    }


# Hand-written "good LLM" output for the goal moment in tests.agents.factories (NBA 1-0, Brennick 67').
GOAL_ANALYST = {
    "headline": "Brennick's 0.34 xG finish tilts it to Northbridge",
    "why_it_matters": "Brennick converts a 0.34 xG chance on 67', lifting Northbridge Athletic from 48% to 71% "
    "to win: the first goal turns a balanced game into theirs to lose.",
    "tactical_tag": "turning_point",
    "key_fact_keys": ["xg", "wp_scorer_before", "wp_scorer_after", "minute"],
}


def goal_variants(
    casual_body: str = "Brennick scores! Northbridge Athletic lead 1-0 and are now big favourites.",
) -> dict:
    return {
        "variants": [
            {
                "persona": "analyst",
                "headline": "Brennick 0.34 xG finish: Northbridge 48% → 71%",
                "body": "Aldane's 7.8-difficulty pass sets up a 0.34 xG chance from 14.2 m, struck at 98 km/h. "
                "Win probability jumps 23 points to 71%.",
                "commentary_line": "A 0.34 chance, finished at 98 km/h. That changes everything.",
            },
            {
                "persona": "casual",
                "headline": "Brennick breaks the deadlock!",
                "body": casual_body,
                "commentary_line": "Brennick! Northbridge lead and the place erupts.",
            },
            {
                "persona": "club_fan",
                "perspective_team_id": HOME,
                "headline": "BRENNICK! Get in, Northbridge!",
                "body": "Our number one finisher does it again: 1-0 and we're up to 71% to win this. Keep going!",
                "commentary_line": "Brennick, of course it's Brennick!",
            },
            {
                "persona": "club_fan",
                "perspective_team_id": AWAY,
                "headline": "Gutted: Brennick punishes Rovers",
                "body": "We switched off and Brennick made us pay. 1-0 down on 67', but there is time to respond.",
                "commentary_line": "Riverside Rovers have a mountain to climb now.",
            },
        ]
    }
