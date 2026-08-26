"""Migrate legacy SQLite LangGraph checkpoints into PostgreSQL."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from app.core.config import get_settings, validate_runtime_settings
from app.db.base import create_engine, create_session_factory
from app.engine.checkpoint import (
    CheckpointCopyResult,
    close_checkpointer,
    copy_sqlite_checkpoints,
    ensure_waiting_approval_checkpoints,
    make_checkpointer,
)


async def migrate_legacy_checkpoints(source_path: Path | None = None) -> CheckpointCopyResult:
    """Copy the configured legacy SQLite file into the configured PostgreSQL saver."""
    settings = get_settings()
    validate_runtime_settings(settings)
    if settings.database_backend != "postgresql":
        raise RuntimeError(
            "checkpoint migration requires DATABASE_URL=postgresql+asyncpg; "
            "SQLite is already the source backend"
        )

    source = (source_path or settings.checkpoint_db_path).expanduser().resolve()
    target = await make_checkpointer(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        result = await copy_sqlite_checkpoints(source, target)
        await ensure_waiting_approval_checkpoints(session_factory, target)
        return result
    finally:
        await close_checkpointer()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.services.migrate_checkpoints",
        description="Copy legacy SQLite LangGraph checkpoints into PostgreSQL.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        help="legacy checkpoints.db path (defaults to APP_DATA_DIR/checkpoints.db)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    result = asyncio.run(migrate_legacy_checkpoints(args.source))
    print(
        f"migrated {result.checkpoints} checkpoint(s), {result.writes} write(s), "
        f"across {result.threads} thread(s)"
    )


if __name__ == "__main__":
    main()
