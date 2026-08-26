"""Async SQLAlchemy engine / session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Lock
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _sqlite_connection_pragmas(settings: Settings):
    journal_lock = Lock()
    journal_configured = False

    def configure(dbapi_connection: Any, _connection_record: Any) -> None:
        nonlocal journal_configured
        cursor = dbapi_connection.cursor()
        try:
            with journal_lock:
                if not journal_configured:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    journal_configured = True
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={settings.sqlite_busy_timeout_ms}")
            cursor.execute(f"PRAGMA synchronous={settings.sqlite_synchronous}")
        finally:
            cursor.close()

    return configure


def create_engine(settings: Settings) -> AsyncEngine:
    url = settings.effective_database_url
    kwargs: dict[str, Any] = {"echo": False}
    if settings.database_backend == "sqlite":
        kwargs["connect_args"] = {
            "check_same_thread": False,
            "timeout": settings.sqlite_busy_timeout_ms / 1000,
        }
    else:
        kwargs.update(
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            pool_recycle=settings.database_pool_recycle_seconds,
            pool_pre_ping=True,
        )
    engine = create_async_engine(url, **kwargs)
    if settings.database_backend == "sqlite":
        event.listen(engine.sync_engine, "connect", _sqlite_connection_pragmas(settings))
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_db(engine: AsyncEngine) -> None:
    """Open one connection so runtime connection hooks fail fast at startup."""
    async with engine.begin() as conn:
        await conn.exec_driver_sql("SELECT 1")
