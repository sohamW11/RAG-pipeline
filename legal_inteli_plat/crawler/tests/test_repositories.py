"""Repository tests.

Exercise the async repositories against an in-memory SQLite database using the
same ``AsyncSession`` machinery the service uses in production.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from crawler.models.registry import Base, Category
from crawler.repositories.category_repository import CategoryRepository
from crawler.repositories.document_repository import DocumentRepository


@pytest.fixture
async def session():
    """Provide a fresh in-memory async SQLite session per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_category_repository_upserts_and_lists(session):
    repo = CategoryRepository(session)

    category = await repo.upsert(
        source="sebi",
        name="Acts",
        url="https://example.com/acts",
        enabled=True,
        crawl_frequency="daily",
    )
    await session.commit()

    assert category.uuid
    fetched = await repo.get_by_uuid(category.uuid)
    assert fetched is not None and fetched.name == "Acts"

    # upsert is idempotent: same (source, name) updates rather than duplicating.
    again = await repo.upsert(source="sebi", name="Acts", url="https://example.com/acts-v2")
    await session.commit()
    assert again.id == category.id
    assert again.url == "https://example.com/acts-v2"

    enabled = await repo.list_enabled()
    assert [c.name for c in enabled] == ["Acts"]


@pytest.mark.asyncio
async def test_document_repository_creates_and_finds_by_number(session):
    category = Category(source="sebi", name="Rules", url="https://example.com/rules")
    session.add(category)
    await session.flush()

    repo = DocumentRepository(session)
    created = await repo.create(
        category_id=category.id,
        title="Test Document",
        document_number="DOC-001",
        pdf_url="https://example.com/doc.pdf",
        html_url="https://example.com/doc.html",
    )
    await session.commit()

    assert created.uuid
    found = await repo.get_by_document_number("DOC-001")
    assert found is not None and found.title == "Test Document"

    existing = await repo.find_existing(document_number="DOC-001", pdf_url=None)
    assert existing is not None and existing.id == created.id

    listed = await repo.list_by_category(category.id)
    assert [d.document_number for d in listed] == ["DOC-001"]
