"""Checkpointer fail-mode policy tests (R-05 / U1-3).

The checkpointer is the durability anchor for human-approval pause/resume and
execution replay. Its failure policy is environment-dependent:

* production  -> fail-closed: a broken SQLite saver raises
  ``CheckpointerUnavailable`` instead of silently degrading to MemorySaver;
* dev/test    -> graceful: falls back to MemorySaver and logs loudly, so
  local iteration never blocks on a missing driver.

``checkpointer_status()`` mirrors whatever state was reached so the
``/readyz`` probe can surface it.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.engine import checkpoint as checkpoint_mod
from app.engine.checkpoint import (
    CheckpointerUnavailable,
    checkpointer_status,
    close_checkpointer,
    make_checkpointer,
)


@pytest.fixture
async def reset_checkpointer():
    await close_checkpointer()
    yield
    await close_checkpointer()


def _settings(tmp_path, *, production: bool) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="production" if production else "test",
    )


def _boom(_settings: Settings) -> None:
    raise OSError("sqlite driver unavailable")


async def test_production_builds_durable_saver(reset_checkpointer, tmp_path) -> None:
    settings = _settings(tmp_path, production=True)
    saver = await make_checkpointer(settings)
    assert saver is not None
    status = checkpointer_status()
    assert status["state"] == "ready"
    assert status["backend"] == "sqlite"


async def test_production_postgres_failure_is_redacted_and_fail_closed(
    reset_checkpointer, tmp_path
) -> None:
    secret = "checkpoint-password-must-not-leak"
    settings = Settings(
        data_dir=tmp_path,
        environment="production",
        database_url=(
            f"postgresql+asyncpg://agentcanvas:{secret}@127.0.0.1:1/agentcanvas?connect_timeout=1"
        ),
        database_pool_timeout_seconds=1,
    )

    with pytest.raises(CheckpointerUnavailable) as exc_info:
        await make_checkpointer(settings)

    status = checkpointer_status()
    assert status["state"] == "unavailable"
    assert status["backend"] == "postgresql"
    assert "postgres" in str(exc_info.value).lower()
    assert secret not in str(exc_info.value)
    assert secret not in status["error"]


async def test_production_fail_closed_no_memory_fallback(
    reset_checkpointer, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(checkpoint_mod, "_try_build_sqlite_saver", _boom)
    settings = _settings(tmp_path, production=True)

    with pytest.raises(CheckpointerUnavailable):
        await make_checkpointer(settings)

    status = checkpointer_status()
    assert status["state"] == "unavailable"
    assert status["backend"] == "sqlite"
    assert "sqlite" in status["error"]


async def test_development_falls_back_to_memory(reset_checkpointer, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(checkpoint_mod, "_try_build_sqlite_saver", _boom)
    settings = _settings(tmp_path, production=False)

    saver = await make_checkpointer(settings)

    # Fallback happened, saver is usable, and the degradation is observable.
    assert saver is not None
    status = checkpointer_status()
    assert status["state"] == "degraded"
    assert status["backend"] == "memory"


async def test_close_resets_status(reset_checkpointer, tmp_path) -> None:
    settings = _settings(tmp_path, production=False)
    await make_checkpointer(settings)
    await close_checkpointer()
    # After close, not-built is reported as unavailable (fail-closed default).
    assert checkpointer_status()["state"] == "unavailable"
