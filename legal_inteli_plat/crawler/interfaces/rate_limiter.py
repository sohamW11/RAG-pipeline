"""Rate limiter contract.

Abstracts *how* request pacing is enforced so the download manager can be
paced either in-process (single instance) or via Redis (many instances sharing
a global budget) without changing its code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class RateLimiter(ABC):
    """Contract for asynchronous rate limiting."""

    @abstractmethod
    async def acquire(self, key: str = "default") -> None:
        """Block until a request is permitted under the configured rate."""
