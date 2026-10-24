"""Bus → read models → WebSocket fan-out.

`Hub` consumes the four topics the API needs (API_CONTRACT §1 consumer groups), updates the per-match
read models, and pushes raw items to every subscribed `Connection`. Each connection personalises in its
own sender task, so one slow translation never stalls the hub or other viewers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket
from pydantic import BaseModel

from fulcrum.bus import Bus
from fulcrum.contracts import (
    TOPIC_EVENTS,
    TOPIC_INSIGHTS,
    TOPIC_MOMENTS,
    TOPIC_STATE,
    CheckStatus,
    EventType,
    Insight,
    InsightsEnvelope,
    MatchEvent,
    MatchState,
    Moment,
    OverlayCue,
    Recap,
    ServerMessage,
    ViewerPrefs,
)

from . import personalise as ps
from .readmodel import MatchReadModel, Registry
from .translation import Translator

log = logging.getLogger("fulcrum.api.hub")
GROUPS = {TOPIC_EVENTS: "api-feed", TOPIC_STATE: "api", TOPIC_MOMENTS: "api", TOPIC_INSIGHTS: "api"}
CONNECTION_QUEUE_MAX = 5_000
# A catch-up snapshot translates only the newest insights on demand (one LLM call each); older ones use
# cached translations or English, so switching language never stalls the live feed for long.
HELLO_TRANSLATE_LATEST = 5


def dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)


_BACKGROUND: set[asyncio.Task[None]] = set()  # background recap writers (strong references)


class Connection:
    """One WebSocket viewer: prefs, current subscription and an outbound queue."""

    def __init__(self, ws: WebSocket, hub: Hub) -> None:
        self.ws = ws
        self.hub = hub
        self.prefs = ViewerPrefs()
        self.match_id: str | None = None
        self.queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=CONNECTION_QUEUE_MAX)
        self._last_player_tag_ms: int | None = None
        self._recap_sent = False  # any recap sent to this viewer
        self._recap_exact = False  # the viewer's own persona + language recap sent
        self._recap_task: asyncio.Task[None] | None = None

    def push(self, kind: str, item: Any) -> None:
        """Called by the hub. Drops (and logs) if the viewer is hopelessly behind."""
        try:
            self.queue.put_nowait((kind, item))
        except asyncio.QueueFull:
            log.warning("dropping %s for a slow viewer on %s", kind, self.match_id)

    async def send(self, type_: str, payload: dict[str, Any], match_id: str | None = None) -> None:
        msg = ServerMessage(type=type_, match_id=match_id or self.match_id, payload=payload)
        await self.ws.send_json(msg.model_dump(mode="json"))

    async def error(self, code: str, message: str) -> None:
        await self.send("error", {"code": code, "message": message})

    async def hello(self, rm: MatchReadModel) -> None:
        """Catch-up snapshot for the current prefs (API_CONTRACT §3 `hello`)."""
        items = list(rm.insights.values())
        cutoff = len(items) - HELLO_TRANSLATE_LATEST
        # Concurrently: the newest few may each need an LLM call (translation / player lens); the rest are cache-only.
        insights = await asyncio.gather(
            *(
                ps.personalise_insight(i, self.prefs, self.hub.translator, translate=n >= cutoff)
                for n, i in enumerate(items)
            )
        )
        await self.send(
            "hello",
            {
                "match_id": rm.match_id,
                "home": dump(rm.home) if rm.home else None,
                "away": dump(rm.away) if rm.away else None,
                "state": dump(rm.state) if rm.state else None,
                "winprob": [dump(p) for p in rm.winprob],
                "moments": [dump(m) for m in rm.moments],
                "insights": [dump(i) for i in insights],
                "status": rm.status,
                "period_start_clock_ms": dict(rm.period_starts),
            },
            rm.match_id,
        )

    async def sender(self) -> None:
        """Drain the queue, personalising each item for this viewer."""
        while True:
            kind, item = await self.queue.get()
            item_match = item.get("match_id") if isinstance(item, dict) else getattr(item, "match_id", None)
            if item_match is not None and item_match != self.match_id:
                continue  # stale item from a previous subscription
            await self._deliver(kind, item)

    async def _deliver(self, kind: str, item: Any) -> None:
        rm = self.hub.registry.get(self.match_id or "")
        if kind == "hello":
            if rm is not None:
                await self.hello(rm)
                if rm.status == "finished" and rm.recaps:  # a (re)subscribe after full time gets its recap too
                    await self._offer_recap(rm)
        elif kind in ("pong", "error"):
            await self.send(kind, item)
        elif kind == "event":
            await self.send("event", dump(item))
            if rm is not None and self.prefs.followed_player_id:
                await self._maybe_player_tag(rm, item)
        elif kind in ("state", "moment"):
            await self.send(kind, dump(item))
        elif kind == "insight":
            await self.send("insight", dump(await ps.personalise_insight(item, self.prefs, self.hub.translator)))
        elif kind == "cue":
            cue = await ps.personalise_cue(item, self.prefs, self.hub.translator)
            if cue is not None:
                await self.send("cue", dump(cue))
        elif kind == "recap":
            # Each arriving recap is a nudge to offer this viewer the best one we have.
            if rm is not None:
                await self._offer_recap(rm)
        elif kind == "recap_ready":  # the viewer's own persona + language recap, written in the background
            if (str(item.persona), item.language) == (str(self.prefs.persona), self.prefs.language):
                self._recap_sent = self._recap_exact = True
                await self.send("recap", dump(item))
        elif kind == "match_end":
            await self.send("match_end", {k: v for k, v in item.items() if k != "match_id"})

    async def _offer_recap(self, rm: MatchReadModel) -> None:
        """Send the viewer's own recap if we have it; else the closest one now, and write theirs in the background.

        The reporter runs on the reasoning model (30-60 s), so a viewer whose persona/language differs from the
        recap published at full time (casual/en) would otherwise stare at a placeholder for a minute.
        """
        if self._recap_exact:
            return
        exact = rm.recaps.get((str(self.prefs.persona), self.prefs.language))
        if exact is not None:
            self._recap_sent = self._recap_exact = True
            await self.send("recap", dump(exact))
            return
        nearest = ps.pick_recap(rm.recaps, self.prefs)
        if nearest is not None and not self._recap_sent:
            self._recap_sent = True
            await self.send("recap", dump(nearest))
        if self._recap_task is None:
            self._recap_task = task = asyncio.create_task(self._write_recap(rm, self.prefs.model_copy()))
            _BACKGROUND.add(task)  # asyncio keeps only weak references; this survives a prefs reset
            task.add_done_callback(_BACKGROUND.discard)

    async def _write_recap(self, rm: MatchReadModel, prefs: ViewerPrefs) -> None:
        try:
            got = await self.hub.best_recap(rm, prefs)
        except Exception:
            log.exception("recap for %s (%s/%s) failed", rm.match_id, prefs.persona, prefs.language)
            return
        if got is not None and (str(got.persona), got.language) == (str(prefs.persona), prefs.language):
            self.push("recap_ready", got)
        elif prefs == self.prefs:
            self._recap_task = None  # only a stand-in came back: let the next nudge or hello try again

    async def _maybe_player_tag(self, rm: MatchReadModel, ev: MatchEvent) -> None:
        last = self._last_player_tag_ms
        if last is not None and ev.clock_ms - last < ps.PLAYER_TAG_MIN_GAP_MS:
            return
        cue = ps.player_tag_cue(rm, ev, self.prefs)
        if cue is not None:
            self._last_player_tag_ms = ev.clock_ms
            await self.send("cue", dump(cue))

    def reset_prefs_state(self) -> None:
        self._last_player_tag_ms = None
        self._recap_sent = self._recap_exact = False
        # Don't cancel a recap being written: it is cached for everyone, and a result for the old prefs is
        # ignored on delivery (persona/language mismatch).
        self._recap_task = None


class Hub:
    """Consumes the bus for every match and fans out to subscribed connections."""

    def __init__(self, bus: Bus, registry: Registry, translator: Translator) -> None:
        self.bus = bus
        self.registry = registry
        self.translator = translator
        self.subscribers: dict[str, set[Connection]] = {}
        # Extra in-process consumers of every new moment (e.g. the pipeline's "skip to the next moment").
        self.moment_listeners: list[Callable[[Moment], None]] = []
        # Editorial gate: returns True for an insight/cue it holds back (the producer desk); None = no gate.
        self.gate: Callable[[Insight | OverlayCue | Recap], bool] | None = None
        self._tasks: list[asyncio.Task[None]] = []

    # ------------------------------------------------------------------ subscriptions

    def subscribe(self, conn: Connection, match_id: str) -> None:
        self.unsubscribe(conn)
        conn.match_id = match_id
        self.subscribers.setdefault(match_id, set()).add(conn)

    def unsubscribe(self, conn: Connection) -> None:
        if conn.match_id and conn.match_id in self.subscribers:
            self.subscribers[conn.match_id].discard(conn)

    async def best_recap(self, rm: MatchReadModel, prefs: ViewerPrefs) -> Recap | None:
        """Exact persona+language recap if cached, else ask the agents (lazy, cached), else the best fallback."""
        exact = rm.recaps.get((str(prefs.persona), prefs.language))
        if exact is not None:
            return exact
        if rm.recaps:  # the agents only answer after full time; one recap having arrived proves it
            generated = await self.translator.recap(rm.match_id, prefs.persona, prefs.language)
            # Keep only a real answer to what was asked: the agents' template fallback is English casual-ish copy,
            # and caching it would replace the good casual/en recap published at full time.
            wanted = (str(prefs.persona), prefs.language)
            if (
                generated is not None
                and generated.check_status != CheckStatus.FALLBACK
                and (str(generated.persona), generated.language) == wanted
            ):
                rm.add_recap(generated)
                return generated
            return ps.pick_recap(rm.recaps, prefs) or generated
        return ps.pick_recap(rm.recaps, prefs)

    def _fanout(self, match_id: str, kind: str, item: Any) -> None:
        for conn in list(self.subscribers.get(match_id, ())):
            conn.push(kind, item)

    # ------------------------------------------------------------------ handlers (also called directly in tests)

    def on_event(self, ev: MatchEvent) -> None:
        rm = self.registry.get_or_create(ev.match_id)
        if ev.type == EventType.MATCH_START:
            self.registry.learn_teams(rm, ev)
        if not rm.add_event(ev):
            return
        self._fanout(ev.match_id, "event", ev)
        if ev.type == EventType.MATCH_END:
            end = {"match_id": ev.match_id, "score_home": rm.score_home, "score_away": rm.score_away}
            self._fanout(ev.match_id, "match_end", end)

    def on_state(self, state: MatchState) -> None:
        if self.registry.get_or_create(state.match_id).set_state(state):
            self._fanout(state.match_id, "state", state)

    def on_moment(self, moment: Moment) -> None:
        if self.registry.get_or_create(moment.match_id).add_moment(moment):
            self._fanout(moment.match_id, "moment", moment)
            for listener in self.moment_listeners:
                try:
                    listener(moment)
                except Exception:
                    log.exception("moment listener failed")

    def on_envelope(self, env: InsightsEnvelope) -> None:
        data = env.data
        if self.gate is not None and self.gate(data):
            return  # held for the producer (editorial gate, `producer.py`)
        self.deliver(data)

    def deliver(self, data: Insight | OverlayCue | Recap) -> None:
        """Into the read model and out to every subscriber (also used by the producer desk to release items)."""
        rm = self.registry.get_or_create(data.match_id)
        if isinstance(data, Insight):
            rm.add_insight(data)
            self._fanout(data.match_id, "insight", data)
        elif isinstance(data, OverlayCue):
            if rm.add_cue(data):
                self._fanout(data.match_id, "cue", data)
        elif isinstance(data, Recap):
            rm.add_recap(data)
            self._fanout(data.match_id, "recap", data)

    # ------------------------------------------------------------------ pumps

    async def _pump(self, topic: str, model: type[BaseModel], handler: Callable[[Any], None]) -> None:
        while True:  # re-subscribe if the consumer itself fails (e.g. a message that doesn't parse)
            try:
                async for msg in self.bus.subscribe(topic, model, GROUPS[topic]):
                    try:
                        handler(msg)
                    except Exception:
                        log.exception("hub failed on %s message", topic)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("hub consumer for %s failed; re-subscribing", topic)
                await asyncio.sleep(0.5)

    def start(self) -> None:
        """Register consumer groups first (memory bus), then start one pump per topic."""
        if self._tasks:
            return
        register = getattr(self.bus, "register", None)
        if register is not None:
            for topic, group in GROUPS.items():
                register(topic, group)
        pumps: list[tuple[str, type[BaseModel], Callable[[Any], None]]] = [
            (TOPIC_EVENTS, MatchEvent, self.on_event),
            (TOPIC_STATE, MatchState, self.on_state),
            (TOPIC_MOMENTS, Moment, self.on_moment),
            (TOPIC_INSIGHTS, InsightsEnvelope, self.on_envelope),
        ]
        self._tasks = [asyncio.create_task(self._pump(*p), name=f"hub-{p[0]}") for p in pumps]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
