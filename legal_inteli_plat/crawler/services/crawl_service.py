"""Crawl orchestration service.

Ties the pieces together into a runnable pipeline:

    discover categories -> page through each listing -> resolve the PDF(s) on
    each document's detail page -> (skip if already stored) -> download -> store
    -> persist metadata, versions and audit history.

It owns no HTML knowledge (that lives in the source adapter) and no storage
knowledge (that lives behind :class:`StorageInterface`); it is pure
coordination plus persistence, which keeps it reusable across regulators.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx

from crawler.config.settings import CrawlerSettings, SourceConfig, get_settings
from crawler.database.session import Database, get_database
from crawler.download.manager import DownloadManager
from crawler.interfaces.rate_limiter import RateLimiter
from crawler.interfaces.storage import StorageInterface
from crawler.repositories.category_repository import CategoryRepository
from crawler.repositories.document_repository import DocumentRepository
from crawler.repositories.history_repository import (
    CrawlHistoryRepository,
    DownloadHistoryRepository,
)
from crawler.repositories.version_repository import DocumentVersionRepository
from crawler.sources.sebi import SebiCategory, SebiDetail, SebiSource
from crawler.storage.factory import create_storage
from crawler.utils.keywords import extract_keywords
from crawler.utils.logging import get_logger
from crawler.utils.rate_limit import InMemoryRateLimiter

logger = get_logger("crawler.service.crawl")

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_DOCNUM_RE = re.compile(r"_(\d+)\.html?$", re.IGNORECASE)
_MAX_SLUG_LEN = 80


def _slug(text: str, *, max_len: int = _MAX_SLUG_LEN) -> str:
    """Filesystem/URL-safe slug from arbitrary text, length-bounded."""
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or "item"


def _document_number(detail_url: str) -> Optional[str]:
    """SEBI detail URLs end with ``_<id>.html``; use that id as a stable key."""
    match = _DOCNUM_RE.search(detail_url)
    return match.group(1) if match else None


def _pdf_extension(pdf_url: str) -> str:
    """Return the file extension of a PDF URL (``.pdf`` fallback)."""
    ext = posixpath.splitext(urlparse(pdf_url).path)[1].lower()
    return ext if ext else ".pdf"


def _storage_key(
    *,
    source_name: str,
    category: SebiCategory,
    title: str,
    doc_number: Optional[str],
    pdf_url: str,
    pdf_index: int,
    total_pdfs: int,
) -> str:
    """Build a human-readable, unique storage key/filename for a PDF.

    Shape: ``<source>/<category>/<title-slug>_<docid>[-<n>].<ext>`` -- readable
    at a glance, collision-free (the SEBI doc id disambiguates same-title docs;
    the ``-n`` suffix disambiguates multiple PDFs on one detail page).
    """
    stem = _slug(title)
    if doc_number:
        stem = f"{stem}_{doc_number}"
    if total_pdfs > 1:
        stem = f"{stem}-{pdf_index + 1}"
    return f"{source_name}/{_slug(category.name, max_len=40)}/{stem}{_pdf_extension(pdf_url)}"


@dataclass
class CategoryResult:
    """Per-category crawl outcome."""

    category: str
    found: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass
class CrawlSummary:
    """Aggregate outcome of a crawl run."""

    source: str
    categories: list[CategoryResult] = field(default_factory=list)

    @property
    def downloaded(self) -> int:
        return sum(c.downloaded for c in self.categories)

    @property
    def skipped(self) -> int:
        return sum(c.skipped for c in self.categories)

    @property
    def failed(self) -> int:
        return sum(c.failed for c in self.categories)

    @property
    def found(self) -> int:
        return sum(c.found for c in self.categories)


class CrawlService:
    """Runs an end-to-end crawl for a configured source and stores the PDFs."""

    def __init__(
        self,
        settings: CrawlerSettings | None = None,
        *,
        database: Database | None = None,
        storage: StorageInterface | None = None,
        download_manager: DownloadManager | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._database = database or get_database()
        self._storage = storage or create_storage(self._settings)
        # One limiter shared across listing/detail fetches AND PDF downloads so
        # the *total* request rate to the site stays within the configured bound.
        self._rate_limiter = rate_limiter or InMemoryRateLimiter(
            self._settings.download.rate_limit_per_second
        )
        # PDFs sit behind SEBI's static file server; a browser-like UA avoids
        # the occasional bot rejection seen on the dynamic pages.
        self._download = download_manager or DownloadManager(
            storage=self._storage,
            settings=self._settings,
            rate_limiter=self._rate_limiter,
            client=httpx.AsyncClient(
                timeout=self._settings.download.timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                    )
                },
            ),
        )

    async def close(self) -> None:
        """Release the download manager's HTTP client."""
        await self._download.close()

    # ------------------------------------------------------------------ #
    def _source_config(self, name: str) -> SourceConfig:
        for source in self._settings.discovery.sources:
            if source.name == name:
                return source
        raise ValueError(f"Unknown source: {name!r}")

    async def list_categories(self, source_name: str = "sebi") -> list[SebiCategory]:
        """Discover (without crawling) the categories available for a source."""
        source = self._source_config(source_name)
        adapter = SebiSource(source, self._settings, rate_limiter=self._rate_limiter)
        try:
            return await adapter.discover_categories()
        finally:
            await adapter.close()

    async def crawl(
        self,
        source_name: str = "sebi",
        *,
        categories: Optional[list[str]] = None,
        max_pages: Optional[int] = None,
        max_documents: Optional[int] = None,
        force: Optional[bool] = None,
        include_archive: bool = True,
    ) -> CrawlSummary:
        """Crawl a source end-to-end, downloading and persisting PDFs.

        Args:
            source_name: Configured source to crawl (default ``sebi``).
            categories: Restrict to these category names (case-insensitive).
                ``None`` crawls every discovered category.
            max_pages: Max listing pages per category (``None`` -> config).
            max_documents: Max documents per category (``None`` -> config).
            force: Re-download even if already stored (``None`` -> config).
            include_archive: Also crawl each section's "Historical Data" archive.

        Returns:
            A :class:`CrawlSummary` with per-category counters.
        """
        cfg = self._settings.crawl
        max_pages = cfg.max_pages_per_category if max_pages is None else max_pages
        max_documents = cfg.max_documents_per_category if max_documents is None else max_documents
        force = cfg.force if force is None else force

        source = self._source_config(source_name)
        adapter = SebiSource(source, self._settings, rate_limiter=self._rate_limiter)
        summary = CrawlSummary(source=source_name)
        wanted = {c.lower() for c in categories} if categories else None

        def _match(category: SebiCategory) -> bool:
            # Filter on the base section name so "Circulars" also selects its archive.
            base = category.name.replace(" (Archive)", "").lower()
            return not wanted or base in wanted

        try:
            discovered = await adapter.discover_categories()
            targets = [c for c in discovered if _match(c)]
            if include_archive:
                targets += [c for c in adapter.archive_categories(discovered) if _match(c)]
            logger.info(
                "crawl_started",
                source=source_name,
                categories=[c.name for c in targets],
                max_pages=max_pages,
                max_documents=max_documents,
                force=force,
            )
            for category in targets:
                result = await self._crawl_category(
                    adapter,
                    source_name,
                    category,
                    max_pages=max_pages,
                    max_documents=max_documents,
                    force=force,
                )
                summary.categories.append(result)
        finally:
            await adapter.close()

        logger.info(
            "crawl_completed",
            source=source_name,
            found=summary.found,
            downloaded=summary.downloaded,
            skipped=summary.skipped,
            failed=summary.failed,
        )
        return summary

    # ------------------------------------------------------------------ #
    async def _crawl_category(
        self,
        adapter: SebiSource,
        source_name: str,
        category: SebiCategory,
        *,
        max_pages: int,
        max_documents: int,
        force: bool,
    ) -> CategoryResult:
        """Crawl one category: page listing, resolve PDFs, download, persist."""
        result = CategoryResult(category=category.name)

        # Register the category and open a crawl-history record (own session).
        async with self._database.sessionmaker() as session:
            registry = await CategoryRepository(session).upsert(
                source=source_name,
                name=category.name,
                url=category.url,
                crawl_frequency=self._source_config(source_name).crawl_frequency,
            )
            history = await CrawlHistoryRepository(session).start(registry.id)
            await session.commit()
            registry_id, history_id = registry.id, history.id

        status, details = "completed", None
        try:
            # Phase 1 -- page the whole listing FAST (no interleaved detail
            # fetches/downloads). SEBI's AJAX pagination is session-stateful, so
            # slow interleaving lets the session expire mid-category and the
            # server silently resets to page 1, truncating large categories.
            # Materialising the list first keeps pagination tight.
            items = []
            async for item in adapter.iter_listing(category, max_pages=max_pages):
                items.append(item)
                if max_documents and len(items) >= max_documents:
                    break
            logger.info("listing_collected", category=category.name, documents=len(items))

            # Phase 2 -- resolve each detail page and download its PDF(s). Each
            # document runs in its OWN short-lived DB session so a transient
            # failure is fully isolated: the failed session is discarded and the
            # next document gets a clean one. (Sharing one session and rolling
            # back on error corrupts it under aiosqlite -> MissingGreenlet.)
            for item in items:
                result.found += 1
                await self._process_item(
                    item,
                    adapter=adapter,
                    source_name=source_name,
                    category=category,
                    registry_id=registry_id,
                    force=force,
                    result=result,
                )
        except Exception as exc:  # noqa: BLE001 - listing-level failure
            status, details = "failed", str(exc)
            logger.info("category_crawl_failed", category=category.name, error=str(exc))

        # Close out the crawl-history record (own session).
        async with self._database.sessionmaker() as session:
            history_repo = CrawlHistoryRepository(session)
            record = await history_repo.get(history_id)
            if record is not None:
                await history_repo.finish(
                    record,
                    status=status,
                    found=result.found,
                    new=result.downloaded,
                    skipped=result.skipped,
                    details=details,
                )
            await session.commit()

        logger.info(
            "category_done",
            category=category.name,
            found=result.found,
            downloaded=result.downloaded,
            skipped=result.skipped,
            failed=result.failed,
        )
        return result

    async def _process_item(
        self,
        item,
        *,
        adapter: SebiSource,
        source_name: str,
        category: SebiCategory,
        registry_id: int,
        force: bool,
        result: CategoryResult,
    ) -> None:
        """Resolve + download one listing item in its own isolated session."""
        try:
            # Cheap early-skip: the SEBI doc id is in the detail URL, so we can
            # skip already-stored documents WITHOUT fetching the detail page --
            # gentler on the site on resumed runs.
            doc_number = _document_number(item.detail_url)
            if not force and doc_number:
                async with self._database.sessionmaker() as session:
                    if await DocumentRepository(session).get_by_document_number(doc_number):
                        result.skipped += 1
                        return

            detail = await adapter.fetch_detail(item.detail_url)
            if not detail.pdf_urls:
                logger.info("no_pdf_found", detail_url=item.detail_url)
                return

            async with self._database.sessionmaker() as session:
                documents = DocumentRepository(session)
                versions = DocumentVersionRepository(session)
                downloads = DownloadHistoryRepository(session)
                for index, pdf_url in enumerate(detail.pdf_urls):
                    await self._handle_pdf(
                        session,
                        documents=documents,
                        versions=versions,
                        downloads=downloads,
                        registry_id=registry_id,
                        source_name=source_name,
                        category=category,
                        item=item,
                        detail=detail,
                        pdf_url=pdf_url,
                        pdf_index=index,
                        total_pdfs=len(detail.pdf_urls),
                        force=force,
                        result=result,
                    )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - isolate per-document failures
            result.failed += 1
            logger.info("document_failed", detail_url=item.detail_url, error=str(exc))

    async def _handle_pdf(
        self,
        session,
        *,
        documents: DocumentRepository,
        versions: DocumentVersionRepository,
        downloads: DownloadHistoryRepository,
        registry_id: int,
        source_name: str,
        category: SebiCategory,
        item,
        detail: SebiDetail,
        pdf_url: str,
        pdf_index: int,
        total_pdfs: int,
        force: bool,
        result: CategoryResult,
    ) -> None:
        """Download and persist a single PDF (idempotent unless ``force``)."""
        doc_number = _document_number(item.detail_url)
        # Prefer the fuller detail-page title over the (sometimes truncated) row.
        title = detail.title or item.title

        existing = await documents.find_existing(document_number=doc_number, pdf_url=pdf_url)
        if existing is not None and not force:
            result.skipped += 1
            logger.info("skip_existing", pdf_url=pdf_url, document_id=existing.id)
            return

        key = _storage_key(
            source_name=source_name,
            category=category,
            title=title,
            doc_number=doc_number,
            pdf_url=pdf_url,
            pdf_index=pdf_index,
            total_pdfs=total_pdfs,
        )
        download = await self._download.download(pdf_url, key)

        if download.status != "completed":
            result.failed += 1
            await downloads.record(
                document_id=existing.id if existing else None,
                url=pdf_url,
                status="failed",
                attempts=download.attempts,
                error=download.error,
            )
            return

        # Precise publication date from the detail page, else the listing year.
        pub_date = detail.publication_date or (
            datetime(item.issued_year, 1, 1, tzinfo=timezone.utc) if item.issued_year else None
        )
        keywords = ", ".join(extract_keywords(title, category=category.name))

        if existing is None:
            document = await documents.create(
                category_id=registry_id,
                title=title,
                document_number=doc_number,
                publication_date=pub_date,
                category_name=category.name,
                pdf_url=pdf_url,
                html_url=item.detail_url,
                source_url=item.detail_url,
                document_type=category.name,
                keywords=keywords,
                content_hash=download.sha256,
            )
            version_number = "1"
        else:
            document = existing
            documents.apply_metadata(
                document, {"content_hash": download.sha256, "keywords": keywords, "title": title}
            )
            latest = await versions.latest_for_document(document.id)
            version_number = str(int(latest.version_number) + 1) if latest else "1"

        await session.flush()
        await versions.create(
            document_id=document.id,
            version_number=version_number,
            url=pdf_url,
            sha256=download.sha256,
            etag=download.etag,
            last_modified=download.last_modified,
            publication_date=pub_date,
            content_type=download.content_type,
            file_size=download.size,
            storage_key=key,
            storage_uri=download.storage_uri,
        )
        await downloads.record(
            document_id=document.id,
            url=pdf_url,
            status="completed",
            attempts=download.attempts,
            sha256=download.sha256,
            storage_uri=download.storage_uri,
        )
        result.downloaded += 1
        logger.info("pdf_stored", pdf_url=pdf_url, key=key, bytes=download.size)
