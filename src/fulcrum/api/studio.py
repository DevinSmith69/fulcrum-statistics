"""Ask the studio, API side: a light per-client rate limit for `POST /api/matches/{id}/ask`.

Each question can cost an LLM call, so one client (keyed by IP) may ask at most `max_calls` questions per
`window_s` seconds (env `FULCRUM_ASK_RATE`, e.g. `6/30`; `0` turns the limit off). Process-local and bounded.
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict, deque
from collections.abc import Callable

DEFAULT_RATE = "6/30"
MAX_CLIENTS = 2048


class RateLimiter:
    """Sliding-window limiter: `max_calls` per `window_s` per key."""

    def __init__(self, max_calls: int, window_s: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_calls = max_calls
        self.window_s = window_s
        self.clock = clock
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    @classmethod
    def from_env(cls) -> RateLimiter:
        """`FULCRUM_ASK_RATE=<calls>/<seconds>` (default 6/30); `0` = unlimited."""
        raw = (os.getenv("FULCRUM_ASK_RATE") or DEFAULT_RATE).strip()
        try:
            calls, _, window = raw.partition("/")
            return cls(int(calls), float(window or 60))
        except ValueError:
            calls, window = DEFAULT_RATE.split("/")
            return cls(int(calls), float(window))

    def allow(self, key: str) -> float | None:
        """Record a call for `key`. Returns None if allowed, else the seconds to wait before retrying."""
        if self.max_calls <= 0:
            return None
        now = self.clock()
        hits = self._hits.setdefault(key, deque())
        self._hits.move_to_end(key)
        while hits and now - hits[0] >= self.window_s:
            hits.popleft()
        if len(hits) >= self.max_calls:
            return max(0.0, self.window_s - (now - hits[0]))
        hits.append(now)
        while len(self._hits) > MAX_CLIENTS:
            self._hits.popitem(last=False)
        return None
