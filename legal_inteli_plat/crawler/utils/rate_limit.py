"""In-process rate limiter (async token bucket).

Enforces a maximum request rate within a single process. Distributed pacing
across replicas is provided by the Redis-backed limiter in
:mod:`crawler.services.cache`; both satisfy the :class:`RateLimiter` contract.
"""

from __future__ import annotations

import asyncio
import time

from crawler.interfaces.rate_limiter import RateLimiter


class InMemoryRateLimiter(RateLimiter):
    """A simple async token-bucket limiter.

    Args:
        rate_per_second: Sustained request rate. Values <= 0 disable limiting.
        burst: Maximum tokens that can accumulate (defaults to one second worth).
    """

    def __init__(self, rate_per_second: float, burst: float | None = None) -> None:
        self._rate = max(rate_per_second, 0.0)
        self._capacity = burst if burst is not None else max(rate_per_second, 1.0)
        self._tokens = self._capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, key: str = "default") -> None:
        if self._rate <= 0:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._updated
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                # Sleep just long enough for the next token to become available.
                await asyncio.sleep((1 - self._tokens) / self._rate)
