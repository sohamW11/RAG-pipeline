"""Download manager.

Fetches document artefacts (PDF / HTML / ZIP / attachments) and persists them
through the storage abstraction. It provides:

* **async** downloads with bounded **parallelism** (a semaphore),
* **retry** with exponential backoff (tenacity),
* per-request **timeout**,
* **rate limiting** (pluggable in-process or Redis-backed),
* a cheap ``head`` probe used by change detection to read ETag / Last-Modified
  without transferring the body.

It knows nothing about the database; callers persist the returned results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

import anyio
import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from crawler.config.settings import CrawlerSettings, get_settings
from crawler.interfaces.rate_limiter import RateLimiter
from crawler.interfaces.storage import StorageInterface
from crawler.storage.factory import create_storage
from crawler.utils.hashing import sha256_bytes
from crawler.utils.logging import get_logger
from crawler.utils.rate_limit import InMemoryRateLimiter

logger = get_logger("crawler.download")

_RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError)


@dataclass
class HeadInfo:
    """Cheap metadata probed via an HTTP HEAD request."""

    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_type: Optional[str] = None
    content_length: Optional[int] = None


@dataclass
class DownloadResult:
    """Outcome of a single download."""

    url: str
    key: str
    status: str
    storage_uri: Optional[str] = None
    sha256: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_type: Optional[str] = None
    size: int = 0
    attempts: int = 0
    skipped: bool = False
    error: Optional[str] = None


class DownloadManager:
    """Downloads artefacts and stores them via the storage backend."""

    def __init__(
        self,
        storage: StorageInterface | None = None,
        settings: CrawlerSettings | None = None,
        *,
        rate_limiter: RateLimiter | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.storage = storage or create_storage(self.settings)
        self._config = self.settings.download
        self._rate_limiter = rate_limiter or InMemoryRateLimiter(self._config.rate_limit_per_second)
        self._client = client or httpx.AsyncClient(
            timeout=self._config.timeout,
            follow_redirects=True,
            headers={"User-Agent": self._config.user_agent},
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def head(self, url: str) -> HeadInfo:
        """Probe caching headers without downloading the body."""
        await self._rate_limiter.acquire(url)
        try:
            response = await self._client.head(url)
            headers = response.headers
            length = headers.get("content-length")
            return HeadInfo(
                etag=headers.get("etag"),
                last_modified=headers.get("last-modified"),
                content_type=headers.get("content-type"),
                content_length=int(length) if length and length.isdigit() else None,
            )
        except httpx.HTTPError as exc:
            logger.info("head_failed", url=url, error=str(exc))
            return HeadInfo()

    async def download(self, url: str, key: str, *, timeout: int | None = None) -> DownloadResult:
        """Download ``url`` and store it at ``key`` with retry + rate limiting."""
        attempts = 0
        request_timeout = timeout or self._config.timeout

        async def _attempt() -> DownloadResult:
            nonlocal attempts
            attempts += 1
            await self._rate_limiter.acquire(url)
            logger.info("download_started", url=url, key=key, attempt=attempts)
            response = await self._client.get(url, timeout=request_timeout)
            response.raise_for_status()
            data = response.content
            stored = await self.storage.save_bytes(
                key, data, content_type=response.headers.get("content-type")
            )
            digest = sha256_bytes(data)
            logger.info(
                "download_completed",
                url=url,
                key=key,
                sha256=digest,
                bytes=stored.size,
                attempt=attempts,
            )
            return DownloadResult(
                url=url,
                key=key,
                status="completed",
                storage_uri=stored.uri,
                sha256=digest,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                content_type=response.headers.get("content-type"),
                size=stored.size,
                attempts=attempts,
            )

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._config.retry_attempts),
                wait=wait_exponential(multiplier=self._config.retry_backoff_seconds),
                retry=retry_if_exception_type(_RETRYABLE),
                reraise=True,
            ):
                with attempt:
                    return await _attempt()
        except Exception as exc:  # noqa: BLE001 - report failure as a result, don't crash the batch
            logger.info("download_failed", url=url, key=key, attempts=attempts, error=str(exc))
            return DownloadResult(
                url=url, key=key, status="failed", attempts=attempts, error=str(exc)
            )
        # Unreachable, but satisfies the type checker.
        return DownloadResult(url=url, key=key, status="failed", attempts=attempts)

    async def download_many(self, items: Iterable[dict[str, Any]]) -> list[DownloadResult]:
        """Download many artefacts concurrently, bounded by ``max_parallel``.

        Each item is a mapping with ``url`` and optional ``key`` (defaults to the
        URL's last path segment). Failures are captured per-item; the batch
        always resolves.
        """
        items = list(items)
        limiter = anyio.CapacityLimiter(self._config.max_parallel)
        results: list[DownloadResult] = [None] * len(items)  # type: ignore[list-item]

        async def _run(index: int, item: dict[str, Any]) -> None:
            async with limiter:
                key = item.get("key") or item["url"].split("/")[-1] or "download.bin"
                results[index] = await self.download(item["url"], key)

        async with anyio.create_task_group() as tg:
            for index, item in enumerate(items):
                tg.start_soon(_run, index, item)
        return results
