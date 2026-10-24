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


class Connection:
    """One WebSocket viewer: prefs, current subscription and an outbound queue."""

    def __init__(self, ws: WebSocket, hub: Hub) -> None:
        self.ws = ws
        self.hub = hub
        self.prefs = ViewerPrefs()
        self.match_id: str | None = None
        self.queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=CONNECTION_QUEUE_MAX)
        self._last_player_tag_ms: int | None = None
        self._recap_sent = False

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
        insights = [
            await ps.personalise_insight(i, self.prefs, self.hub.translator, translate=n >= cutoff)
            for n, i in enumerate(items)
        ]
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
            },
            rm.match_id,
        )

    async def sender(self) -> None:
        """Drain the queue, personalising each item for this viewer."""
        while True:
            kind, item = await self.queue.get()
            if item is not None and getattr(item, "match_id", self.match_id) != self.match_id:
                continue  # stale item from a previous subscription
            await self._deliver(kind, item)

    async def _deliver(self, kind: str, item: Any) -> None:
        rm = self.hub.registry.get(self.match_id or "")
        if kind == "hello":
            if rm is not None:
                await self.hello(rm)
                if rm.status == "finished" and rm.recaps:  # a (re)subscribe after full time gets its recap too
                    best = await self.hub.best_recap(rm, self.prefs)
                    if best is not None:
                        self._recap_sent = True
                        await self.send("recap", dump(best))
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
            # Each arriving recap is a nudge: send the best one for this viewer, once.
            if rm is not None and not self._recap_sent:
                best = await self.hub.best_recap(rm, self.prefs)
                if best is not None:
                    self._recap_sent = True
                    await self.send("recap", dump(best))
        elif kind == "match_end":
            await self.send("match_end", item)

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
        self._recap_sent = False


class Hub:
    """Consumes the bus for every match and fans out to subscribed connections."""

    def __init__(self, bus: Bus, registry: Registry, translator: Translator) -> None:
        self.bus = bus
        self.registry = registry
        self.translator = translator
        self.subscribers: dict[str, set[Connection]] = {}
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
            if generated is not None:
                rm.add_recap(generated)
                return generated
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
            self._fanout(ev.match_id, "match_end", {"score_home": rm.score_home, "score_away": rm.score_away})

    def on_state(self, state: MatchState) -> None:
        if self.registry.get_or_create(state.match_id).set_state(state):
            self._fanout(state.match_id, "state", state)

    def on_moment(self, moment: Moment) -> None:
        if self.registry.get_or_create(moment.match_id).add_moment(moment):
            self._fanout(moment.match_id, "moment", moment)

    def on_envelope(self, env: InsightsEnvelope) -> None:
        data = env.data
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
        async for msg in self.bus.subscribe(topic, model, GROUPS[topic]):
            try:
                handler(msg)
            except Exception:
                log.exception("hub failed on %s message", topic)

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
