"""Async database engine and session management.

Uses SQLAlchemy 2.0's async engine. The default URL is ``sqlite+aiosqlite``
so the service (and the test suite) runs with no external database; production
overrides it with ``postgresql+asyncpg://`` via configuration.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from crawler.config.settings import CrawlerSettings, get_settings
from crawler.models.registry import Base


class Database:
    """Owns the async engine and session factory for one settings instance."""

    def __init__(self, settings: CrawlerSettings | None = None) -> None:
        self._settings = settings or get_settings()
        # SQLite does not accept pool sizing kwargs; branch accordingly.
        url = self._settings.database.url
        kwargs: dict[str, object] = {"echo": self._settings.database.echo, "future": True}
        if not url.startswith("sqlite"):
            kwargs.update(
                pool_size=self._settings.database.pool_size,
                max_overflow=self._settings.database.max_overflow,
                pool_pre_ping=True,
            )
        self._engine: AsyncEngine = create_async_engine(url, **kwargs)
        self._sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine, expire_on_commit=False, autoflush=False
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        return self._sessionmaker

    async def create_all(self) -> None:
        """Create all tables (development / test convenience; prod uses Alembic)."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_all(self) -> None:
        """Drop all tables (test teardown)."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def dispose(self) -> None:
        """Dispose the engine's connection pool on shutdown."""
        await self._engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session inside a transaction, committing/rolling back for you."""
        async with self._sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


_DATABASE: Database | None = None


def get_database() -> Database:
    """Return a process-wide cached :class:`Database` (dependency-injection root)."""
    global _DATABASE
    if _DATABASE is None:
        _DATABASE = Database()
    return _DATABASE
