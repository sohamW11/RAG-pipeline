"""SQLAlchemy ORM models -- the crawler's metadata registry.

Seven tables model the full lifecycle of a discovered legal document:

* ``categories``          -- what we crawl (registry of legal categories)
* ``documents``           -- the current metadata for each document
* ``document_versions``   -- immutable history; a new row per detected change
* ``crawl_history``       -- one row per listing-page crawl run
* ``download_history``    -- one row per download attempt
* ``crawler_jobs``        -- units of work (discover / crawl / download)
* ``scheduler_jobs``      -- declarative schedule of recurring jobs

The models are backend-agnostic (SQLite for local/tests, PostgreSQL in prod)
and only depend on generic SQLAlchemy types.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from crawler.utils.ids import new_uuid
from crawler.utils.time import utcnow


class Base(DeclarativeBase):
    """Declarative base for all crawler models."""


class TimestampMixin:
    """Adds created/updated timestamps with UTC defaults."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Category(Base, TimestampMixin):
    """A crawlable legal category (e.g. ``Circulars`` for SEBI)."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(64), unique=True, default=new_uuid, nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="sebi", nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    crawl_frequency: Mapped[str] = mapped_column(String(64), default="daily", nullable=False)
    last_crawl: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    documents: Mapped[list["Document"]] = relationship(
        back_populates="category", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("source", "name", name="uq_category_source_name"),
    )


class Document(Base, TimestampMixin):
    """Current metadata for a single legal document (no file content stored)."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(64), unique=True, default=new_uuid, nullable=False)
    category_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("categories.id"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    document_number: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    publication_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    department: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Text label of the category as printed on the source site. Distinct from
    # the ``category`` relationship, which points at the registry row.
    category_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    pdf_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    html_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    document_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    version: Mapped[Optional[str]] = mapped_column(String(64), default="1", nullable=True)
    # Fingerprint of the metadata record; drives change detection cheaply.
    content_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)

    category: Mapped[Optional[Category]] = relationship(back_populates="documents")
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentVersion.id"
    )

    __table_args__ = (
        Index("ix_documents_dedup", "document_number", "pdf_url"),
    )


class DocumentVersion(Base):
    """Immutable snapshot of a document created whenever a change is detected."""

    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(64), unique=True, default=new_uuid, nullable=False)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id"), nullable=False, index=True
    )
    version_number: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    sha256: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    etag: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_modified: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    publication_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    content_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    storage_key: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    storage_uri: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    document: Mapped[Document] = relationship(back_populates="versions")


class CrawlHistory(Base):
    """Audit record for a single listing-page crawl run."""

    __tablename__ = "crawl_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(64), unique=True, default=new_uuid, nullable=False)
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="running", nullable=False)
    documents_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    documents_new: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    documents_changed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    documents_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class DownloadHistory(Base):
    """Audit record for a single download attempt."""

    __tablename__ = "download_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(64), unique=True, default=new_uuid, nullable=False)
    document_id: Mapped[Optional[int]] = mapped_column(ForeignKey("documents.id"), nullable=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sha256: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    storage_uri: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class CrawlerJob(Base, TimestampMixin):
    """A unit of work processed by a worker (discover / crawl / download)."""

    __tablename__ = "crawler_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(64), unique=True, default=new_uuid, nullable=False)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), default="queued", nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class SchedulerJob(Base, TimestampMixin):
    """A declarative recurring job managed by the scheduler."""

    __tablename__ = "scheduler_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(64), unique=True, default=new_uuid, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    schedule: Mapped[str] = mapped_column(String(255), nullable=False)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_run: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "Base",
    "Category",
    "Document",
    "DocumentVersion",
    "CrawlHistory",
    "DownloadHistory",
    "CrawlerJob",
    "SchedulerJob",
]
