"""In-memory sliding-window rate limiter.

Used to shed request floods per client address. It is disposable state: the limits
that matter for security (failed consent attempts) are persisted in the store.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

_MAX_KEYS = 5000


class SlidingWindow:
    def __init__(self, limit: int, window: float) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None, *, count: int = 1) -> bool:
        """Count ``count`` requests for ``key`` (all or none); False when they would go
        over the limit."""
        now = time.monotonic() if now is None else now
        if len(self._hits) > _MAX_KEYS:
            self._evict(now)
        hits = self._current(key, now)
        if len(hits) + count > self.limit:
            return False
        hits.extend([now] * count)
        return True

    def remaining(self, key: str, now: float | None = None) -> int:
        """How many more requests ``key`` may make in the current window."""
        now = time.monotonic() if now is None else now
        return max(0, self.limit - len(self._current(key, now)))

    def _current(self, key: str, now: float) -> deque[float]:
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        return hits

    def _evict(self, now: float) -> None:
        stale = [k for k, hits in self._hits.items() if not hits or now - hits[-1] > self.window]
        for key in stale:
            del self._hits[key]
        if len(self._hits) > _MAX_KEYS:  # still flooded with live keys: start over
            self._hits.clear()
