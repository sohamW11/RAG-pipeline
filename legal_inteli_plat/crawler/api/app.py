"""FastAPI application for the crawler service.

Exposes health/status, read access to the crawled metadata (categories,
documents, jobs) and endpoints to trigger crawls. Crawls run in the background
and are tracked as ``crawler_jobs`` rows so progress is observable via
``GET /jobs``.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.database.session import Database, get_database
from crawler.repositories.category_repository import CategoryRepository
from crawler.repositories.document_repository import DocumentRepository
from crawler.repositories.job_repository import CrawlerJobRepository
from crawler.services.crawl_service import CrawlService
from crawler.utils.logging import get_logger

logger = get_logger("crawler.api")

app = FastAPI(title="Crawler Service", version="0.1.0")


@app.on_event("startup")
async def _on_startup() -> None:
    """Ensure the schema exists (idempotent; prod also runs Alembic)."""
    await get_database().create_all()


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped async session."""
    db: Database = get_database()
    async with db.sessionmaker() as session:
        yield session


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class CrawlRequest(BaseModel):
    """Payload for :func:`crawl` (all fields optional)."""

    source: str = "sebi"
    categories: Optional[list[str]] = None
    max_pages: Optional[int] = None
    max_documents: Optional[int] = None
    force: Optional[bool] = None


class CategoryCrawlRequest(BaseModel):
    """Payload for :func:`crawl_category`."""

    source: str = "sebi"
    category: str
    max_pages: Optional[int] = None
    max_documents: Optional[int] = None
    force: Optional[bool] = None


# --------------------------------------------------------------------------- #
# Background crawl runner (tracked as a crawler_jobs row)
# --------------------------------------------------------------------------- #
async def _run_crawl(job_id: int, kwargs: dict[str, Any]) -> None:
    """Execute a crawl in the background, updating its job row."""
    db = get_database()
    service = CrawlService()
    status, error, summary = "completed", None, None
    try:
        result = await service.crawl(kwargs.pop("source", "sebi"), **kwargs)
        summary = {
            "found": result.found,
            "downloaded": result.downloaded,
            "skipped": result.skipped,
            "failed": result.failed,
        }
    except Exception as exc:  # noqa: BLE001 - record failure on the job
        status, error = "failed", str(exc)
        logger.info("crawl_job_failed", job_id=job_id, error=error)
    finally:
        await service.close()

    async with db.sessionmaker() as session:
        jobs = CrawlerJobRepository(session)
        job = await jobs.get(job_id)
        if job is not None:
            job.status = status
            job.error = error
            if summary is not None:
                job.payload = json.dumps({**json.loads(job.payload or "{}"), "result": summary})
            await session.commit()
    logger.info("crawl_job_finished", job_id=job_id, status=status, summary=summary)


async def _enqueue_crawl(payload: dict[str, Any], background: BackgroundTasks) -> dict[str, Any]:
    """Create a job row and schedule the crawl to run after the response."""
    db = get_database()
    async with db.sessionmaker() as session:
        jobs = CrawlerJobRepository(session)
        job = await jobs.enqueue(job_type="crawl", payload=payload)
        await session.commit()
        job_id, job_uuid = job.id, job.uuid

    # Strip None so service-layer/config defaults apply.
    kwargs = {k: v for k, v in payload.items() if v is not None}
    background.add_task(_run_crawl, job_id, kwargs)
    return {"status": "accepted", "job_id": job_id, "job_uuid": job_uuid}


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/status")
async def status(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Summary counts across the metadata registry."""
    return {
        "status": "running",
        "categories": await CategoryRepository(session).count(),
        "documents": await DocumentRepository(session).count(),
        "jobs": await CrawlerJobRepository(session).count(),
    }


@app.get("/categories")
async def categories(session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    """List categories in the registry."""
    rows = await CategoryRepository(session).list(limit=500)
    return [
        {
            "uuid": c.uuid,
            "source": c.source,
            "name": c.name,
            "url": c.url,
            "enabled": c.enabled,
            "last_crawl": c.last_crawl.isoformat() if c.last_crawl else None,
        }
        for c in rows
    ]


@app.get("/documents")
async def documents(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """List crawled document metadata, newest first."""
    rows = await DocumentRepository(session).list(limit=limit, offset=offset)
    return [
        {
            "uuid": d.uuid,
            "title": d.title,
            "document_number": d.document_number,
            "category": d.category_name,
            "pdf_url": d.pdf_url,
            "html_url": d.html_url,
            "publication_date": d.publication_date.isoformat() if d.publication_date else None,
            "content_hash": d.content_hash,
        }
        for d in rows
    ]


@app.get("/jobs")
async def jobs(session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    """List recent crawl jobs, newest first."""
    rows = await CrawlerJobRepository(session).list_recent(limit=50)
    return [
        {
            "uuid": j.uuid,
            "job_type": j.job_type,
            "status": j.status,
            "attempts": j.attempts,
            "payload": json.loads(j.payload or "{}"),
            "error": j.error,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        }
        for j in rows
    ]


@app.post("/crawl")
async def crawl(req: CrawlRequest, background: BackgroundTasks) -> dict[str, Any]:
    """Trigger a background crawl of a source (optionally limited)."""
    return await _enqueue_crawl(req.model_dump(), background)


@app.post("/crawl/category")
async def crawl_category(req: CategoryCrawlRequest, background: BackgroundTasks) -> dict[str, Any]:
    """Trigger a background crawl of a single category."""
    payload = req.model_dump()
    category = payload.pop("category")
    payload["categories"] = [category]
    return await _enqueue_crawl(payload, background)
