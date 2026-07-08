"""Storage backend factory.

Turns configuration into a concrete :class:`StorageInterface` so callers can
depend on the abstraction and let configuration decide the implementation.
"""

from __future__ import annotations

from crawler.config.settings import CrawlerSettings, StorageConfig
from crawler.interfaces.storage import StorageInterface
from crawler.storage.local import LocalStorage


def create_storage(settings: CrawlerSettings | StorageConfig) -> StorageInterface:
    """Build the storage backend named by configuration.

    Args:
        settings: Either the whole :class:`CrawlerSettings` or a
            :class:`StorageConfig` section.

    Returns:
        A ready-to-use storage backend implementing :class:`StorageInterface`.

    Raises:
        ValueError: If the configured backend is unknown.
    """
    config = settings.storage if isinstance(settings, CrawlerSettings) else settings
    backend = config.backend.lower()

    if backend == "local":
        return LocalStorage(config.local_path)
    if backend == "s3":
        from crawler.storage.s3 import S3Storage

        return S3Storage(config.s3)
    if backend == "minio":
        from crawler.storage.minio import MinIOStorage

        return MinIOStorage(config.minio)

    raise ValueError(f"Unknown storage backend: {config.backend!r}")
