"""Generic async repository.

Implements the Repository pattern: persistence details live behind a narrow,
typed interface so the service layer works with domain objects and never writes
raw SQL. Concrete repositories subclass :class:`AsyncRepository` and set
``model``.
"""

from __future__ import annotations

from typing import Generic, Optional, Sequence, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.models.registry import Base

ModelT = TypeVar("ModelT", bound=Base)


class AsyncRepository(Generic[ModelT]):
    """Base class providing CRUD operations shared by all repositories."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, instance: ModelT) -> ModelT:
        """Persist a new instance (flushes to populate defaults/PKs)."""
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def get(self, id_: int) -> Optional[ModelT]:
        """Return a row by primary key, or ``None``."""
        return await self.session.get(self.model, id_)

    async def get_by_uuid(self, uuid: str) -> Optional[ModelT]:
        """Return a row by its public ``uuid`` column, or ``None``."""
        result = await self.session.execute(
            select(self.model).where(self.model.uuid == uuid)  # type: ignore[attr-defined]
        )
        return result.scalar_one_or_none()

    async def list(self, *, limit: int = 100, offset: int = 0) -> Sequence[ModelT]:
        """Return rows ordered by newest id first, paginated."""
        result = await self.session.execute(
            select(self.model).order_by(self.model.id.desc()).limit(limit).offset(offset)  # type: ignore[attr-defined]
        )
        return result.scalars().all()

    async def count(self) -> int:
        """Return the total number of rows."""
        result = await self.session.execute(select(func.count()).select_from(self.model))
        return int(result.scalar_one())

    async def delete(self, instance: ModelT) -> None:
        """Delete a persisted instance."""
        await self.session.delete(instance)
        await self.session.flush()
