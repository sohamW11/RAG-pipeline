"""Event-bus contract.

The crawler publishes lifecycle events (discovery, metadata, downloads, errors,
ready-for-parse, dead-letter). The rest of the platform (parser, indexer, ...)
consumes them. Publishers implement this contract so the crawler is agnostic to
whether the bus is Kafka, a no-op logger, or a test double.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class EventPublisher(ABC):
    """Contract for publishing events to the message bus."""

    @abstractmethod
    async def start(self) -> None:
        """Establish the connection / producer."""

    @abstractmethod
    async def stop(self) -> None:
        """Flush and close the producer."""

    @abstractmethod
    async def publish(self, topic: str, value: dict[str, Any], *, key: Optional[str] = None) -> None:
        """Publish a JSON-serialisable ``value`` to ``topic`` with an optional key."""
