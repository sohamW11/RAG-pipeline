"""S3-compatible object storage backend (AWS S3).

``aioboto3`` is an optional dependency; it is imported lazily so the service
runs without it when a different backend is configured. MinIO reuses the same
S3 API and subclasses this backend (see :mod:`crawler.storage.minio`).
"""

from __future__ import annotations

from typing import AsyncIterator

from crawler.config.settings import S3Config
from crawler.interfaces.storage import StorageInterface, StoredObject


class S3CompatibleStorage(StorageInterface):
    """Shared implementation for any S3-API object store (AWS S3, MinIO, ...)."""

    scheme = "s3"

    def __init__(self, config: S3Config) -> None:
        self._config = config
        try:  # Lazy optional dependency.
            import aioboto3  # noqa: F401
        except ImportError as exc:  # pragma: no cover - exercised only in prod
            raise RuntimeError(
                "aioboto3 is required for S3/MinIO storage. "
                "Install with `pip install aioboto3`."
            ) from exc
        import aioboto3

        self._session = aioboto3.Session()

    def _key(self, key: str) -> str:
        prefix = self._config.prefix.strip("/")
        return f"{prefix}/{key}" if prefix else key

    def _client(self):
        return self._session.client(
            "s3",
            region_name=self._config.region,
            endpoint_url=self._config.endpoint_url,
            aws_access_key_id=self._config.access_key,
            aws_secret_access_key=self._config.secret_key,
            use_ssl=self._config.secure,
        )

    def _uri(self, full_key: str) -> str:
        return f"{self.scheme}://{self._config.bucket}/{full_key}"

    async def save_bytes(self, key: str, data: bytes, content_type: str | None = None) -> StoredObject:
        full_key = self._key(key)
        extra = {"ContentType": content_type} if content_type else {}
        async with self._client() as client:
            await client.put_object(Bucket=self._config.bucket, Key=full_key, Body=data, **extra)
        return StoredObject(key=full_key, uri=self._uri(full_key), size=len(data))

    async def save_stream(
        self, key: str, stream: AsyncIterator[bytes], content_type: str | None = None
    ) -> StoredObject:
        # Buffer to bytes then upload; multipart streaming is a future optimisation.
        chunks = [chunk async for chunk in stream]
        return await self.save_bytes(key, b"".join(chunks), content_type)

    async def read_bytes(self, key: str) -> bytes:
        full_key = self._key(key)
        async with self._client() as client:
            response = await client.get_object(Bucket=self._config.bucket, Key=full_key)
            async with response["Body"] as body:
                return await body.read()

    async def exists(self, key: str) -> bool:
        full_key = self._key(key)
        async with self._client() as client:
            try:
                await client.head_object(Bucket=self._config.bucket, Key=full_key)
                return True
            except Exception:
                return False

    async def delete(self, key: str) -> None:
        full_key = self._key(key)
        async with self._client() as client:
            await client.delete_object(Bucket=self._config.bucket, Key=full_key)


class S3Storage(S3CompatibleStorage):
    """AWS S3 storage backend."""

    scheme = "s3"
