"""Alembic migration environment for the async application database."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.db import models  # noqa: F401
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

POSTGRES_MIGRATION_LOCK_KEY = int.from_bytes(b"AgentCan", "big", signed=False)
LANGGRAPH_CHECKPOINT_TABLES = frozenset(
    {"checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"}
)


def _include_name(name: str | None, type_: str, _parent_names: dict[str, str | None]) -> bool:
    return type_ != "table" or name not in LANGGRAPH_CHECKPOINT_TABLES


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_name=_include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_sync_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_name=_include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        async with connectable.connect() as connection:
            lock_acquired = False
            if connection.dialect.name == "postgresql":
                timeout_seconds = float(
                    config.get_main_option("agentcanvas.migration_lock_timeout_seconds") or "60"
                )
                deadline = asyncio.get_running_loop().time() + timeout_seconds
                while not lock_acquired:
                    lock_acquired = bool(
                        await connection.scalar(
                            text("SELECT pg_try_advisory_lock(:lock_key)"),
                            {"lock_key": POSTGRES_MIGRATION_LOCK_KEY},
                        )
                    )
                    if lock_acquired:
                        break
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise TimeoutError(
                            "timed out waiting for the AgentCanvas PostgreSQL migration lock"
                        )
                    await asyncio.sleep(min(0.2, remaining))

                # The lock is session-scoped, so it survives this commit. End
                # the SELECT's implicit transaction and let Alembic own the DDL
                # transaction that follows.
                await connection.commit()

            # PostgreSQL releases the session lock when this connection closes,
            # including failed migration paths with an aborted transaction.
            await connection.run_sync(_run_sync_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
