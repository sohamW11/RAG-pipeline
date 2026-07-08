"""Repository for the ``documents`` table (metadata registry)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import or_, select

from crawler.models.registry import Document
from crawler.repositories.base import AsyncRepository


class DocumentRepository(AsyncRepository[Document]):
    """Persistence for document metadata."""

    model = Document

    async def create(
        self,
        *,
        category_id: Optional[int] = None,
        title: str,
        document_number: Optional[str] = None,
        publication_date: Optional[datetime] = None,
        effective_date: Optional[datetime] = None,
        department: Optional[str] = None,
        category_name: Optional[str] = None,
        pdf_url: Optional[str] = None,
        html_url: Optional[str] = None,
        source_url: Optional[str] = None,
        language: Optional[str] = None,
        document_type: Optional[str] = None,
        version: Optional[str] = "1",
        content_hash: Optional[str] = None,
    ) -> Document:
        """Insert a new document metadata row."""
        document = Document(
            category_id=category_id,
            title=title,
            document_number=document_number,
            publication_date=publication_date,
            effective_date=effective_date,
            department=department,
            category_name=category_name,
            pdf_url=pdf_url,
            html_url=html_url,
            source_url=source_url,
            language=language,
            document_type=document_type,
            version=version,
            content_hash=content_hash,
        )
        return await self.add(document)

    async def get_by_document_number(self, document_number: str) -> Optional[Document]:
        """Find a document by its regulator-issued number."""
        result = await self.session.execute(
            select(Document).where(Document.document_number == document_number)
        )
        return result.scalar_one_or_none()

    async def find_existing(
        self, *, document_number: Optional[str], pdf_url: Optional[str]
    ) -> Optional[Document]:
        """Find a pre-existing document by number or PDF URL (dedup key)."""
        clauses = []
        if document_number:
            clauses.append(Document.document_number == document_number)
        if pdf_url:
            clauses.append(Document.pdf_url == pdf_url)
        if not clauses:
            return None
        result = await self.session.execute(select(Document).where(or_(*clauses)).limit(1))
        return result.scalars().first()

    async def list_by_category(self, category_id: int, *, limit: int = 100) -> Sequence[Document]:
        """List documents belonging to a category."""
        result = await self.session.execute(
            select(Document)
            .where(Document.category_id == category_id)
            .order_by(Document.id.desc())
            .limit(limit)
        )
        return result.scalars().all()

    def apply_metadata(self, document: Document, fields: dict[str, Any]) -> None:
        """Copy provided metadata fields onto an existing document instance."""
        for key, value in fields.items():
            if hasattr(document, key) and value is not None:
                setattr(document, key, value)
