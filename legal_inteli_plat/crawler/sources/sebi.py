"""SEBI source adapter.

Encodes the navigation specific to SEBI's legal section:

1. **Discovery** -- ``/legal.html`` lists the legal categories (Acts, Rules,
   Regulations, Circulars, ...). Each links to a listing action carrying an
   ``ssid`` query parameter that identifies the category.
2. **Listing** -- a category is paged through SEBI's AJAX endpoint
   ``/sebiweb/ajax/home/getnewslistinfo.jsp``. A page must be requested with a
   live ``JSESSIONID`` cookie (seeded by first visiting the listing action) and
   a ``Referer`` header, otherwise the server answers ``530``. The page index is
   passed as ``doDirect`` (0-based). The response is
   ``<listing-html>#@#<breadcrumb-html>``.
3. **Detail -> PDF** -- each listing row links to a detail HTML page whose PDF
   is embedded in an ``<iframe src="../../../web/?file=/sebi_data/attachdocs/
   NNN.pdf">``. The real, directly-downloadable PDF is the ``file`` parameter
   resolved against the site root.

The three ``parse_*`` functions are pure (HTML in, data out) so they are unit
tested without any network. :class:`SebiSource` adds the async HTTP plumbing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from crawler.config.settings import CrawlerSettings, SourceConfig, get_settings
from crawler.interfaces.rate_limiter import RateLimiter
from crawler.utils.logging import get_logger
from crawler.utils.rate_limit import InMemoryRateLimiter

logger = get_logger("crawler.sources.sebi")

# Marker separating the listing fragment from the breadcrumb in AJAX responses.
_AJAX_SEPARATOR = "#@#"


def _is_retryable(exc: BaseException) -> bool:
    """Retry transient transport errors and 5xx responses, not 4xx."""
    if isinstance(exc, httpx.TransportError):  # disconnects, timeouts, resets
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


@dataclass(frozen=True)
class SebiCategory:
    """A legal category (or its archive) to crawl.

    ``ajax_endpoint`` is the listing pagination endpoint: ``None`` means the
    default active-listing endpoint; archive categories carry their own.
    """

    name: str
    ssid: str
    url: str
    ajax_endpoint: str | None = None
    is_archive: bool = False


# Sections that expose a separate "Historical Data" / "Archive" tab, mapped to
# (seed action param, archive listing AJAX endpoint). SEBI's own JS points the
# General Orders archive at the Guideline endpoint (their copy-paste); it is
# empty in any case, as are Master Circulars' -- kept so a future population is
# picked up automatically.
_ARCHIVE_DEFS: dict[str, tuple[str, str]] = {
    "Circulars": ("doListingCirArchive", "/sebiweb/ajax/home/getArchiveCircularlistinfo.jsp"),
    "Guidelines": ("doListingGuidelineArchive", "/sebiweb/ajax/home/getArchiveGuidelinelistinfo.jsp"),
    "Master Circulars": (
        "doListingMasterCircularArchive",
        "/sebiweb/ajax/home/getArchiveMasterCircularlistinfo.jsp",
    ),
    "General Orders": (
        "doListingGeneralOrderArchive",
        "/sebiweb/ajax/home/getArchiveGuidelinelistinfo.jsp",
    ),
}


@dataclass
class SebiListingItem:
    """One document row on a category listing page (pre-PDF-resolution)."""

    title: str
    detail_url: str
    issued_year: Optional[int] = None
    pdf_urls: list[str] = field(default_factory=list)


@dataclass
class SebiDetail:
    """Metadata + PDF links resolved from a document's detail page."""

    title: Optional[str] = None
    publication_date: Optional[datetime] = None
    category_label: Optional[str] = None
    pdf_urls: list[str] = field(default_factory=list)


# "Apr 01, 2021 | Acts" -- the date/category strip shown under the detail title.
_DETAIL_DATE_RE = re.compile(
    r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s*\|\s*([A-Za-z][A-Za-z /&-]*)"
)


# --------------------------------------------------------------------------- #
# Pure parsing helpers (no I/O -- unit-tested with saved fixtures)
# --------------------------------------------------------------------------- #
def parse_categories(html: str, base_url: str, keywords: list[str]) -> list[SebiCategory]:
    """Extract legal categories from the ``legal.html`` landing page.

    Args:
        html: The landing page HTML.
        base_url: Site root used to absolutise links.
        keywords: Link texts to promote to categories. Empty means "take any
            link that looks like a category listing action".

    Returns:
        Categories in document order, de-duplicated by ``ssid``.
    """
    wanted = {k.strip().lower() for k in keywords}
    soup = BeautifulSoup(html, "html.parser")
    out: list[SebiCategory] = []
    seen: set[str] = set()

    for link in soup.select("a[href]"):
        href = link["href"]
        if "doListing" not in href and "ssid=" not in href:
            continue
        ssid_values = parse_qs(urlparse(href).query).get("ssid")
        if not ssid_values:
            continue
        ssid = ssid_values[0]
        text = link.get_text(" ", strip=True)
        if not text:
            continue
        if wanted and text.lower() not in wanted:
            continue
        if ssid in seen:
            continue
        seen.add(ssid)
        out.append(SebiCategory(name=text, ssid=ssid, url=urljoin(base_url, href)))
    return out


def parse_listing_rows(fragment_html: str, base_url: str) -> list[SebiListingItem]:
    """Extract document rows from a listing page (or AJAX fragment).

    Each qualifying row has an issued-year cell and a link to the document's
    detail page. Header rows (no link) are skipped.
    """
    soup = BeautifulSoup(fragment_html, "html.parser")
    items: list[SebiListingItem] = []
    seen: set[str] = set()

    for row in soup.select("tr"):
        anchor = row.find("a", href=True)
        if anchor is None:
            continue
        title = anchor.get_text(" ", strip=True)
        detail_url = urljoin(base_url, anchor["href"])
        if not title or detail_url in seen:
            continue
        seen.add(detail_url)

        year: Optional[int] = None
        for cell in row.find_all("td"):
            token = cell.get_text(" ", strip=True)
            if token.isdigit() and len(token) == 4:
                year = int(token)
                break
        items.append(SebiListingItem(title=title, detail_url=detail_url, issued_year=year))
    return items


def parse_pdf_urls(detail_html: str, base_url: str) -> list[str]:
    """Resolve the downloadable PDF URL(s) from a document detail page.

    Handles SEBI's iframe viewer (``web/?file=/sebi_data/attachdocs/NNN.pdf``)
    as well as any plain ``.pdf`` anchors. Returns absolute URLs, de-duplicated
    and order-preserving.
    """
    soup = BeautifulSoup(detail_html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()

    def _add(candidate: Optional[str]) -> None:
        if not candidate:
            return
        absolute = urljoin(base_url, candidate)
        if absolute not in seen:
            seen.add(absolute)
            urls.append(absolute)

    # Iframe viewer: the ?file= parameter points at the real PDF.
    for frame in soup.find_all("iframe", src=True):
        src = frame["src"]
        file_param = parse_qs(urlparse(src).query).get("file")
        if file_param:
            _add(file_param[0])
        elif ".pdf" in src.lower():
            _add(src)

    # Direct links to PDFs / attachment paths.
    for anchor in soup.select("a[href]"):
        href = anchor["href"]
        low = href.lower()
        if low.endswith(".pdf") or "/attachdocs/" in low or "attachdoc" in low:
            _add(href)

    return urls


def parse_detail_metadata(detail_html: str, base_url: str) -> SebiDetail:
    """Extract title, publication date and PDF links from a detail page.

    SEBI's per-document ``<meta>`` tags are generic site boilerplate, so the
    real signal comes from the ``<h1>`` (full title) and the "Mmm DD, YYYY |
    Category" strip beneath it.
    """
    soup = BeautifulSoup(detail_html, "html.parser")
    detail = SebiDetail(pdf_urls=parse_pdf_urls(detail_html, base_url))

    heading = soup.find("h1")
    if heading and heading.get_text(strip=True):
        detail.title = heading.get_text(" ", strip=True)
    elif soup.title:
        # Fall back to the <title>, dropping the "SEBI | " prefix.
        detail.title = re.sub(r"^\s*SEBI\s*\|\s*", "", soup.title.get_text(" ", strip=True)) or None

    match = _DETAIL_DATE_RE.search(soup.get_text(" ", strip=True))
    if match:
        detail.category_label = match.group(2).strip()
        try:
            detail.publication_date = datetime.strptime(match.group(1), "%b %d, %Y").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            detail.publication_date = None
    return detail


# --------------------------------------------------------------------------- #
# Async adapter (HTTP plumbing over the pure parsers)
# --------------------------------------------------------------------------- #
class SebiSource:
    """Async navigator for SEBI's legal section."""

    def __init__(
        self,
        source: SourceConfig,
        settings: CrawlerSettings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._source = source
        self._settings = settings or get_settings()
        self._base = source.base_url.rstrip("/")
        download = self._settings.download
        self._client = client or httpx.AsyncClient(
            timeout=download.timeout,
            follow_redirects=True,
            headers={
                # A browser-like UA -- SEBI rejects some default clients.
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                )
            },
        )
        self._rate_limiter = rate_limiter or InMemoryRateLimiter(download.rate_limit_per_second)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def _send(self, url: str, make_request) -> httpx.Response:
        """Execute a request with rate limiting + retry on transient failures.

        SEBI occasionally drops connections ("Server disconnected") under load;
        retrying with backoff lets a run ride through those without aborting.
        """
        download = self._settings.download
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(download.retry_attempts),
            wait=wait_exponential(multiplier=download.retry_backoff_seconds),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                await self._rate_limiter.acquire(url)
                response = await make_request()
                response.raise_for_status()
                return response
        raise RuntimeError("unreachable")  # pragma: no cover

    async def _get(self, url: str, *, referer: str | None = None) -> httpx.Response:
        headers = {"Referer": referer} if referer else None
        return await self._send(url, lambda: self._client.get(url, headers=headers))

    async def discover_categories(self) -> list[SebiCategory]:
        """Fetch ``legal.html`` and return the configured legal categories."""
        landing = urljoin(self._base + "/", self._source.legal_path.lstrip("/"))
        response = await self._get(landing)
        categories = parse_categories(response.text, self._base, self._source.discovery_keywords)
        logger.info(
            "sebi_categories_discovered",
            count=len(categories),
            categories=[c.name for c in categories],
        )
        return categories

    def archive_categories(self, active: list[SebiCategory]) -> list[SebiCategory]:
        """Derive the archive ("Historical Data") categories from active ones.

        Each archive reuses its section's ``ssid`` but seeds from the archive
        action URL and pages via the archive AJAX endpoint. Empty archives (SEBI
        shows "No record(s) available") simply yield nothing.
        """
        archives: list[SebiCategory] = []
        for category in active:
            spec = _ARCHIVE_DEFS.get(category.name)
            if spec is None:
                continue
            action, endpoint = spec
            url = urljoin(
                self._base + "/",
                f"sebiweb/home/HomeAction.do?{action}=yes&sid=1&ssid={category.ssid}&smid=0",
            )
            archives.append(
                SebiCategory(
                    name=f"{category.name} (Archive)",
                    ssid=category.ssid,
                    url=url,
                    ajax_endpoint=endpoint,
                    is_archive=True,
                )
            )
        return archives

    async def iter_listing(
        self, category: SebiCategory, *, max_pages: int = 0
    ) -> AsyncIterator[SebiListingItem]:
        """Yield every listing item for a category, paging via the AJAX endpoint.

        Args:
            category: The category to page through.
            max_pages: Stop after this many pages (0 = all pages).

        Yields:
            One :class:`SebiListingItem` per document row, in listing order.
        """
        endpoint = category.ajax_endpoint or self._source.listing_ajax_path
        ajax_url = urljoin(self._base + "/", endpoint.lstrip("/"))
        # Seed the session cookie (JSESSIONID) by visiting the listing action.
        await self._get(category.url)

        page = 0
        seen_details: set[str] = set()
        while True:
            if max_pages and page >= max_pages:
                break
            data = {
                "nextValue": "1",
                "next": "n",
                "search": "",
                "fromDate": "",
                "toDate": "",
                "fromYear": "",
                "toYear": "",
                "deptId": "",
                "sid": "1",
                "ssid": category.ssid,
                "smid": "0",
                "ssidhidden": category.ssid,
                "intmid": "-1",
                "sText": "Legal",
                "ssText": category.name.replace(" (Archive)", ""),
                "smText": "",
                "doDirect": str(page),
            }
            response = await self._send(
                ajax_url,
                lambda: self._client.post(
                    ajax_url,
                    data=data,
                    headers={"X-Requested-With": "XMLHttpRequest", "Referer": category.url},
                ),
            )
            fragment = response.text.split(_AJAX_SEPARATOR, 1)[0]
            rows = parse_listing_rows(fragment, self._base)

            fresh = [r for r in rows if r.detail_url not in seen_details]
            if not fresh:
                # No rows, or the server clamped us back to an already-seen page.
                break
            for row in fresh:
                seen_details.add(row.detail_url)
                yield row
            logger.info(
                "sebi_listing_page",
                category=category.name,
                page=page,
                rows=len(fresh),
            )
            page += 1

    async def fetch_detail(self, detail_url: str) -> SebiDetail:
        """Fetch a detail page and return its metadata and PDF URL(s)."""
        response = await self._get(detail_url, referer=self._base + "/legal.html")
        return parse_detail_metadata(response.text, self._base)

    async def resolve_pdf_urls(self, detail_url: str) -> list[str]:
        """Backwards-compatible helper: just the PDF URL(s) from a detail page."""
        return (await self.fetch_detail(detail_url)).pdf_urls
