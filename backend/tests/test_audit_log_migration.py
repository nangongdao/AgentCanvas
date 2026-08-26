"""Migration contract for append-only administrative audit history."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic.config import Config
from sqlalchemy import inspect

from alembic import command
from app.core.config import BACKEND_DIR, Settings
from app.db.base import create_engine
from app.db.migrations import CURRENT_REVISION, upgrade_database


def _config(settings: Settings) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.effective_database_url)
    return config


async def _audit_schema(settings: Settings) -> tuple[str | None, set[str], set[str]]:
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            def inspect_schema(sync_connection):
                inspector = inspect(sync_connection)
                tables = set(inspector.get_table_names())
                indexes = {
                    str(index["name"])
                    for index in inspector.get_indexes("audit_logs")
                } if "audit_logs" in tables else set()
                return tables, indexes

            tables, indexes = await connection.run_sync(inspect_schema)
            version = None
            if "alembic_version" in tables:
                result = await connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                )
                version = result.scalar_one()
            return version, tables, indexes
    finally:
        await engine.dispose()


async def test_audit_migration_downgrades_and_reapplies(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)

    version, tables, indexes = await _audit_schema(settings)
    assert version == CURRENT_REVISION
    assert "audit_logs" in tables
    assert {
        "ix_audit_logs_created_id",
        "ix_audit_logs_organization_created",
        "ix_audit_logs_project_created",
        "ix_audit_logs_actor_created",
        "ix_audit_logs_action_created",
        "ix_audit_logs_resource_created",
    } <= indexes

    await asyncio.to_thread(command.downgrade, _config(settings), "0017_workflow_reviews")
    version, tables, _indexes = await _audit_schema(settings)
    assert version == "0017_workflow_reviews"
    assert "audit_logs" not in tables

    await upgrade_database(settings)
    version, tables, _indexes = await _audit_schema(settings)
    assert version == CURRENT_REVISION
    assert "audit_logs" in tables
