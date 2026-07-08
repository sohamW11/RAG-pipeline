import asyncio

from crawler.download.manager import DownloadManager
from crawler.interfaces.storage import LocalStorage


class FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    async def aread(self):
        return self._data


class FakeSession:
    def __init__(self, data: bytes):
        self.data = data

    def stream(self, method, url):
        return FakeResponse(self.data)

    async def aclose(self):
        return None


def test_download_manager_writes_bytes(tmp_path):
    storage = LocalStorage(base_path=tmp_path)
    manager = DownloadManager(storage=storage)
    manager._session = FakeSession(b"hello")

    result = asyncio.run(manager.download("https://example.com/file.pdf", "sample/file.pdf"))

    assert result.status == "completed"
    assert result.sha256
    assert (tmp_path / "sample/file.pdf").exists()

    asyncio.run(manager.close())
