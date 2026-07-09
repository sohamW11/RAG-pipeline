"""Download manager tests.

The manager depends only on an ``httpx.AsyncClient``-shaped object and the
storage abstraction, so we inject a fake client and a real :class:`LocalStorage`
pointed at a temp dir -- no network, no external services.
"""

from crawler.config.settings import CrawlerSettings
from crawler.download.manager import DownloadManager
from crawler.interfaces.rate_limiter import RateLimiter
from crawler.storage import LocalStorage


class NoopRateLimiter(RateLimiter):
    """Rate limiter that never blocks, keeping tests deterministic and fast."""

    async def acquire(self, key: str = "default") -> None:
        return None


class FakeResponse:
    def __init__(self, data: bytes, headers: dict[str, str] | None = None) -> None:
        self.content = data
        self.headers = headers or {"content-type": "application/pdf", "etag": "abc123"}

    def raise_for_status(self) -> None:
        return None


class FakeClient:
    """Minimal stand-in for ``httpx.AsyncClient``."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.closed = False

    async def get(self, url: str, timeout: int | None = None) -> FakeResponse:
        return FakeResponse(self.data)

    async def head(self, url: str) -> FakeResponse:
        return FakeResponse(b"")

    async def aclose(self) -> None:
        self.closed = True


async def test_download_manager_writes_bytes(tmp_path):
    storage = LocalStorage(base_path=tmp_path)
    manager = DownloadManager(
        storage=storage,
        settings=CrawlerSettings(),
        rate_limiter=NoopRateLimiter(),
        client=FakeClient(b"hello"),
    )

    result = await manager.download("https://example.com/file.pdf", "sample/file.pdf")

    assert result.status == "completed"
    assert result.sha256
    assert result.size == len(b"hello")
    assert result.etag == "abc123"
    assert (tmp_path / "sample/file.pdf").read_bytes() == b"hello"

    await manager.close()
