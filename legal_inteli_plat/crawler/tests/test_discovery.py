"""Discovery service tests.

Discovery is configuration-driven and depends only on the :class:`PageFetcher`
contract, so we inject a fake fetcher and assert on the categories produced --
no network, no browser.
"""

from crawler.config.settings import CrawlerSettings, SourceConfig
from crawler.crawler.fetcher import PageFetcher
from crawler.discovery.service import DiscoveryService


class FakeFetcher(PageFetcher):
    """A :class:`PageFetcher` that returns canned HTML for any URL."""

    def __init__(self, html: str) -> None:
        self.html = html
        self.closed = False

    async def fetch(self, url: str) -> str:
        return self.html

    async def close(self) -> None:
        self.closed = True


async def test_discovery_scrapes_landing_page_by_keywords():
    settings = CrawlerSettings()
    source = SourceConfig(
        name="sebi",
        base_url="https://example.com",
        category_path="/legal",
        discovery_keywords=["Acts", "Rules"],
    )
    html = (
        "<html><body>"
        "<a href='/legal/acts'>Acts</a>"
        "<a href='/legal/rules'>Rules</a>"
        "<a href='/about'>About Us</a>"  # not a keyword -> ignored
        "</body></html>"
    )
    service = DiscoveryService(settings, fetcher=FakeFetcher(html))

    categories = await service.discover(source)

    assert [c.name for c in categories] == ["Acts", "Rules"]
    assert categories[0].url == "https://example.com/legal/acts"
    assert all(c.source == "sebi" for c in categories)


async def test_discovery_uses_declared_categories_without_fetching():
    settings = CrawlerSettings()
    source = SourceConfig(
        name="sebi",
        base_url="https://example.com",
        category_path="/legal",
        categories=[
            {"name": "Acts", "path": "/legal/acts"},
            {"name": "Circulars", "path": "/legal/circulars", "enabled": False},
        ],
    )
    # A fetcher that would raise if the landing page were scraped.
    service = DiscoveryService(settings, fetcher=FakeFetcher(""))

    categories = await service.discover(source)

    # Only the enabled declared category is returned; no scraping happens.
    assert [c.name for c in categories] == ["Acts"]
    assert categories[0].url == "https://example.com/legal/acts"
