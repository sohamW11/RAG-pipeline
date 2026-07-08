"""Listing crawler -- orchestrates fetching and metadata extraction.

Given a source and category, it fetches the listing page(s) and returns the
extracted :class:`DocumentMetadata`. It owns no persistence and performs no
downloads; those concerns belong to the service layer and download manager
respectively. This keeps the crawler a pure, easily-tested transformation:
*(source, category) -> list[DocumentMetadata]*.
"""

from __future__ import annotations

from urllib.parse import urljoin

from crawler.config.settings import CategoryConfig, CrawlerSettings, SourceConfig
from crawler.crawler.extractor import DocumentMetadata, get_extractor
from crawler.crawler.fetcher import PageFetcher, create_fetcher
from crawler.utils.logging import get_logger

logger = get_logger("crawler.listing")


class ListingCrawler:
    """Crawls listing pages for a configured source."""

    def __init__(
        self,
        source: SourceConfig,
        settings: CrawlerSettings,
        *,
        fetcher: PageFetcher | None = None,
    ) -> None:
        self._source = source
        self._settings = settings
        # Fetcher can be injected (tests) or built from configuration.
        self._fetcher = fetcher or create_fetcher(source, settings.download)
        self._extractor = get_extractor(source.name)

    async def close(self) -> None:
        """Release the underlying fetcher's resources."""
        await self._fetcher.close()

    def _category_url(self, category: CategoryConfig) -> str:
        """Resolve the absolute listing URL for a category."""
        path = category.path or self._source.category_path
        return urljoin(self._source.base_url.rstrip("/") + "/", path.lstrip("/"))

    async def crawl_category(self, category: CategoryConfig) -> list[DocumentMetadata]:
        """Fetch and extract metadata for a single category's listing page."""
        url = self._category_url(category)
        logger.info("category_crawl_started", source=self._source.name, category=category.name, url=url)
        html = await self._fetcher.fetch(url)
        documents = self._extractor.extract(
            html,
            base_url=url,
            selectors=self._source.selectors,
            category=category,
        )
        logger.info(
            "category_crawl_completed",
            source=self._source.name,
            category=category.name,
            documents=len(documents),
        )
        return documents

    async def crawl_all(self) -> dict[str, list[DocumentMetadata]]:
        """Crawl every enabled category configured for the source."""
        results: dict[str, list[DocumentMetadata]] = {}
        for category in self._source.categories:
            if not category.enabled:
                continue
            results[category.name] = await self.crawl_category(category)
        return results
