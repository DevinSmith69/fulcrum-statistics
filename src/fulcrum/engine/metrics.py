"""Engine metrics (MODEL_SPEC §2): decayed pressure, rolling control/chaos/tempo, phase hysteresis, momentum.

Everything here is pure and keyed on **sim time** (`clock_ms`), never wall clock. `state.py` owns the event
bookkeeping and calls into these helpers.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

from fulcrum.contracts import MatchPhase

from .params import DEFAULT_PARAMS, MetricParams, RatingParams


class DecayingScore:
    """A score that halves every `half_life_s` of sim time (the raw input to the pressure index)."""

    def __init__(self, half_life_s: float) -> None:
        self.half_life_ms = half_life_s * 1000.0
        self._value = 0.0
        self._at_ms = 0

    def value_at(self, clock_ms: int) -> float:
        """Decayed value at `clock_ms` (never decays backwards in time)."""
        dt = max(0, clock_ms - self._at_ms)
        return self._value * 0.5 ** (dt / self.half_life_ms)

    def add(self, points: float, clock_ms: int) -> None:
        """Decay to `clock_ms`, then add `points`."""
        self._value = self.value_at(clock_ms) + points
        self._at_ms = max(self._at_ms, clock_ms)


def pressure_index(raw: float, params: MetricParams = DEFAULT_PARAMS.metrics) -> float:
    """Map raw decayed threat points to 0–100."""
    return 100.0 * (1.0 - math.exp(-raw / params.pressure_scale))


@dataclass
class Possession:
    """One spell of possession. `end_ms` is None while it is still running."""

    index: int
    team_id: str
    start_ms: int
    possession_id: int | None = None
    end_ms: int | None = None
    passes: int = 0
    reached_final_third: bool = False
    shots: int = 0

    def overlap_ms(self, lo_ms: int, hi_ms: int) -> int:
        """Milliseconds of this possession inside [lo_ms, hi_ms]."""
        end = hi_ms if self.end_ms is None else self.end_ms
        return max(0, min(end, hi_ms) - max(self.start_ms, lo_ms))


class Window[T]:
    """Time-stamped items kept for `window_s` of sim time."""

    def __init__(self, window_s: float) -> None:
        self.window_ms = int(window_s * 1000)
        self._items: deque[tuple[int, T]] = deque()

    def add(self, clock_ms: int, item: T) -> None:
        self._items.append((clock_ms, item))

    def items(self, now_ms: int) -> list[T]:
        """Items with clock in (now - window, now]. Prunes older ones."""
        lo = now_ms - self.window_ms
        while self._items and self._items[0][0] < lo:
            self._items.popleft()
        return [it for t, it in self._items if t <= now_ms]

    def stamped(self, now_ms: int) -> list[tuple[int, T]]:
        """Like `items` but with timestamps."""
        self.items(now_ms)
        return [(t, it) for t, it in self._items if t <= now_ms]


def rate_window_min(now_ms: int, params: MetricParams = DEFAULT_PARAMS.metrics) -> float:
    """Denominator (minutes) for per-minute rates: elapsed time capped at the window, floored early on."""
    span_ms = min(params.window_s * 1000, max(now_ms, params.min_rate_window_s * 1000))
    return span_ms / 60000.0


def possession_shares(possessions: Iterable[Possession], team_ids: tuple[str, str], lo_ms: int, hi_ms: int):
    """Time share of each team inside [lo_ms, hi_ms] → {team_id: 0..1}."""
    totals = dict.fromkeys(team_ids, 0)
    for p in possessions:
        if p.team_id in totals:
            totals[p.team_id] += p.overlap_ms(lo_ms, hi_ms)
    s = sum(totals.values())
    return {t: (v / s if s else 0.5) for t, v in totals.items()}


def control_index(
    poss_share: float,
    completion: float,
    mean_passes_per_possession: float,
    params: MetricParams = DEFAULT_PARAMS.metrics,
) -> float:
    """MODEL_SPEC §2 control index, 0–100."""
    depth = min(1.0, mean_passes_per_possession / params.control_target_passes_per_possession)
    return 100.0 * poss_share * completion * depth


def chaos_index(
    turnovers_per_min: float,
    share_short_possessions: float,
    share_long_passes: float,
    params: MetricParams = DEFAULT_PARAMS.metrics,
) -> float:
    """MODEL_SPEC §2 chaos index, 0–100."""
    return 100.0 * (
        params.chaos_w_turnovers * min(1.0, turnovers_per_min / params.chaos_turnovers_per_min_cap)
        + params.chaos_w_short_possessions * share_short_possessions
        + params.chaos_w_long_passes * share_long_passes
    )


def classify_phase(
    chaos: float, control_home: float, control_away: float, params: MetricParams = DEFAULT_PARAMS.metrics
) -> MatchPhase:
    """Instantaneous phase (before hysteresis)."""
    if chaos >= params.phase_chaos_threshold:
        return MatchPhase.CHAOS
    diff = control_home - control_away
    if diff >= params.phase_control_diff:
        return MatchPhase.CONTROL_HOME
    if diff <= -params.phase_control_diff:
        return MatchPhase.CONTROL_AWAY
    return MatchPhase.BALANCED


class PhaseTracker:
    """Applies the persistence hysteresis: a new phase must hold `phase_persist_s` before it becomes current."""

    def __init__(self, params: MetricParams = DEFAULT_PARAMS.metrics) -> None:
        self.persist_ms = int(params.phase_persist_s * 1000)
        self.phase = MatchPhase.BALANCED
        self._candidate: MatchPhase | None = None
        self._since_ms = 0

    def update(self, candidate: MatchPhase, clock_ms: int, control_diff: float | None = None) -> MatchPhase | None:
        """Feed the instantaneous phase. Returns the *previous* phase if a transition was committed.

        `control_diff` (home minus away control index): a "X in control" phase ends at once, back to balanced,
        when the other side's control is higher, so the label never contradicts the control gauges on screen.
        """
        stale = (self.phase == MatchPhase.CONTROL_HOME and control_diff is not None and control_diff < 0) or (
            self.phase == MatchPhase.CONTROL_AWAY and control_diff is not None and control_diff > 0
        )
        if stale:
            before, self.phase = self.phase, MatchPhase.BALANCED
            self._candidate = None if candidate == MatchPhase.BALANCED else candidate
            self._since_ms = clock_ms
            return before
        if candidate == self.phase:
            self._candidate = None
            return None
        if candidate != self._candidate:
            self._candidate, self._since_ms = candidate, clock_ms
            return None
        if clock_ms - self._since_ms >= self.persist_ms:
            before, self.phase, self._candidate = self.phase, candidate, None
            return before
        return None


def ema(previous: float, value: float, alpha: float) -> float:
    """One exponential-moving-average step."""
    return previous + alpha * (value - previous)


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def player_rating(delta: float, params: RatingParams = DEFAULT_PARAMS.ratings) -> float:
    """Rating from accumulated deltas: start 6.0, soft-capped towards 3 and 10.

    A plain clamp let a busy winger with no goals hit 10.0. tanh keeps small deltas almost linear
    (+1 → ~7.0) but makes the extremes asymptotic (+3.6 → ~8.9, +4.3 → ~9.2), so 10.0 means a historic match.
    """
    up, down = params.ceiling - params.base, params.base - params.floor
    span = up if delta >= 0 else down
    return clamp(params.base + span * math.tanh(delta / span), params.floor, params.ceiling)


def completed_pass_rating(difficulty: float, params: RatingParams = DEFAULT_PARAMS.ratings) -> float:
    """Rating credit for one completed pass of the given difficulty."""
    return params.completed_pass_per_difficulty * (1.0 + difficulty)
