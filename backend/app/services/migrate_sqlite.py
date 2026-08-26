"""Copy an offline v0.x SQLite relational database into empty PostgreSQL."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Integer, Table, func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

import app.db.models  # noqa: F401 - populate declarative metadata
from app.core.config import Settings, get_settings
from app.db.base import Base, create_engine
from app.db.migrations import CURRENT_REVISION

_VECTOR_TABLE = "document_chunks"
_SELF_PARENT_COLUMNS = {
    "executions": "parent_execution_id",
    "refresh_tokens": "replaced_by_id",
    "workflow_comments": "parent_comment_id",
}
_BATCH_SIZE = 500


@dataclass(frozen=True)
class RelationalMigrationResult:
    table_counts: dict[str, int]

    @property
    def tables(self) -> int:
        return len(self.table_counts)

    @property
    def rows(self) -> int:
        return sum(self.table_counts.values())


async def _revision(engine: AsyncEngine, label: str) -> str:
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    except Exception as exc:  # noqa: BLE001 - normalize an invalid migration source
        raise RuntimeError(f"{label} database has no readable Alembic revision") from exc
    revision = str(value or "")
    if revision != CURRENT_REVISION:
        raise RuntimeError(
            f"{label} database must be at {CURRENT_REVISION}; found {revision or 'none'}"
        )
    return revision


def _ordered_rows(table: Table, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parent_column = _SELF_PARENT_COLUMNS.get(table.name)
    if parent_column is None or not rows:
        return rows

    pending = {str(row["id"]): row for row in rows}
    if len(pending) != len(rows):
        raise RuntimeError(f"duplicate primary key while ordering {table.name}")
    emitted: set[str] = set()
    ordered: list[dict[str, Any]] = []
    while pending:
        ready = [
            row
            for row in pending.values()
            if row.get(parent_column) is None or str(row[parent_column]) in emitted
        ]
        if not ready:
            unresolved = ", ".join(sorted(pending))
            raise RuntimeError(
                f"{table.name} has a missing or cyclic {parent_column}: {unresolved}"
            )
        for row in ready:
            key = str(row["id"])
            ordered.append(row)
            emitted.add(key)
            del pending[key]
    return ordered


async def _count(connection: AsyncConnection, table: Table) -> int:
    return int(await connection.scalar(select(func.count()).select_from(table)) or 0)


async def _count_user_rows(connection: AsyncConnection, table: Table) -> int:
    """Count rows excluding migration-seeded reference data.

    ``org_plans`` is seeded with system example plans by the migration
    itself, so a freshly migrated target is never literally empty. The
    system rows carry no user data and are replaced by the source's
    versions during the copy.
    """
    if table.name != "org_plans":
        return await _count(connection, table)
    return int(
        await connection.scalar(
            select(func.count()).select_from(table).where(table.c.is_system == False)  # noqa: E712
        )
        or 0
    )


async def _copy_table(
    source: AsyncConnection,
    target: AsyncConnection,
    table: Table,
) -> int:
    if table.name in _SELF_PARENT_COLUMNS:
        rows = [dict(row) for row in (await source.execute(select(table))).mappings()]
        rows = _ordered_rows(table, rows)
        for offset in range(0, len(rows), _BATCH_SIZE):
            await target.execute(table.insert(), rows[offset : offset + _BATCH_SIZE])
        return len(rows)

    source_count = 0
    result = await source.stream(select(table))
    async for partition in result.mappings().partitions(_BATCH_SIZE):
        rows = [dict(row) for row in partition]
        if rows:
            await target.execute(table.insert(), rows)
            source_count += len(rows)
    return source_count


async def _reset_postgres_sequences(
    connection: AsyncConnection,
    tables: tuple[Table, ...],
) -> None:
    for table in tables:
        for column in table.primary_key.columns:
            if not isinstance(column.type, Integer) or not column.autoincrement:
                continue
            sequence = await connection.scalar(
                text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                {"table_name": table.name, "column_name": column.name},
            )
            if not sequence:
                continue
            maximum = await connection.scalar(select(func.max(column)))
            await connection.execute(
                text("SELECT setval(CAST(:sequence_name AS regclass), :value, :is_called)"),
                {
                    "sequence_name": str(sequence),
                    "value": int(maximum or 1),
                    "is_called": maximum is not None,
                },
            )


async def _copy_application_data(
    source_settings: Settings,
    target_settings: Settings,
) -> RelationalMigrationResult:
    source_engine = create_engine(source_settings)
    target_engine = create_engine(target_settings)
    tables = tuple(table for table in Base.metadata.sorted_tables if table.name != _VECTOR_TABLE)
    vector_table = Base.metadata.tables[_VECTOR_TABLE]
    try:
        await _revision(source_engine, "source")
        await _revision(target_engine, "target")
        async with source_engine.connect() as source, target_engine.begin() as target:
            # The migration seeds system example plans on both sides; drop
            # the target's copies so the source's (possibly edited) plan
            # definitions can be inserted without primary-key conflicts.
            await target.execute(
                Base.metadata.tables["org_plans"].delete().where(
                    Base.metadata.tables["org_plans"].c.is_system == True  # noqa: E712
                )
            )
            nonempty_target = [
                table.name
                for table in (*tables, vector_table)
                if await _count_user_rows(target, table)
            ]
            if nonempty_target:
                raise RuntimeError(
                    "target PostgreSQL application tables must be empty; found rows in "
                    + ", ".join(nonempty_target)
                )
            if await _count(source, vector_table):
                raise RuntimeError(
                    "source document_chunks is not empty; migrate vectors separately with "
                    "app.services.migrate_vectors"
                )

            counts: dict[str, int] = {}
            for table in tables:
                source_count = await _copy_table(source, target, table)
                target_count = await _count(target, table)
                if target_count != source_count:
                    raise RuntimeError(
                        f"row-count verification failed for {table.name}: "
                        f"source={source_count} target={target_count}"
                    )
                counts[table.name] = target_count

            if target_settings.database_backend == "postgresql":
                await _reset_postgres_sequences(target, tables)
            return RelationalMigrationResult(table_counts=counts)
    finally:
        await source_engine.dispose()
        await target_engine.dispose()


async def migrate_sqlite_relational(
    source_path: Path,
    target_settings: Settings,
) -> RelationalMigrationResult:
    """Copy current-head SQLite application tables into an empty PostgreSQL target."""
    source = source_path.expanduser().resolve()
    if not source.is_file():
        raise RuntimeError(f"SQLite source database does not exist: {source}")
    if target_settings.database_backend != "postgresql":
        raise RuntimeError("relational migration target must use postgresql+asyncpg")
    source_settings = Settings(
        data_dir=source.parent,
        database_url=f"sqlite+aiosqlite:///{source.as_posix()}",
    )
    return await _copy_application_data(source_settings, target_settings)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.services.migrate_sqlite",
        description="Copy an offline current-head SQLite app.db into empty PostgreSQL.",
    )
    parser.add_argument("--source", type=Path, required=True, help="offline SQLite app.db copy")
    args = parser.parse_args()
    result = asyncio.run(migrate_sqlite_relational(args.source, get_settings()))
    print(f"migrated {result.rows} row(s) across {result.tables} application table(s)")


if __name__ == "__main__":
    main()
