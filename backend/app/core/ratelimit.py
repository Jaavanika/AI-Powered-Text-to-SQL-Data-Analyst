"""Small in-memory limiters: a sliding window for request rates and a failure counter for logins.

State lives in this process only. With several server processes each has its own counts, so run one
process, or put a shared limiter in front, if the limits must be exact.
"""

import threading
import time
from collections import deque
from collections.abc import Callable

DEFAULT_MAX_KEYS = 10_000


class SlidingWindow:
    """At most `limit` hits per key in any `window` seconds. The limit is given on each call."""

    def __init__(self, window: float, *, clock: Callable[[], float] = time.monotonic, max_keys: int = DEFAULT_MAX_KEYS):
        self.window = window
        self._clock = clock
        self._max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._hits)

    def hit(self, key: str, limit: int) -> float | None:
        """Record a request. Returns None if it is allowed, or the seconds to wait if it is not.
        A refused request is not recorded, so retrying early never lengthens the wait."""
        if limit <= 0:
            return None
        now = self._clock()
        with self._lock:
            times = self._hits.setdefault(key, deque())
            while times and times[0] <= now - self.window:
                times.popleft()
            if len(times) >= limit:
                return max(times[0] + self.window - now, 0.001)
            times.append(now)
            if len(self._hits) > self._max_keys:
                self._forget_idle(now)
            return None

    def _forget_idle(self, now: float) -> None:
        for key in [k for k, t in self._hits.items() if not t or t[-1] <= now - self.window]:
            del self._hits[key]

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


class FailureLimiter:
    """Locks a key out after `max_failures` failures within `window` seconds; a success clears it."""

    def __init__(self, max_failures: int, window: float, *, clock: Callable[[], float] = time.monotonic,
                 max_keys: int = DEFAULT_MAX_KEYS):
        self.max_failures = max_failures
        self.window = window
        self._clock = clock
        self._max_keys = max_keys
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        times = self._failures.get(key)
        if times is None:
            return deque()
        while times and times[0] <= now - self.window:
            times.popleft()
        if not times:
            del self._failures[key]
            return deque()
        return times

    def locked_for(self, key: str) -> float:
        """Seconds until this key may try again; 0 if it may try now."""
        now = self._clock()
        with self._lock:
            times = self._recent(key, now)
            if len(times) >= self.max_failures:
                return max(times[0] + self.window - now, 0.001)
            return 0

    def fail(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            times = self._recent(key, now)
            if len(times) >= self.max_failures:
                return                     # already locked: more attempts do not make the lock longer
            if not times:
                if len(self._failures) > self._max_keys:
                    self._failures = {k: v for k, v in self._failures.items() if v and v[-1] > now - self.window}
                times = self._failures.setdefault(key, deque())
            times.append(now)

    def succeed(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
