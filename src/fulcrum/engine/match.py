"""`MatchEngine`: one match's tracker + win probability + moment detector behind a two-method API.

Pure and synchronous (no bus, no clock), so tests, `scripts/print_moments.py` and `service.py` all share
exactly the same logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fulcrum.contracts import MatchEvent, MatchState, Moment, Team, TrackingFrame, WinProbability, WinProbPoint

from .moments import MomentDetector
from .params import DEFAULT_PARAMS, EngineParams
from .state import MatchTracker


@dataclass
class EngineOutput:
    """What the engine wants published after one input."""

    state: MatchState | None = None
    moments: list[Moment] = field(default_factory=list)
    winprob: WinProbPoint | None = None


class MatchEngine:
    """Turns a match's events and tracking frames into `MatchState` snapshots and `Moment`s."""

    def __init__(
        self,
        match_id: str,
        home: Team | None = None,
        away: Team | None = None,
        teams_by_id: dict[str, Team] | None = None,
        params: EngineParams = DEFAULT_PARAMS,
    ) -> None:
        self.params = params
        self.tracker = MatchTracker(match_id, home, away, teams_by_id, params)
        self.detector = MomentDetector(self.tracker, params.moments)
        self.wp: WinProbability = self.tracker.win_prob()
        self.winprob_points: list[WinProbPoint] = []
        self._last_state_clock: int | None = None
        self._last_period = 0

    @property
    def moments(self) -> list[Moment]:
        return self.detector.emitted

    def process_event(self, ev: MatchEvent) -> EngineOutput:
        """Apply one event. Duplicates (seq already applied) produce an empty output."""
        wp_before = self.wp
        fx = self.tracker.apply_event(ev)
        if fx.duplicate:
            return EngineOutput()
        t = self.tracker
        self.wp = t.win_prob()
        point = WinProbPoint(
            clock_ms=t.clock_ms, seq=t.last_seq, home=self.wp.home, draw=self.wp.draw, away=self.wp.away
        )
        self.winprob_points.append(point)
        moments = self.detector.on_event(ev, fx, wp_before, self.wp)
        force = bool(moments) or t.period != self._last_period or fx.period_ended is not None or fx.match_ended
        return EngineOutput(self._maybe_state(force), moments, point)

    def process_frame(self, frame: TrackingFrame) -> EngineOutput:
        """Apply one tracking frame (player distance / speed). May emit speed / distance moments."""
        fx = self.tracker.apply_frame(frame)
        if fx.duplicate:
            return EngineOutput()
        moments = self.detector.on_frame(frame, fx, self.wp)
        return EngineOutput(self._maybe_state(True) if moments else None, moments)

    def _maybe_state(self, force: bool) -> MatchState | None:
        t = self.tracker
        due = self._last_state_clock is None or t.clock_ms - self._last_state_clock >= self.params.state_interval_ms
        if not (force or due):
            return None
        self._last_state_clock = t.clock_ms
        self._last_period = t.period
        return self.snapshot()

    def snapshot(self) -> MatchState:
        """Current `MatchState` (always available, regardless of the publish throttle)."""
        return self.tracker.snapshot(self.wp)
