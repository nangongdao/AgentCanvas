"""Migration contract for project ownership and quota accounting."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.core.config import BACKEND_DIR, Settings
from app.db.base import create_engine
from app.db.migrations import CURRENT_REVISION, upgrade_database

QUOTA_TABLES = {
    "project_quotas",
    "project_quota_counters",
    "project_quota_period_usage",
    "project_quota_reservations",
}


def _config(settings: Settings) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.effective_database_url)
    return config


async def _schema(settings: Settings) -> tuple[str | None, set[str], set[str], set[str]]:
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:

            def inspect_schema(sync_connection):
                inspector = inspect(sync_connection)
                tables = set(inspector.get_table_names())
                kb_columns = {column["name"] for column in inspector.get_columns("knowledge_bases")}
                mcp_columns = {column["name"] for column in inspector.get_columns("mcp_servers")}
                return tables, kb_columns, mcp_columns

            tables, kb_columns, mcp_columns = await connection.run_sync(inspect_schema)
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            return str(version), tables, kb_columns, mcp_columns
    finally:
        await engine.dispose()


async def _seed_revision_0018(settings: Settings) -> None:
    await asyncio.to_thread(command.upgrade, _config(settings), "0018_audit_logs")
    engine = create_engine(settings)
    timestamp = "2026-08-07 00:00:00"
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                "INSERT INTO organizations VALUES (?, ?, ?, ?, ?)",
                ("org", "Organization", "organization", timestamp, timestamp),
            )
            await connection.exec_driver_sql(
                "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?)",
                ("project", "org", "Project", "project", timestamp, timestamp),
            )
            await connection.exec_driver_sql(
                """
                INSERT INTO knowledge_bases (
                    id, name, description, embedding_model_id, chunk_size,
                    chunk_overlap, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-kb",
                    "Legacy KB",
                    "",
                    "default-embedding",
                    1000,
                    150,
                    timestamp,
                    timestamp,
                ),
            )
            await connection.exec_driver_sql(
                """
                INSERT INTO mcp_servers (
                    id, name, transport, command, args_json, env_json, url,
                    headers_json, enabled, tools_cache_json, tools_cached_at,
                    last_status, created_at, env_encrypted, headers_encrypted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-mcp",
                    "Legacy MCP",
                    "stdio",
                    "python",
                    "[]",
                    "{}",
                    None,
                    "{}",
                    1,
                    "[]",
                    None,
                    None,
                    timestamp,
                    None,
                    None,
                ),
            )
    finally:
        await engine.dispose()


async def _assert_integrity_error(settings: Settings, statement: str) -> None:
    engine = create_engine(settings)
    try:
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(text(statement))
    finally:
        await engine.dispose()


async def test_project_quota_migration_backfills_downgrades_and_reapplies(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    await _seed_revision_0018(settings)
    await upgrade_database(settings)

    version, tables, kb_columns, mcp_columns = await _schema(settings)
    assert version == CURRENT_REVISION
    assert tables >= QUOTA_TABLES
    assert "project_id" in kb_columns
    assert "project_id" in mcp_columns

    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            quota = (
                await connection.execute(
                    text(
                        "SELECT concurrent_execution_limit, storage_bytes_limit, "
                        "monthly_embedding_input_bytes_limit, "
                        "monthly_model_cost_units_limit, stdio_mcp_process_limit "
                        "FROM project_quotas WHERE project_id = 'project'"
                    )
                )
            ).one()
            counter = (
                await connection.execute(
                    text(
                        "SELECT concurrent_executions, storage_bytes, stdio_mcp_processes "
                        "FROM project_quota_counters WHERE project_id = 'project'"
                    )
                )
            ).one()
            assert tuple(quota) == (None, None, None, None, None)
            assert tuple(counter) == (0, 0, 0)
            assert (
                await connection.scalar(
                    text("SELECT project_id FROM knowledge_bases WHERE id = 'legacy-kb'")
                )
                is None
            )
            assert (
                await connection.scalar(
                    text("SELECT project_id FROM mcp_servers WHERE id = 'legacy-mcp'")
                )
                is None
            )
    finally:
        await engine.dispose()

    await _assert_integrity_error(
        settings,
        "UPDATE project_quotas SET storage_bytes_limit = -1 WHERE project_id = 'project'",
    )
    await _assert_integrity_error(
        settings,
        "UPDATE project_quota_counters SET concurrent_executions = -1 WHERE project_id = 'project'",
    )
    await _assert_integrity_error(
        settings,
        "INSERT INTO project_quota_period_usage VALUES "
        "('project', '2026-08-01', -1, 0, CURRENT_TIMESTAMP)",
    )
    await _assert_integrity_error(
        settings,
        "INSERT INTO project_quota_reservations VALUES "
        "('bad', 'project', 'execution', 'resource', -1, CURRENT_TIMESTAMP)",
    )

    await asyncio.to_thread(command.downgrade, _config(settings), "0018_audit_logs")
    version, tables, kb_columns, mcp_columns = await _schema(settings)
    assert version == "0018_audit_logs"
    assert not (QUOTA_TABLES & tables)
    assert "project_id" not in kb_columns
    assert "project_id" not in mcp_columns

    await upgrade_database(settings)
    version, tables, _kb_columns, _mcp_columns = await _schema(settings)
    assert version == CURRENT_REVISION
    assert tables >= QUOTA_TABLES
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT COUNT(*) FROM knowledge_bases WHERE id = 'legacy-kb'")
                )
                == 1
            )
            assert (
                await connection.scalar(
                    text("SELECT COUNT(*) FROM mcp_servers WHERE id = 'legacy-mcp'")
                )
                == 1
            )
    finally:
        await engine.dispose()
