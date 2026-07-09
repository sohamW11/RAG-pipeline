"""Storage backend tests (LocalStorage against the StorageInterface contract)."""

import pytest

from crawler.storage import LocalStorage


async def test_local_storage_round_trips_bytes(tmp_path):
    storage = LocalStorage(base_path=tmp_path)

    stored = await storage.save_bytes("docs/a.pdf", b"payload", content_type="application/pdf")

    assert stored.key == "docs/a.pdf"
    assert stored.size == len(b"payload")
    assert await storage.exists("docs/a.pdf")
    assert await storage.read_bytes("docs/a.pdf") == b"payload"


async def test_local_storage_delete_is_idempotent(tmp_path):
    storage = LocalStorage(base_path=tmp_path)
    await storage.save_bytes("docs/a.pdf", b"x")

    await storage.delete("docs/a.pdf")
    assert not await storage.exists("docs/a.pdf")
    # Deleting a missing key is a no-op, not an error.
    await storage.delete("docs/a.pdf")


async def test_local_storage_rejects_path_traversal(tmp_path):
    storage = LocalStorage(base_path=tmp_path)
    with pytest.raises(ValueError):
        await storage.save_bytes("../escape.pdf", b"nope")


async def test_local_storage_streams(tmp_path):
    storage = LocalStorage(base_path=tmp_path)

    async def chunks():
        for part in (b"foo", b"bar", b"baz"):
            yield part

    stored = await storage.save_stream("docs/stream.bin", chunks())

    assert stored.size == len(b"foobarbaz")
    assert await storage.read_bytes("docs/stream.bin") == b"foobarbaz"
