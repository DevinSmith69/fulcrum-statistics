"""Live match controls (demo director): speed, pause / resume and "skip to the next moment".

A `MatchControl` only changes *wall-clock pacing*. The simulator generator is pure and seeded, so the items
it yields (and their order) never depend on the control: same seed → identical event stream at any speed,
paused or not. `run_match(..., control=...)` awaits `control.pace(clock_ms)` before publishing each item.

Pacing is anchored: the item at sim time `c` is due at `anchor_wall + (c - anchor_sim) / 1000 / speed`.
Every change (speed, pause, resume, skip start / end) re-anchors at the current position, so a change takes
effect immediately and never makes the stream burst to "catch up".

Concurrency: the object is confined to the event loop that runs the match (the API handlers and the bus
consumers are coroutines on that same loop), so plain attributes are safe. Waiters are woken with an
`asyncio.Event`; call the mutators from the loop thread only (use `loop.call_soon_threadsafe` otherwise).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from fulcrum.contracts import Moment, MomentType

MIN_SPEED = 0.25
MAX_SPEED = 600.0
SKIP_SPEED = 120.0  # sim-time multiplier while skipping to the next moment (2 match minutes per second)
DEFAULT_SKIP_SIGNIFICANCE = 0.65  # goals, red cards, big chances, full time (not phase/tempo/pressure notes)


class MatchControl:
    """Per-match pacing state consulted by the simulator loop (see module docstring)."""

    def __init__(self, match_id: str, speed: float, *, skip_speed: float = SKIP_SPEED) -> None:
        self.match_id = match_id
        self.speed = _check_speed(speed)
        if skip_speed <= 0:
            raise ValueError("skip_speed must be positive")
        self.skip_speed = float(skip_speed)  # not capped: tests skip at 1e6×
        self.paused = False
        self.finished = False
        self.position_ms = 0  # sim clock of the last item released
        self.skip_min_significance: float | None = None  # not None while skipping
        self.skip_types: frozenset[MomentType] | None = None
        self.last_skip: dict[str, Any] | None = None  # the moment the last skip stopped at
        self._changed = asyncio.Event()
        self._anchor_wall: float | None = None
        self._anchor_sim = 0

    # ------------------------------------------------------------------ state

    @property
    def skipping(self) -> bool:
        """True while running fast towards the next significant moment."""
        return self.skip_min_significance is not None

    @property
    def effective_speed(self) -> float:
        """The multiplier currently pacing the stream."""
        return max(self.skip_speed, self.speed) if self.skipping else self.speed

    def snapshot(self) -> dict[str, Any]:
        """JSON-able view for `GET /api/matches/{id}/control` (API_CONTRACT §2 "Match controls")."""
        return {
            "match_id": self.match_id,
            "available": True,
            "speed": self.speed,
            "effective_speed": self.effective_speed,
            "paused": self.paused,
            "skipping": self.skipping,
            "skip_min_significance": self.skip_min_significance,
            "position_ms": self.position_ms,
            "finished": self.finished,
            "last_skip": self.last_skip,
        }

    # ------------------------------------------------------------------ mutators (event-loop thread)

    def set_speed(self, speed: float) -> None:
        """Change the chosen speed (also the speed a running skip drops back to)."""
        self.speed = _check_speed(speed)
        self._reanchor()
        self._wake()

    def pause(self) -> None:
        """Hold the stream before the next item (the sim generator is simply not advanced)."""
        self.paused = True
        self._wake()

    def resume(self) -> None:
        """Continue from where the stream was paused, at the current speed."""
        if self.paused:
            self.paused = False
            self._anchor_wall = None  # re-anchor on the next item: no catch-up burst after a pause
        self._wake()

    def skip_to_next(
        self, min_significance: float = DEFAULT_SKIP_SIGNIFICANCE, types: Iterable[MomentType | str] | None = None
    ) -> None:
        """Run at `skip_speed` until the engine emits a moment with `significance >= min_significance` (or of one
        of `types`, when given), then drop back to the chosen speed. Resumes a paused match."""
        if not 0.0 <= min_significance <= 1.0:
            raise ValueError("min_significance must be between 0 and 1")
        self._reanchor()
        self.skip_min_significance = min_significance
        self.skip_types = frozenset(MomentType(t) for t in types) if types else None
        self.paused = False
        self._wake()

    def cancel_skip(self) -> None:
        """Stop skipping without waiting for a moment."""
        if self.skipping:
            self._reanchor()
            self.skip_min_significance, self.skip_types = None, None
            self._wake()

    def on_moment(self, moment: Moment) -> bool:
        """Engine output hook: ends a running skip when `moment` qualifies. Returns True if it did."""
        if not self.skipping or moment.match_id != self.match_id:
            return False
        qualifies = (
            moment.type in self.skip_types
            if self.skip_types
            else moment.significance >= (self.skip_min_significance or 0.0)
        )
        if not qualifies:
            return False
        self.last_skip = {
            "moment_id": moment.moment_id,
            "type": str(moment.type),
            "clock_ms": moment.clock_ms,
            "significance": moment.significance,
        }
        self.cancel_skip()
        return True

    def finish(self) -> None:
        """The stream has ended (or was cancelled)."""
        self.finished = True
        self.skip_min_significance, self.skip_types = None, None
        self._wake()

    # ------------------------------------------------------------------ pacing (sim loop)

    async def pace(self, clock_ms: int) -> None:
        """Wait until the item at sim time `clock_ms` is due under the current speed / pause state."""
        loop = asyncio.get_running_loop()
        while True:
            self._changed.clear()
            if self.paused:
                await self._changed.wait()
                continue
            if self._anchor_wall is None:
                self._anchor_wall, self._anchor_sim = loop.time(), self.position_ms
            due = self._anchor_wall + max(0, clock_ms - self._anchor_sim) / 1000.0 / self.effective_speed
            delay = due - loop.time()
            if delay <= 0:
                await asyncio.sleep(0)  # bursts (same-clock items, catching up) still let the engine and API run
                break
            try:
                await asyncio.wait_for(self._changed.wait(), delay)
            except TimeoutError:
                break
        self.position_ms = max(self.position_ms, clock_ms)

    # ------------------------------------------------------------------ internals

    def _reanchor(self) -> None:
        self._anchor_wall = None  # the next pace() anchors at (now, position_ms)

    def _wake(self) -> None:
        self._changed.set()


def _check_speed(speed: float) -> float:
    speed = float(speed)
    if not MIN_SPEED <= speed <= MAX_SPEED:
        raise ValueError(f"speed must be between {MIN_SPEED} and {MAX_SPEED}")
    return speed
