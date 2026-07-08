"""MinIO storage backend.

MinIO speaks the S3 API, so this backend is a thin specialisation of
:class:`S3CompatibleStorage` that only differs in the URI scheme it reports and
the fact that it expects a custom ``endpoint_url``.
"""

from __future__ import annotations

from crawler.config.settings import S3Config
from crawler.storage.s3 import S3CompatibleStorage


class MinIOStorage(S3CompatibleStorage):
    """S3-compatible storage pointed at a self-hosted MinIO endpoint."""

    scheme = "minio"

    def __init__(self, config: S3Config) -> None:
        if not config.endpoint_url:
            raise ValueError("MinIO storage requires 'endpoint_url' to be configured.")
        super().__init__(config)
