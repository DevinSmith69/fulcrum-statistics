"""On-demand translation and lazy recaps for the API, delegating to the agents layer (WS3) when present.

The agents expose them on `fulcrum.agents.AgentService` (see docs/HANDOFFS.md, WS3 → WS2):
    await svc.translate(insight_id, language)          -> list[InsightVariant]   ([] if unknown / failed)
    await svc.translate_cues(insight_id, language)     -> list[OverlayCue]
    await svc.recap(match_id, persona, language)       -> Recap | None
Without an agents service — or when a call fails or returns nothing — the English original is served
unchanged (the agreed stub), so the overlay keeps working. Successful results are cached per id + language.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from fulcrum.contracts import Insight, InsightVariant, OverlayCue, Persona, Recap

log = logging.getLogger("fulcrum.api.translation")


class AgentHooks(Protocol):
    async def translate(self, insight_id: str, language: str) -> list[InsightVariant]: ...
    async def translate_cues(self, insight_id: str, language: str) -> list[OverlayCue]: ...
    async def recap(self, match_id: str, persona: Persona | str, language: str) -> Recap | None: ...


def _slot(x: InsightVariant | OverlayCue) -> tuple[Any, ...]:
    """What makes a variant/cue 'the same' across languages."""
    base = (str(x.persona), x.perspective_team_id, x.perspective_player_id)
    return (*base, str(x.template)) if isinstance(x, OverlayCue) else base


class Translator:
    """Caching facade over the agents' on-demand hooks, with an English passthrough fallback."""

    def __init__(self, agents: AgentHooks | None = None) -> None:
        self.agents = agents
        self._variants: dict[tuple[str, str], list[InsightVariant]] = {}
        self._cues: dict[tuple[str, str], list[OverlayCue]] = {}

    async def _hook(self, name: str, *args: Any) -> Any:
        fn = getattr(self.agents, name, None)
        if fn is None:
            return None
        try:
            return await fn(*args)
        except Exception:
            log.exception("agents.%s%s failed; serving English", name, args)
            return None

    async def variants(self, insight: Insight, variants: list[InsightVariant], language: str) -> list[InsightVariant]:
        """`variants` (normally English) rendered in `language`, matched slot by slot."""
        if not variants or all(v.language == language for v in variants):
            return variants
        key = (insight.insight_id, language)
        if key not in self._variants:
            got = await self._hook("translate", insight.insight_id, language)
            if not got:
                return variants  # not cached: a later request may succeed
            self._variants[key] = list(got)
        by_slot = {_slot(v): v for v in self._variants[key]}
        return [by_slot.get(_slot(v), v) for v in variants]

    async def cue(self, cue: OverlayCue, language: str) -> OverlayCue:
        """`cue` in `language` (the agents translate all cues of an insight at once)."""
        if cue.language == language or cue.insight_id is None:
            return cue
        key = (cue.insight_id, language)
        if key not in self._cues:
            got = await self._hook("translate_cues", cue.insight_id, language)
            if not got:
                return cue
            self._cues[key] = list(got)
        return next((c for c in self._cues[key] if _slot(c) == _slot(cue)), cue)

    async def recap(self, match_id: str, persona: Persona | str, language: str) -> Recap | None:
        """Lazily generated recap for a persona + language (None if the agents can't make one)."""
        got = await self._hook("recap", match_id, persona, language)
        return got if isinstance(got, Recap) else None
