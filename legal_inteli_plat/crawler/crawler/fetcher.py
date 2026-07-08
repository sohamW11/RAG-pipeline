"""Page fetchers.

A :class:`PageFetcher` retrieves the raw HTML of a listing page. Two
implementations are provided:

* :class:`HttpxFetcher`      -- fast async HTTP, the default; good for
  server-rendered regulator sites.
* :class:`PlaywrightFetcher` -- drives a headless browser for pages that need
  JavaScript to render their listings. Playwright is an optional dependency,
  imported lazily so the service runs without it.

Both share the :class:`PageFetcher` contract, so the crawler picks one by
configuration (``source.fetcher``) without caring which is in use.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import httpx

from crawler.config.settings import DownloadConfig, SourceConfig
from crawler.utils.logging import get_logger

logger = get_logger("crawler.fetcher")


class PageFetcher(ABC):
    """Contract for retrieving a page's HTML."""

    @abstractmethod
    async def fetch(self, url: str) -> str:
        """Return the fully-rendered HTML for ``url``."""

    async def close(self) -> None:
        """Release any held resources. Default: no-op."""


class HttpxFetcher(PageFetcher):
    """Async HTTP fetcher backed by httpx."""

    def __init__(self, *, user_agent: str, timeout: int = 30) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )

    async def fetch(self, url: str) -> str:
        response = await self._client.get(url)
        response.raise_for_status()
        logger.info("page_fetched", url=url, status=response.status_code, bytes=len(response.text))
        return response.text

    async def close(self) -> None:
        await self._client.aclose()


class PlaywrightFetcher(PageFetcher):
    """Headless-browser fetcher for JavaScript-rendered listings."""

    def __init__(self, *, user_agent: str, timeout: int = 30) -> None:
        self._user_agent = user_agent
        self._timeout_ms = timeout * 1000
        self._browser = None
        self._playwright = None

    async def _ensure_browser(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "Playwright is required for the 'playwright' fetcher. "
                "Install with `pip install playwright` and run `playwright install chromium`."
            ) from exc
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)

    async def fetch(self, url: str) -> str:  # pragma: no cover - requires browser
        await self._ensure_browser()
        assert self._browser is not None
        context = await self._browser.new_context(user_agent=self._user_agent)
        page = await context.new_page()
        try:
            await page.goto(url, timeout=self._timeout_ms, wait_until="networkidle")
            html = await page.content()
            logger.info("page_fetched", url=url, fetcher="playwright", bytes=len(html))
            return html
        finally:
            await context.close()

    async def close(self) -> None:  # pragma: no cover - requires browser
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()


def create_fetcher(source: SourceConfig, download: DownloadConfig) -> PageFetcher:
    """Instantiate the fetcher named by ``source.fetcher``."""
    kind = source.fetcher.lower()
    if kind == "httpx":
        return HttpxFetcher(user_agent=download.user_agent, timeout=download.timeout)
    if kind == "playwright":
        return PlaywrightFetcher(user_agent=download.user_agent, timeout=download.timeout)
    raise ValueError(f"Unknown fetcher: {source.fetcher!r}")
