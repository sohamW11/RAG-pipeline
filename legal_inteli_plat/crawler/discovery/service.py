"""Discovery service.

Discovers the legal *categories* a regulator publishes (Acts, Rules,
Regulations, Circulars, Master Circulars, Gazette Notifications, Guidance
Notes, Advisories, Orders, Consultation Papers, ...).

Two discovery modes, both fully configuration-driven -- there are **no
hardcoded URLs or category names in this module**:

1. *Declared* -- categories are listed explicitly in the source config; we
   simply resolve their absolute URLs.
2. *Scraped*  -- the source's landing page is fetched and links whose text
   matches ``discovery_keywords`` are promoted to categories.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from crawler.config.settings import CrawlerSettings, SourceConfig, get_settings
from crawler.crawler.fetcher import PageFetcher, create_fetcher
from crawler.utils.logging import get_logger

logger = get_logger("crawler.discovery")


@dataclass
class DiscoveredCategory:
    """A category surfaced by discovery, ready to be persisted in the registry."""

    source: str
    name: str
    url: str
    enabled: bool = True
    crawl_frequency: str = "daily"


class DiscoveryService:
    """Discovers crawlable categories for configured regulator sources."""

    def __init__(
        self,
        settings: CrawlerSettings | None = None,
        *,
        fetcher: PageFetcher | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        # A single injected fetcher (tests) is reused across sources when given.
        self._injected_fetcher = fetcher

    async def discover(self, source: SourceConfig) -> list[DiscoveredCategory]:
        """Discover categories for one source."""
        if not self.settings.discovery.enabled:
            logger.info("discovery_disabled")
            return []

        if source.categories:
            categories = self._from_declared(source)
        else:
            categories = await self._from_landing_page(source)

        logger.info(
            "discovery_completed",
            source=source.name,
            count=len(categories),
            categories=[c.name for c in categories],
        )
        return categories

    async def discover_all(self) -> list[DiscoveredCategory]:
        """Discover categories across every enabled source."""
        discovered: list[DiscoveredCategory] = []
        for source in self.settings.discovery.sources:
            if not source.enabled:
                continue
            discovered.extend(await self.discover(source))
        return discovered

    # ------------------------------------------------------------------ #
    # Internal strategies
    # ------------------------------------------------------------------ #
    def _from_declared(self, source: SourceConfig) -> list[DiscoveredCategory]:
        """Build categories from the explicit configuration list."""
        base = source.base_url.rstrip("/") + "/"
        return [
            DiscoveredCategory(
                source=source.name,
                name=category.name,
                url=urljoin(base, (category.path or source.category_path).lstrip("/")),
                enabled=category.enabled,
                crawl_frequency=source.crawl_frequency,
            )
            for category in source.categories
            if category.enabled
        ]

    async def _from_landing_page(self, source: SourceConfig) -> list[DiscoveredCategory]:
        """Scrape the landing page for links matching ``discovery_keywords``."""
        landing = urljoin(source.base_url.rstrip("/") + "/", source.category_path.lstrip("/"))
        fetcher = self._injected_fetcher or create_fetcher(source, self.settings.download)
        try:
            html = await fetcher.fetch(landing)
        finally:
            if self._injected_fetcher is None:
                await fetcher.close()

        keywords = {k.lower() for k in source.discovery_keywords}
        soup = BeautifulSoup(html, "html.parser")
        categories: list[DiscoveredCategory] = []
        seen: set[str] = set()

        for link in soup.select("a[href]"):
            text = (link.get_text(" ", strip=True) or "").strip()
            if not text:
                continue
            if keywords and text.lower() not in keywords:
                continue
            if text in seen:
                continue
            seen.add(text)
            categories.append(
                DiscoveredCategory(
                    source=source.name,
                    name=text,
                    url=urljoin(landing, link["href"]),
                    enabled=True,
                    crawl_frequency=source.crawl_frequency,
                )
            )
        return categories
