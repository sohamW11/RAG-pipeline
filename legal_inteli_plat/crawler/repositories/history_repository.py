"""Repositories for audit tables: ``crawl_history`` and ``download_history``."""

from __future__ import annotations

from typing import Optional

from crawler.models.registry import CrawlHistory, DownloadHistory
from crawler.repositories.base import AsyncRepository
from crawler.utils.time import utcnow


class CrawlHistoryRepository(AsyncRepository[CrawlHistory]):
    """Persistence for crawl-run audit records."""

    model = CrawlHistory

    async def start(self, category_id: Optional[int]) -> CrawlHistory:
        """Open a new crawl-history record in the ``running`` state."""
        record = CrawlHistory(category_id=category_id, status="running")
        return await self.add(record)

    async def finish(
        self,
        record: CrawlHistory,
        *,
        status: str,
        found: int = 0,
        new: int = 0,
        changed: int = 0,
        skipped: int = 0,
        details: Optional[str] = None,
    ) -> CrawlHistory:
        """Close a crawl-history record with outcome counters."""
        record.status = status
        record.documents_found = found
        record.documents_new = new
        record.documents_changed = changed
        record.documents_skipped = skipped
        record.details = details
        record.completed_at = utcnow()
        await self.session.flush()
        return record


class DownloadHistoryRepository(AsyncRepository[DownloadHistory]):
    """Persistence for download-attempt audit records."""

    model = DownloadHistory

    async def record(
        self,
        *,
        document_id: Optional[int],
        url: str,
        status: str,
        attempts: int = 1,
        sha256: Optional[str] = None,
        storage_uri: Optional[str] = None,
        error: Optional[str] = None,
    ) -> DownloadHistory:
        """Persist the outcome of a single download attempt."""
        record = DownloadHistory(
            document_id=document_id,
            url=url,
            status=status,
            attempts=attempts,
            sha256=sha256,
            storage_uri=storage_uri,
            error=error,
            completed_at=utcnow() if status in {"completed", "failed", "skipped"} else None,
        )
        return await self.add(record)
