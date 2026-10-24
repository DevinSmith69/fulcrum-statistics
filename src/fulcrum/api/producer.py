"""Producer console, API side: the editorial gate and the graphics put on air (API_CONTRACT §2, Producer console).

`ProducerDesk` sits between the `insights` topic and the viewers (`Hub.gate`). Per match, a producer can switch
**approval required** on: from then on every new `Insight` and its `OverlayCue`s are *held* here instead of
reaching the read model and the WebSocket fan-out. The producer then approves, edits or kills each held item.
Edits are re-fact-checked against the moment's facts with the agents' deterministic checker
(`fulcrum.agents.producer.apply_edit`) and the cues are rebuilt from the edited copy; a failing edit is rejected.
`auto_approve_s` releases items that have waited that long (keeps a demo flowing). Turning approval off releases
everything still held, in order. With approval off (the default) nothing is held and nothing changes.

Released items are delivered by this process (read model + WebSocket + `overlay-cues`), not re-published to the
`insights` topic. Producer graphics (`origin="producer"`) are never held: the producer put them on air.
Process-local and bounded, like the read models.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fulcrum.agents.context import foreign_names
from fulcrum.agents.cues import build_cues
from fulcrum.agents.producer import apply_edit, insight_sheet, score_at
from fulcrum.contracts import (
    GraphicProposal,
    HeldItem,
    Insight,
    InsightVariant,
    OverlayCue,
    Persona,
    ProducerDecision,
    ProducerLogEntry,
    ProducerState,
)

from .readmodel import MatchReadModel, Registry

log = logging.getLogger("fulcrum.api.producer")

MAX_HELD = 200  # per match; beyond this the oldest held item is dropped (never sent unseen)
MAX_LOG = 60
MAX_PROPOSALS = 50
TICK_S = 0.5
APPROVED_NOTE = "approved by producer"


def _auto_note(seconds: int) -> str:
    return f"auto-approved after {seconds} s"


@dataclass
class _Held:
    insight: Insight
    held_at: datetime
    held_mono: float
    cues: list[OverlayCue] = field(default_factory=list)


@dataclass
class MatchDesk:
    """One match's gate settings, held items, decision log and graphic proposals."""

    match_id: str
    approval_required: bool = False
    auto_approve_s: int | None = None
    held: OrderedDict[str, _Held] = field(default_factory=OrderedDict)
    killed: set[str] = field(default_factory=set)
    log: deque[ProducerLogEntry] = field(default_factory=lambda: deque(maxlen=MAX_LOG))
    proposals: OrderedDict[str, GraphicProposal] = field(default_factory=OrderedDict)


class DeskError(Exception):
    """A producer action that cannot be done; `status` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _headline(insight: Insight) -> str:
    casual = next((v for v in insight.variants if v.persona == Persona.CASUAL), None)
    return (casual or (insight.variants[0] if insight.variants else None) or insight.explanation).headline


class ProducerDesk:
    """The editorial gate for every match in this process."""

    def __init__(
        self,
        registry: Registry,
        deliver: Callable[[Insight | OverlayCue], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        on_edit: Callable[[Insight], None] | None = None,
    ) -> None:
        self.registry = registry
        self.deliver = deliver
        self.clock = clock
        self.on_edit = on_edit
        self.desks: dict[str, MatchDesk] = {}

    def desk(self, match_id: str) -> MatchDesk:
        if match_id not in self.desks:
            self.desks[match_id] = MatchDesk(match_id)
        return self.desks[match_id]

    # ------------------------------------------------------------------ the gate (Hub.gate)

    def intercept(self, item: Any) -> bool:
        """True if `item` is held (or dropped) here instead of going to viewers."""
        if isinstance(item, Insight):
            d = self.desks.get(item.match_id)
            if d is None or not d.approval_required:
                return False
            if item.insight_id in d.held:  # a re-publish (e.g. more variants): keep the newest copy
                d.held[item.insight_id].insight = item
                return True
            d.held[item.insight_id] = _Held(item, datetime.now(UTC), self.clock())
            while len(d.held) > MAX_HELD:
                _, old = d.held.popitem(last=False)
                d.killed.add(old.insight.insight_id)
                self._log(d, "drop", old.insight, note="held queue full: dropped, not sent")
            return True
        if isinstance(item, OverlayCue):
            if item.origin == "producer" or item.insight_id is None:
                return False
            d = self.desks.get(item.match_id)
            if d is None:
                return False
            if item.insight_id in d.killed:
                return True
            held = d.held.get(item.insight_id)
            if held is None:
                return False
            if all(c.cue_id != item.cue_id for c in held.cues):
                held.cues.append(item)
            return True
        return False

    # ------------------------------------------------------------------ settings and state

    def configure(self, match_id: str, *, approval_required: bool | None, auto_approve_s: int | None) -> None:
        """Change the gate. Turning approval off releases everything held, oldest first."""
        d = self.desk(match_id)
        if auto_approve_s is not None:
            d.auto_approve_s = auto_approve_s or None
        if approval_required is not None:
            d.approval_required = approval_required
            if not approval_required:
                for insight_id in list(d.held):
                    self._release(d, insight_id, "release", note="approval switched off")

    def state(self, match_id: str, *, graphics_available: bool = False) -> ProducerState:
        d = self.desk(match_id)
        return ProducerState(
            match_id=match_id,
            approval_required=d.approval_required,
            auto_approve_s=d.auto_approve_s,
            held=self.held(match_id),
            log=list(reversed(d.log)),
            graphics_available=graphics_available,
        )

    def held(self, match_id: str) -> list[HeldItem]:
        """Held items, oldest first, with their age and the time left before auto-approve."""
        d = self.desk(match_id)
        rm = self.registry.get(match_id)
        moments = {m.moment_id: m for m in rm.moments} if rm is not None else {}
        now = self.clock()
        out = []
        for h in d.held.values():
            age = max(0.0, now - h.held_mono)
            left = max(0.0, d.auto_approve_s - age) if d.auto_approve_s else None
            out.append(
                HeldItem(
                    insight=h.insight,
                    cues=list(h.cues),
                    moment=moments.get(h.insight.moment_id),
                    held_at=h.held_at,
                    age_s=round(age, 1),
                    auto_release_in_s=None if left is None else round(left, 1),
                )
            )
        return out

    # ------------------------------------------------------------------ decisions

    def _take(self, match_id: str, insight_id: str) -> tuple[MatchDesk, _Held]:
        d = self.desk(match_id)
        held = d.held.get(insight_id)
        if held is None:
            raise DeskError(404, f"no held insight '{insight_id}' (already released or killed?)")
        return d, held

    def approve(self, match_id: str, insight_id: str) -> ProducerDecision:
        d, held = self._take(match_id, insight_id)
        insight, n = self._release(d, insight_id, "approve")
        return ProducerDecision(
            match_id=match_id, insight_id=insight_id, action="approve", ok=True, insight=insight, cues_released=n
        )

    def kill(self, match_id: str, insight_id: str) -> ProducerDecision:
        d, held = self._take(match_id, insight_id)
        del d.held[insight_id]
        d.killed.add(insight_id)
        self._log(d, "kill", held.insight, note=f"{len(held.cues)} cues dropped")
        return ProducerDecision(match_id=match_id, insight_id=insight_id, action="kill", ok=True)

    def edit(self, match_id: str, insight_id: str, variants: Sequence[InsightVariant]) -> ProducerDecision:
        """Re-check the edited copy against the moment's facts; release it (with rebuilt cues) only if it passes."""
        d, held = self._take(match_id, insight_id)
        rm = self.registry.get(match_id)
        moment = next((m for m in rm.moments if m.moment_id == held.insight.moment_id), None) if rm else None
        if rm is None or moment is None or rm.home is None or rm.away is None:
            issues = ["the moment or the teams of this insight are unknown, so the edit can't be verified"]
            return ProducerDecision(match_id=match_id, insight_id=insight_id, action="edit", ok=False, issues=issues)
        sheet = insight_sheet(
            moment,
            score=score_at(moment, _score_at_seq(rm, moment.seq_end)),
            foreign=foreign_names(rm.home, rm.away, self.registry.teams.values()),
        )
        edited, result = apply_edit(held.insight, variants, sheet, home_id=rm.home.team_id, away_id=rm.away.team_id)
        if edited is None:
            return ProducerDecision(
                match_id=match_id, insight_id=insight_id, action="edit", ok=False, issues=result.messages
            )
        held.insight = edited
        held.cues = build_cues(edited, moment, rm.home, rm.away)
        if self.on_edit is not None:
            try:
                self.on_edit(edited)
            except Exception:
                log.exception("producer: on_edit hook failed for %s", insight_id)
        insight, n = self._release(d, insight_id, "edit")
        return ProducerDecision(
            match_id=match_id, insight_id=insight_id, action="edit", ok=True, insight=insight, cues_released=n
        )

    def tick(self) -> int:
        """Auto-approve items that have waited `auto_approve_s`. Returns how many were released."""
        now = self.clock()
        released = 0
        for d in list(self.desks.values()):
            if not d.auto_approve_s:
                continue
            for insight_id, h in list(d.held.items()):
                if now - h.held_mono >= d.auto_approve_s:
                    self._release(d, insight_id, "auto_approve", note=_auto_note(d.auto_approve_s))
                    released += 1
        return released

    async def run(self) -> None:
        """Background auto-approve loop (started by the API lifespan)."""
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("producer auto-approve tick failed")
            await asyncio.sleep(TICK_S)

    # ------------------------------------------------------------------ graphics

    def remember(self, proposal: GraphicProposal) -> None:
        d = self.desk(proposal.match_id)
        d.proposals[proposal.proposal_id] = proposal
        while len(d.proposals) > MAX_PROPOSALS:
            d.proposals.popitem(last=False)

    def air(self, match_id: str, proposal_id: str, *, clock_ms: int, seq: int) -> OverlayCue:
        """Put a proposal's cue on air now: a fresh cue (new id, shown at the current clock) to every viewer."""
        d = self.desk(match_id)
        proposal = d.proposals.get(proposal_id)
        if proposal is None:
            raise DeskError(404, f"unknown graphic proposal '{proposal_id}'")
        if not proposal.ok or proposal.cue is None:
            raise DeskError(409, "this request was refused, so there is no graphic to put on air")
        cue = OverlayCue.model_validate(
            {
                **proposal.cue.model_dump(exclude={"cue_id"}),
                "sync_clock_ms": clock_ms,
                "sync_seq": seq,
                "show_at_clock_ms": clock_ms,
                "origin": "producer",
            }
        )
        self.deliver(cue)
        d.log.append(ProducerLogEntry(action="air", cue_id=cue.cue_id, headline=cue.headline, note=str(cue.template)))
        return cue

    # ------------------------------------------------------------------ internals

    def _release(self, d: MatchDesk, insight_id: str, action: str, note: str | None = None) -> tuple[Insight, int]:
        held = d.held.pop(insight_id)
        stamp = {"approve": APPROVED_NOTE, "auto_approve": note, "release": None, "edit": None}.get(action)
        insight = held.insight
        if stamp:
            insight = insight.model_copy(update={"check_notes": [*insight.check_notes, stamp]})
        self.deliver(insight)
        for cue in held.cues:
            self.deliver(cue)
        self._log(d, action, insight, note=note)
        return insight, len(held.cues)

    def _log(self, d: MatchDesk, action: str, insight: Insight, note: str | None = None) -> None:
        d.log.append(
            ProducerLogEntry(action=action, insight_id=insight.insight_id, headline=_headline(insight), note=note)
        )


def _score_at_seq(rm: MatchReadModel, seq: int) -> str:
    """The score after event `seq` (latest event at or before it), else the read model's score."""
    for ev in reversed(rm.events):
        if ev.seq <= seq:
            return f"{ev.score_home}-{ev.score_away}"
    return f"{rm.score_home}-{rm.score_away}"
