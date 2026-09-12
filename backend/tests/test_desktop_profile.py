"""C9 §5.1: the desktop profile freezes the single-machine degradation set.

With ``APP_PROFILE=desktop`` the backend pins SQLite, the ``all`` process
role, no Redis, a loopback bind, and the SQL vector backend (the bundle
excludes the chromadb dependency tree), so the Rust shell only passes a
dynamic port and a data directory. The Tauri webview origins join the CORS
allow-list for the direct SSE connections (ADR 0003 / C9 §3.2 verdict B').
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings, load_settings, validate_runtime_settings


def test_desktop_profile_forces_single_machine_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", "desktop")
    monkeypatch.setenv("APP_HOST", "0.0.0.0")
    monkeypatch.setenv("APP_PROCESS_ROLE", "api")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("VECTOR_BACKEND", "chroma")

    settings = load_settings()

    assert settings.app_profile == "desktop"
    assert settings.app_host == "127.0.0.1"
    assert settings.process_role == "all"
    assert settings.redis_url == ""
    assert settings.vector_backend == "sql"
    assert "tauri://localhost" in settings.cors_origins
    assert "http://tauri.localhost" in settings.cors_origins
    validate_runtime_settings(settings)


def test_desktop_profile_keeps_port_and_data_dir_injection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("APP_PROFILE", "desktop")
    monkeypatch.setenv("APP_PORT", "48213")
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "desktop-data"))

    settings = load_settings()

    assert settings.app_port == 48213
    assert settings.data_dir == tmp_path / "desktop-data"
    validate_runtime_settings(settings)


def test_desktop_profile_rejects_postgresql(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", "desktop")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://agentcanvas:secret@127.0.0.1:5432/agentcanvas",
    )

    settings = load_settings()

    with pytest.raises(RuntimeError, match="desktop profile requires sqlite"):
        validate_runtime_settings(settings)


def test_desktop_profile_rejects_external_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", "desktop")
    monkeypatch.setenv("APP_HOST", "127.0.0.1")

    settings = load_settings()
    # load_settings drops REDIS_URL in the desktop profile; construct the
    # object directly to prove the validator also fails closed if some future
    # code path reintroduces one.
    violating = Settings(redis_url="redis://127.0.0.1:6379/0", app_profile="desktop")
    with pytest.raises(RuntimeError, match="desktop profile must not set REDIS_URL"):
        validate_runtime_settings(violating)
    validate_runtime_settings(settings)


def test_unknown_profile_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", "server")

    with pytest.raises(RuntimeError, match="APP_PROFILE must be standard or desktop"):
        load_settings()


def test_standard_profile_stays_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PROFILE", "standard")

    settings = load_settings()

    assert settings.app_profile == "standard"
    assert "http://tauri.localhost" not in settings.cors_origins
    assert "tauri://localhost" not in settings.cors_origins
    validate_runtime_settings(settings)
