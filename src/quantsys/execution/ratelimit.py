"""Token-bucket rate limiter for the Angel One API budgets.

SmartAPI publishes per-endpoint limits; we configure conservative buckets
(orders ~10/s, historical ~3/s, generic ~3/s) and BLOCK (not drop) when empty,
because dropping an order silently is the worst outcome. Thread-safe.
"""

from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: float | None = None,
                 name: str = ""):
        self.rate = rate_per_sec
        self.capacity = capacity if capacity is not None else max(1.0, rate_per_sec)
        self.name = name
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last = now

    def try_acquire(self, n: float = 1.0) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    def acquire(self, n: float = 1.0, timeout: float | None = None) -> bool:
        """Block until n tokens are available (or timeout). Returns success."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= n:
                    self._tokens -= n
                    return True
                needed = (n - self._tokens) / self.rate
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                time.sleep(min(needed, remaining))
            else:
                time.sleep(needed)
