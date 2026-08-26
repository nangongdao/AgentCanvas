"""Tests for the P5 memory store (SQLite backend + builder fallback)."""

from __future__ import annotations

from app.core.config import Settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.memory import (
    BaseMemoryStore,
    NullMemoryStore,
    SqliteMemoryStore,
    build_memory_store,
)


async def test_sqlite_memory_store_roundtrip(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        store = SqliteMemoryStore(session_factory)
        assert store.backend_name == "sqlite"
        assert await store.load("s1", window=10) == []

        await store.append("s1", "user", "hello")
        await store.append("s1", "assistant", "hi there")
        await store.append("s2", "user", "other session")

        entries = await store.load("s1", window=10)
        assert [(e.role, e.content) for e in entries] == [
            ("user", "hello"),
            ("assistant", "hi there"),
        ]

        windowed = await store.load("s1", window=1)
        assert [(e.role, e.content) for e in windowed] == [("assistant", "hi there")]

        await store.reset("s1")
        assert await store.load("s1", window=10) == []
        assert await store.load("s2", window=10)  # untouched
    finally:
        await engine.dispose()


async def test_build_memory_store_without_redis_uses_sqlite(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, redis_url="")
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        store = await build_memory_store(settings, session_factory)
        assert isinstance(store, SqliteMemoryStore)
        assert isinstance(store, BaseMemoryStore)
    finally:
        await engine.dispose()


async def test_build_memory_store_redis_unreachable_falls_back(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, redis_url="redis://127.0.0.1:1/0")
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        store = await build_memory_store(settings, session_factory)
        assert isinstance(store, SqliteMemoryStore)
    finally:
        await engine.dispose()


async def test_null_memory_store_is_noop() -> None:
    store = NullMemoryStore()
    assert store.backend_name == "none"
    assert await store.load("any") == []
    await store.append("any", "user", "x")  # must not raise
    await store.reset("any")
