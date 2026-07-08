"""Repository for the ``document_versions`` table."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import desc, select

from crawler.models.registry import DocumentVersion
from crawler.repositories.base import AsyncRepository


class DocumentVersionRepository(AsyncRepository[DocumentVersion]):
    """Persistence for immutable document version snapshots."""

    model = DocumentVersion

    async def latest_for_document(self, document_id: int) -> Optional[DocumentVersion]:
        """Return the most recent version for a document, if any."""
        result = await self.session.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(desc(DocumentVersion.id))
            .limit(1)
        )
        return result.scalars().first()

    async def list_for_document(self, document_id: int) -> Sequence[DocumentVersion]:
        """Return the full version history for a document, oldest first."""
        result = await self.session.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.id)
        )
        return result.scalars().all()

    async def create(
        self,
        *,
        document_id: int,
        version_number: str,
        url: str,
        sha256: Optional[str] = None,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        publication_date: Optional[datetime] = None,
        content_type: Optional[str] = None,
        file_size: Optional[int] = None,
        storage_key: Optional[str] = None,
        storage_uri: Optional[str] = None,
    ) -> DocumentVersion:
        """Create a new version snapshot."""
        version = DocumentVersion(
            document_id=document_id,
            version_number=version_number,
            url=url,
            sha256=sha256,
            etag=etag,
            last_modified=last_modified,
            publication_date=publication_date,
            content_type=content_type,
            file_size=file_size,
            storage_key=storage_key,
            storage_uri=storage_uri,
        )
        return await self.add(version)
