import asyncio

from crawler.config.settings import CrawlerSettings
from crawler.discovery.service import DiscoveryService


def test_discovery_returns_categories_for_configured_source(monkeypatch):
    settings = CrawlerSettings.from_env()
    service = DiscoveryService(settings)

    class FakeResponse:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    class FakeSession:
        async def get(self, url):
            return FakeResponse("<html><body><a href='/legal/acts'>Acts</a><a href='/legal/rules'>Rules</a></body></html>")

        async def aclose(self):
            return None

    service._session = FakeSession()
    categories = asyncio.run(service.discover_categories(settings.discovery.sources[0]))

    assert len(categories) == 2
    assert categories[0].name == "Acts"
    assert categories[1].name == "Rules"

    asyncio.run(service.close())
