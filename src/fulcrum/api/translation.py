"""On-demand translation for the API, delegating to `fulcrum.agents.translate` (WS3) when it is available.

Expected WS3 entry points (any may be sync or async; see docs/HANDOFFS.md):
    translate_variants(variants: list[InsightVariant], language: str) -> list[InsightVariant]
    translate_cue(cue: OverlayCue, language: str) -> OverlayCue                     (optional)
    translate_recap(recap: Recap, language: str) -> Recap                           (optional)

Until WS3 lands — or if a call fails — this returns the English original unchanged (the agreed stub), so
the overlay keeps working; results are cached per (id, language).
"""

from __future__ import annotations

import importlib
import inspect
import logging
from collections.abc import Callable
from typing import Any

from fulcrum.contracts import Insight, InsightVariant, OverlayCue, Recap

log = logging.getLogger("fulcrum.api.translation")


def _agents_fn(name: str) -> Callable[..., Any] | None:
    try:
        mod = importlib.import_module("fulcrum.agents.translate")
    except Exception:  # module missing or still a stub that fails to import
        return None
    fn = getattr(mod, name, None)
    return fn if callable(fn) else None


async def _call(fn: Callable[..., Any], *args: Any) -> Any:
    out = fn(*args)
    return await out if inspect.isawaitable(out) else out


class Translator:
    """Caching facade over the agents' translator, with an English passthrough fallback."""

    def __init__(self) -> None:
        self._variants: dict[tuple[str, str], list[InsightVariant]] = {}
        self._cues: dict[tuple[str, str], OverlayCue] = {}
        self._recaps: dict[tuple[str, str, str], Recap] = {}

    async def variants(self, insight: Insight, variants: list[InsightVariant], language: str) -> list[InsightVariant]:
        """`variants` (English) rendered in `language`. Cached per insight + language + persona set."""
        if not variants or all(v.language == language for v in variants):
            return variants
        key = (f"{insight.insight_id}:{','.join(sorted({str(v.persona) for v in variants}))}", language)
        if key in self._variants:
            return self._variants[key]
        out = variants
        fn = _agents_fn("translate_variants")
        if fn is not None:
            try:
                got = await _call(fn, variants, language)
                if isinstance(got, list) and all(isinstance(v, InsightVariant) for v in got) and got:
                    out = got
            except Exception:
                log.exception("translate_variants failed for %s → %s; serving English", insight.insight_id, language)
        if out is not variants:  # don't cache the fallback, so a later-arriving translator is used
            self._variants[key] = out
        return out

    async def cue(self, cue: OverlayCue, language: str) -> OverlayCue:
        if cue.language == language:
            return cue
        key = (cue.cue_id, language)
        if key in self._cues:
            return self._cues[key]
        fn = _agents_fn("translate_cue")
        if fn is not None:
            try:
                got = await _call(fn, cue, language)
                if isinstance(got, OverlayCue):
                    self._cues[key] = got
                    return got
            except Exception:
                log.exception("translate_cue failed for %s → %s; serving English", cue.cue_id, language)
        return cue

    async def recap(self, recap: Recap, language: str) -> Recap:
        if recap.language == language:
            return recap
        key = (recap.match_id, str(recap.persona), language)
        if key in self._recaps:
            return self._recaps[key]
        fn = _agents_fn("translate_recap")
        if fn is not None:
            try:
                got = await _call(fn, recap, language)
                if isinstance(got, Recap):
                    self._recaps[key] = got
                    return got
            except Exception:
                log.exception("translate_recap failed for %s → %s; serving English", recap.match_id, language)
        return recap
