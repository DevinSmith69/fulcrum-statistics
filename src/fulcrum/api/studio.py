"""Rate limits for the endpoints that spend money per call: `POST .../ask` (LLM) and `POST /api/speech` (TTS).

Each client (keyed by `client_key`) may make at most `max_calls` calls per `window_s` seconds (env
`FULCRUM_ASK_RATE` / `FULCRUM_SPEECH_RATE`, e.g. `6/30`; `0` turns a limit off), and a global limiter caps all
clients together. Process-local and bounded.
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping

DEFAULT_RATE = "6/30"
DEFAULT_SPEECH_RATE = "40/60"  # a viewer hears a line every ~10 s at 10x
MAX_CLIENTS = 2048


class RateLimiter:
    """Sliding-window limiter: `max_calls` per `window_s` per key."""

    def __init__(self, max_calls: int, window_s: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_calls = max_calls
        self.window_s = window_s
        self.clock = clock
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    @classmethod
    def from_env(cls, var: str = "FULCRUM_ASK_RATE", default: str = DEFAULT_RATE) -> RateLimiter:
        """`<var>=<calls>/<seconds>` (default `default`); `0` = unlimited."""
        raw = (os.getenv(var) or default).strip()
        try:
            calls, _, window = raw.partition("/")
            return cls(int(calls), float(window or 60))
        except ValueError:
            calls, window = default.split("/")
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


def client_key(headers: Mapping[str, str], peer: str | None) -> str:
    """Who is calling, for rate limits: the right-most `X-Forwarded-For` hop, else the socket peer.

    Behind Container Apps' ingress the proxy appends the real client address as the last hop; everything to its
    left is client-controlled (uvicorn's `forwarded_allow_ips="*"` would use the left-most, spoofable one).
    """
    forwarded = [h.strip() for h in headers.get("x-forwarded-for", "").split(",") if h.strip()]
    return forwarded[-1] if forwarded else (peer or "unknown")
