"""Repository for the ``categories`` table (the category registry)."""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select

from crawler.models.registry import Category
from crawler.repositories.base import AsyncRepository
from crawler.utils.time import utcnow


class CategoryRepository(AsyncRepository[Category]):
    """Persistence for legal categories."""

    model = Category

    async def get_by_name(self, source: str, name: str) -> Optional[Category]:
        """Look up a category by its (source, name) natural key."""
        result = await self.session.execute(
            select(Category).where(Category.source == source, Category.name == name)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        source: str,
        name: str,
        url: str,
        enabled: bool = True,
        crawl_frequency: str = "daily",
    ) -> Category:
        """Insert a category or update its URL/flags if it already exists.

        Idempotent so repeated discovery runs converge rather than duplicate.
        """
        existing = await self.get_by_name(source, name)
        if existing is None:
            category = Category(
                source=source,
                name=name,
                url=url,
                enabled=enabled,
                crawl_frequency=crawl_frequency,
            )
            return await self.add(category)

        existing.url = url
        existing.enabled = enabled
        existing.crawl_frequency = crawl_frequency
        await self.session.flush()
        return existing

    async def list_enabled(self) -> Sequence[Category]:
        """Return all categories that are enabled for crawling."""
        result = await self.session.execute(
            select(Category).where(Category.enabled.is_(True)).order_by(Category.name)
        )
        return result.scalars().all()

    async def mark_crawled(self, category: Category) -> None:
        """Stamp ``last_crawl`` after a successful crawl."""
        category.last_crawl = utcnow()
        await self.session.flush()
