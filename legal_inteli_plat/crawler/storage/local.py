"""Local filesystem storage backend.

Fully asynchronous: blocking file I/O is off-loaded to a worker thread via
``anyio.to_thread`` so the event loop is never blocked. Useful for local
development and tests; production typically uses S3/MinIO.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import anyio

from crawler.interfaces.storage import StorageInterface, StoredObject


class LocalStorage(StorageInterface):
    """Stores objects under a base directory on the local filesystem."""

    def __init__(self, base_path: str | Path | None = None) -> None:
        self.base_path = Path(base_path or "./storage-data")
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        target = (self.base_path / key).resolve()
        base = self.base_path.resolve()
        # Guard against path traversal via crafted keys.
        if base not in target.parents and target != base:
            raise ValueError(f"Refusing to write outside storage root: {key!r}")
        return target

    async def save_bytes(self, key: str, data: bytes, content_type: str | None = None) -> StoredObject:
        target = self._resolve(key)

        def _write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        await anyio.to_thread.run_sync(_write)
        return StoredObject(key=key, uri=target.as_uri(), size=len(data))

    async def save_stream(
        self, key: str, stream: AsyncIterator[bytes], content_type: str | None = None
    ) -> StoredObject:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        async with await anyio.open_file(target, "wb") as handle:
            async for chunk in stream:
                size += len(chunk)
                await handle.write(chunk)
        return StoredObject(key=key, uri=target.as_uri(), size=size)

    async def read_bytes(self, key: str) -> bytes:
        target = self._resolve(key)
        return await anyio.to_thread.run_sync(target.read_bytes)

    async def exists(self, key: str) -> bool:
        return await anyio.to_thread.run_sync(self._resolve(key).exists)

    async def delete(self, key: str) -> None:
        target = self._resolve(key)
        await anyio.to_thread.run_sync(lambda: target.unlink(missing_ok=True))
