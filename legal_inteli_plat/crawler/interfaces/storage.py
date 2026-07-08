"""Storage abstraction.

Defines the *contract* every storage backend must satisfy. The rest of the
codebase depends only on :class:`StorageInterface` -- it never imports a
concrete backend -- so swapping Local <-> MinIO <-> S3 is a configuration
change, honouring the Dependency Inversion Principle.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass(frozen=True)
class StoredObject:
    """Result of a successful store operation."""

    key: str
    uri: str
    size: int


class StorageInterface(ABC):
    """Backend-agnostic object storage contract."""

    @abstractmethod
    async def save_bytes(self, key: str, data: bytes, content_type: str | None = None) -> StoredObject:
        """Persist ``data`` at ``key`` and return its canonical URI."""

    @abstractmethod
    async def save_stream(
        self, key: str, stream: AsyncIterator[bytes], content_type: str | None = None
    ) -> StoredObject:
        """Persist a streamed body at ``key`` without buffering it fully in memory."""

    @abstractmethod
    async def read_bytes(self, key: str) -> bytes:
        """Return the object's bytes."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Return whether an object exists at ``key``."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete the object at ``key`` (no-op if absent)."""
