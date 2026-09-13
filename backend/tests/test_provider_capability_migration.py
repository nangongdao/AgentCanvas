"""Migration contract for model-level Provider capability overrides."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from app.core.config import BACKEND_DIR, Settings
from app.db.base import create_engine
from app.db.migrations import CURRENT_REVISION, upgrade_database


def _config(settings: Settings) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.effective_database_url)
    return config


async def _columns(settings: Settings) -> set[str]:
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_connection: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns("model_configs")
                }
            )
    finally:
        await engine.dispose()


async def test_capability_migration_backfills_downgrades_and_reapplies(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    await asyncio.to_thread(command.upgrade, _config(settings), "0019_project_quotas")
    engine = create_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO model_configs (
                        id, name, provider, model_name, base_url,
                        api_key_encrypted, params_json, kind, is_default,
                        created_at, prompt_price_per_million_usd,
                        completion_price_per_million_usd, pricing_version
                    ) VALUES (
                        'legacy-model', 'Legacy', 'openai_compat', 'legacy', NULL,
                        NULL, '{}', 'chat', 0, '2026-08-07 00:00:00',
                        NULL, NULL, NULL
                    )
                    """
                )
            )
    finally:
        await engine.dispose()

    await upgrade_database(settings)
    assert CURRENT_REVISION == "2a2231226aa0"
    assert "capabilities_json" in await _columns(settings)
    engine = create_engine(settings)
    try:
        async with engine.begin() as connection:
            raw = await connection.scalar(
                text("SELECT capabilities_json FROM model_configs WHERE id = 'legacy-model'")
            )
            assert json.loads(str(raw)) == {}
            await connection.execute(
                text(
                    "UPDATE model_configs SET capabilities_json = "
                    "'{\"tools\": false}' WHERE id = 'legacy-model'"
                )
            )
    finally:
        await engine.dispose()

    await asyncio.to_thread(command.downgrade, _config(settings), "0019_project_quotas")
    assert "capabilities_json" not in await _columns(settings)
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT COUNT(*) FROM model_configs WHERE id = 'legacy-model'")
                )
                == 1
            )
    finally:
        await engine.dispose()

    await upgrade_database(settings)
    assert "capabilities_json" in await _columns(settings)
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            raw = await connection.scalar(
                text("SELECT capabilities_json FROM model_configs WHERE id = 'legacy-model'")
            )
            assert json.loads(str(raw)) == {}
    finally:
        await engine.dispose()
