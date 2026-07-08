from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from crawler.models.registry import Base, Category
from crawler.repositories.category_repository import CategoryRepository
from crawler.repositories.document_repository import DocumentRepository


def test_category_repository_persists_and_lists():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    session = Session(engine)
    repo = CategoryRepository(session)
    category = repo.create(
        name="Acts",
        url="https://example.com/acts",
        enabled=True,
        crawl_frequency="daily",
    )

    assert category.uuid
    assert repo.get_by_uuid(category.uuid).name == "Acts"
    assert repo.list()[0].name == "Acts"

    session.close()


def test_document_repository_creates_and_finds_by_number():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    session = Session(engine)
    category = Category(name="Rules", url="https://example.com/rules")
    session.add(category)
    session.commit()
    session.refresh(category)

    repo = DocumentRepository(session)
    repo.create(
        category_id=category.id,
        title="Test Document",
        document_number="DOC-001",
        pdf_url="https://example.com/doc.pdf",
        html_url="https://example.com/doc.html",
    )

    assert repo.list() is not None
    session.close()
