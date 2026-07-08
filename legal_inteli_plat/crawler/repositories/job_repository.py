"""Repositories for ``crawler_jobs`` and ``scheduler_jobs``."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import select

from crawler.models.registry import CrawlerJob, SchedulerJob
from crawler.repositories.base import AsyncRepository
from crawler.utils.time import utcnow


class CrawlerJobRepository(AsyncRepository[CrawlerJob]):
    """Persistence for units of work processed by workers."""

    model = CrawlerJob

    async def enqueue(
        self,
        *,
        job_type: str,
        payload: Optional[dict[str, Any]] = None,
        priority: int = 5,
        max_attempts: int = 3,
        scheduled_for: Optional[datetime] = None,
    ) -> CrawlerJob:
        """Create a queued job."""
        job = CrawlerJob(
            job_type=job_type,
            status="queued",
            priority=priority,
            max_attempts=max_attempts,
            payload=json.dumps(payload or {}),
            scheduled_for=scheduled_for,
        )
        return await self.add(job)

    async def claim_next(self) -> Optional[CrawlerJob]:
        """Atomically pick the highest-priority queued job and mark it running."""
        result = await self.session.execute(
            select(CrawlerJob)
            .where(CrawlerJob.status == "queued")
            .order_by(CrawlerJob.priority.asc(), CrawlerJob.id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job = result.scalars().first()
        if job is not None:
            job.status = "running"
            job.attempts += 1
            await self.session.flush()
        return job

    async def complete(self, job: CrawlerJob, *, status: str, error: Optional[str] = None) -> None:
        """Mark a job terminal (``completed`` / ``failed``)."""
        job.status = status
        job.error = error
        await self.session.flush()

    async def list_recent(self, *, limit: int = 50) -> Sequence[CrawlerJob]:
        """Return recent jobs, newest first."""
        return await self.list(limit=limit)


class SchedulerJobRepository(AsyncRepository[SchedulerJob]):
    """Persistence for declarative recurring jobs."""

    model = SchedulerJob

    async def get_by_name(self, name: str) -> Optional[SchedulerJob]:
        """Look up a scheduler job by its unique name."""
        result = await self.session.execute(select(SchedulerJob).where(SchedulerJob.name == name))
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        name: str,
        schedule: str,
        job_type: str,
        priority: int = 5,
        enabled: bool = True,
        payload: Optional[dict[str, Any]] = None,
    ) -> SchedulerJob:
        """Insert or update a scheduler job (idempotent on ``name``)."""
        existing = await self.get_by_name(name)
        serialized = json.dumps(payload or {})
        if existing is None:
            job = SchedulerJob(
                name=name,
                schedule=schedule,
                job_type=job_type,
                priority=priority,
                enabled=enabled,
                payload=serialized,
            )
            return await self.add(job)
        existing.schedule = schedule
        existing.job_type = job_type
        existing.priority = priority
        existing.enabled = enabled
        existing.payload = serialized
        await self.session.flush()
        return existing

    async def list_enabled(self) -> Sequence[SchedulerJob]:
        """Return enabled scheduler jobs."""
        result = await self.session.execute(
            select(SchedulerJob).where(SchedulerJob.enabled.is_(True))
        )
        return result.scalars().all()

    async def mark_run(self, job: SchedulerJob, next_run: Optional[datetime]) -> None:
        """Record that a scheduled job fired and compute its next run."""
        job.last_run = utcnow()
        job.next_run = next_run
        await self.session.flush()
