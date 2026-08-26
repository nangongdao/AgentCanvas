"""Compare a restored PostgreSQL database with its quiesced source."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict, dataclass, replace
from decimal import Decimal

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings, load_settings, validate_runtime_settings
from app.db.base import create_engine
from app.db.migrations import CURRENT_REVISION, CURRENT_TABLES
from app.services.backup import BackupError, _postgres_identity, _postgres_url

CHECKPOINT_TABLES = (
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)


@dataclass(frozen=True)
class DatabaseInventory:
    revision: str
    table_counts: dict[str, int]
    checkpoint_counts: dict[str, int]
    waiting_approval_ids: tuple[str, ...]
    waiting_checkpoint_threads: tuple[str, ...]
    vector_extension_version: str
    vector_probe_distance: str | None
    vector_probe_top_id: str | None


async def collect_inventory(engine: AsyncEngine) -> DatabaseInventory:
    async with engine.connect() as connection:
        revision_result = await connection.execute(text("SELECT version_num FROM alembic_version"))
        revision = str(revision_result.scalar_one_or_none() or "")
        table_counts: dict[str, int] = {}
        for table_name in sorted(CURRENT_TABLES):
            result = await connection.execute(text(f'SELECT count(*) FROM "{table_name}"'))
            table_counts[table_name] = int(result.scalar_one())

        waiting_result = await connection.execute(
            text("SELECT id, thread_id FROM executions WHERE status='waiting_approval' ORDER BY id")
        )
        waiting_rows = list(waiting_result.all())
        waiting_ids = tuple(str(row.id) for row in waiting_rows)
        waiting_threads = tuple(str(row.thread_id) for row in waiting_rows)

        checkpoint_counts: dict[str, int] = {}
        for table_name in CHECKPOINT_TABLES:
            exists_result = await connection.execute(
                text("SELECT to_regclass(:name) IS NOT NULL"), {"name": table_name}
            )
            if bool(exists_result.scalar_one()):
                count_result = await connection.execute(
                    text(f'SELECT count(*) FROM "{table_name}"')
                )
                checkpoint_counts[table_name] = int(count_result.scalar_one())
            else:
                checkpoint_counts[table_name] = -1

        waiting_checkpoint_threads: tuple[str, ...] = ()
        if waiting_threads and checkpoint_counts.get("checkpoints", -1) >= 0:
            thread_statement = text(
                "SELECT DISTINCT thread_id FROM checkpoints "
                "WHERE thread_id IN :thread_ids ORDER BY thread_id"
            ).bindparams(bindparam("thread_ids", expanding=True))
            thread_result = await connection.execute(
                thread_statement,
                {"thread_ids": list(waiting_threads)},
            )
            waiting_checkpoint_threads = tuple(str(value) for value in thread_result.scalars())

        extension_result = await connection.execute(
            text("SELECT extversion FROM pg_extension WHERE extname='vector'")
        )
        vector_version = str(extension_result.scalar_one_or_none() or "")
        vector_probe: str | None = None
        vector_probe_top_id: str | None = None
        if table_counts.get("document_chunks", 0) > 0:
            probe_result = await connection.execute(
                text(
                    "WITH probe AS ("
                    " SELECT id, kb_id, dimensions, embedding"
                    " FROM document_chunks ORDER BY id LIMIT 1"
                    ")"
                    " SELECT candidate.id, candidate.embedding <=> probe.embedding AS distance"
                    " FROM document_chunks AS candidate CROSS JOIN probe"
                    " WHERE candidate.kb_id = probe.kb_id"
                    " AND candidate.dimensions = probe.dimensions"
                    " ORDER BY distance ASC, candidate.id ASC LIMIT 1"
                )
            )
            ranked = probe_result.one()
            vector_probe_top_id = str(ranked.id)
            distance = Decimal(str(ranked.distance))
            if distance != 0:
                raise BackupError(f"pgvector ranking probe returned distance {distance}")
            vector_probe = str(distance)

    return DatabaseInventory(
        revision=revision,
        table_counts=table_counts,
        checkpoint_counts=checkpoint_counts,
        waiting_approval_ids=waiting_ids,
        waiting_checkpoint_threads=waiting_checkpoint_threads,
        vector_extension_version=vector_version,
        vector_probe_distance=vector_probe,
        vector_probe_top_id=vector_probe_top_id,
    )


def compare_inventories(
    source: DatabaseInventory,
    target: DatabaseInventory,
) -> list[str]:
    mismatches: list[str] = []
    if source.revision != CURRENT_REVISION or target.revision != CURRENT_REVISION:
        mismatches.append(
            f"migration head: source={source.revision!r} target={target.revision!r} "
            f"expected={CURRENT_REVISION!r}"
        )
    if not source.vector_extension_version or not target.vector_extension_version:
        mismatches.append("pgvector extension is missing from source or restored database")
    missing_checkpoint_tables = [
        name
        for name in CHECKPOINT_TABLES
        if source.checkpoint_counts.get(name, -1) < 0 or target.checkpoint_counts.get(name, -1) < 0
    ]
    if missing_checkpoint_tables:
        mismatches.append(f"checkpoint tables missing: {missing_checkpoint_tables!r}")
    for field_name in (
        "table_counts",
        "checkpoint_counts",
        "waiting_approval_ids",
        "waiting_checkpoint_threads",
        "vector_extension_version",
        "vector_probe_top_id",
    ):
        source_value = getattr(source, field_name)
        target_value = getattr(target, field_name)
        if source_value != target_value:
            mismatches.append(f"{field_name}: source={source_value!r} target={target_value!r}")
    if source.table_counts.get("document_chunks", 0) > 0 and (
        target.vector_probe_distance is None or target.vector_probe_top_id is None
    ):
        mismatches.append("pgvector retrieval probe did not run on restored document chunks")
    return mismatches


async def run_restore_drill(
    settings: Settings,
    *,
    target_database_url: str,
) -> dict[str, object]:
    source_url = _postgres_url(settings.effective_database_url, label="source")
    target_url = _postgres_url(target_database_url, label="target")
    if _postgres_identity(source_url) == _postgres_identity(target_url):
        raise BackupError("restore drill target must differ from the source database")
    source_settings = replace(settings, startup_migrations=False)
    target_settings = replace(
        settings,
        database_url=target_database_url,
        startup_migrations=False,
    )
    validate_runtime_settings(source_settings)
    validate_runtime_settings(target_settings)
    source_engine = create_engine(source_settings)
    target_engine = create_engine(target_settings)
    try:
        source_inventory, target_inventory = await asyncio.gather(
            collect_inventory(source_engine),
            collect_inventory(target_engine),
        )
    finally:
        await source_engine.dispose()
        await target_engine.dispose()
    mismatches = compare_inventories(source_inventory, target_inventory)
    if mismatches:
        raise BackupError("restore drill failed: " + "; ".join(mismatches))
    return {
        "status": "passed",
        "source": asdict(source_inventory),
        "target": asdict(target_inventory),
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.services.restore_drill")
    parser.add_argument(
        "--target-database-url",
        default=os.environ.get("PG_RESTORE_TARGET_DATABASE_URL", ""),
    )
    args = parser.parse_args()
    if not args.target_database_url:
        raise BackupError(
            "restore drill requires --target-database-url or PG_RESTORE_TARGET_DATABASE_URL"
        )
    result = asyncio.run(
        run_restore_drill(
            load_settings(),
            target_database_url=args.target_database_url,
        )
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
