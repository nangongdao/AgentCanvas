"""Public contracts for supported application database backends."""

from __future__ import annotations

import pytest

from app.core.config import Settings, validate_runtime_settings
from app.db.base import create_engine


@pytest.mark.parametrize(
    ("database_url", "backend"),
    [
        ("sqlite+aiosqlite:///data/app.db", "sqlite"),
        (
            "postgresql+asyncpg://agentcanvas:secret@db.example.test/agentcanvas",
            "postgresql",
        ),
    ],
)
def test_supported_async_database_urls_are_explicit(database_url: str, backend: str) -> None:
    settings = Settings(database_url=database_url)

    validate_runtime_settings(settings)

    assert settings.database_backend == backend


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///data/app.db",
        "postgresql://agentcanvas:secret@db.example.test/agentcanvas",
        "postgresql+psycopg://agentcanvas:secret@db.example.test/agentcanvas",
        "mysql+aiomysql://agentcanvas:secret@db.example.test/agentcanvas",
    ],
)
def test_sync_or_unknown_database_urls_fail_before_startup(database_url: str) -> None:
    with pytest.raises(RuntimeError, match="DATABASE_URL must use"):
        validate_runtime_settings(Settings(database_url=database_url))


def test_postgresql_engine_uses_bounded_configured_pool() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://agentcanvas:secret@db.example.test/agentcanvas",
        database_pool_size=7,
        database_pool_timeout_seconds=11,
        database_pool_recycle_seconds=900,
    )

    engine = create_engine(settings)
    try:
        assert engine.pool.size() == 7  # type: ignore[attr-defined]
        assert engine.pool.timeout() == 11  # type: ignore[attr-defined]
    finally:
        engine.sync_engine.dispose()


def test_database_display_url_redacts_postgresql_password() -> None:
    settings = Settings(
        database_url=(
            "postgresql+asyncpg://agentcanvas:super-secret@db.example.test/agentcanvas"
        )
    )

    assert settings.database_display_url == (
        "postgresql+asyncpg://agentcanvas:***@db.example.test/agentcanvas"
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("database_pool_size", 0, "DATABASE_POOL_SIZE"),
        ("database_max_overflow", -1, "DATABASE_MAX_OVERFLOW"),
        ("database_pool_timeout_seconds", 0, "DATABASE_POOL_TIMEOUT_SECONDS"),
        ("database_pool_recycle_seconds", 0, "DATABASE_POOL_RECYCLE_SECONDS"),
        ("database_migration_lock_timeout_seconds", 0, "DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS"),
    ],
)
def test_database_pool_and_migration_limits_are_positive(
    field: str, value: int, message: str
) -> None:
    values = {field: value}
    with pytest.raises(RuntimeError, match=message):
        validate_runtime_settings(Settings(**values))  # type: ignore[arg-type]
