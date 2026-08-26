"""Backend-aware checkpointer factory for LangGraph persistence.

The checkpointer backs workflow interrupt/resume (human approval) and
execution replay, so it must be *durable*. SQLite remains the single-instance
development backend; PostgreSQL deployments use ``AsyncPostgresSaver``.

Fail-mode policy (R-05 / U1-3):

* In **production** the configured durable checkpointer is mandatory. If it cannot be
  initialized we raise ``CheckpointerUnavailable`` instead of silently
  falling back to ``MemorySaver`` — an in-memory saver would lose every
  pending ``waiting_approval`` execution on the next restart, which is
  exactly the data the platform promises to preserve. The readiness probe
  surfaces the same failure so the container never receives traffic while
  the checkpointer is broken.
* In development / test we keep the graceful ``MemorySaver`` fallback so a
  missing SQLite driver never blocks local iteration, but the degradation
  is logged loudly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy.engine import make_url

from app.core.config import Settings

logger = logging.getLogger(__name__)


class CheckpointerUnavailable(RuntimeError):
    """Raised when a durable checkpointer cannot be initialized in production."""


_saver_cm: Any = None
_saver: Any = None
# ``True`` once a durable saver was built; ``False`` when we degraded to the
# in-memory fallback. ``None`` means ``make_checkpointer`` has not run yet.
_durable: bool | None = None
_init_error: str | None = None
_backend: str | None = None

POSTGRES_CHECKPOINT_SETUP_LOCK_KEY = int.from_bytes(b"AgentChk", "big", signed=False)


@dataclass(frozen=True)
class CheckpointCopyResult:
    checkpoints: int
    writes: int
    threads: int


def make_strict_serde() -> JsonPlusSerializer:
    """Build a checkpoint serializer with an explicit object allowlist.

    LangGraph 1.x defaults to a permissive serde and warns that the default
    ``allowed_objects`` will tighten in a future release. We pin it now to
    the built-in ``core`` set (langgraph's own message/channel types) so
    checkpoint deserialization can never load arbitrary pickles — the exact
    hardening the R-01 advisories ask for. ``pickle_fallback`` stays off.
    """
    return JsonPlusSerializer(
        allowed_json_modules=(("core",),),
        allowed_msgpack_modules=(("core",),),
    )


async def _try_build_sqlite_saver(settings: Settings) -> Any:
    """Attempt to construct + enter the AsyncSqliteSaver context.

    Returns the live saver. Raises on any failure so the caller can apply
    the environment-aware fail policy.
    """
    global _saver_cm, _saver
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    settings.checkpoint_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(settings.checkpoint_db_path))
    saver = AsyncSqliteSaver(conn, serde=make_strict_serde())
    try:
        await saver.setup()
    except Exception:
        await conn.close()
        raise
    _saver_cm = conn  # close via __aexit__ semantics below
    logger.info("checkpointer: AsyncSqliteSaver (%s)", settings.checkpoint_db_path)
    return saver


def _postgres_conninfo(settings: Settings) -> str:
    """Convert the validated SQLAlchemy URL to Psycopg's driver-neutral URL."""
    url = make_url(settings.effective_database_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


async def _try_build_postgres_saver(settings: Settings) -> Any:
    """Build a bounded PostgreSQL saver and serialize its schema setup."""
    global _saver_cm
    import asyncio

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool[AsyncConnection[dict[str, Any]]](
        conninfo=_postgres_conninfo(settings),
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        min_size=1,
        max_size=max(1, settings.database_pool_size),
        open=False,
        timeout=settings.database_pool_timeout_seconds,
        max_lifetime=settings.database_pool_recycle_seconds,
        reconnect_timeout=settings.database_pool_timeout_seconds,
        name="agentcanvas-checkpoints",
    )
    try:
        await pool.open(wait=True, timeout=settings.database_pool_timeout_seconds)
        async with pool.connection() as connection:
            deadline = (
                asyncio.get_running_loop().time() + settings.database_migration_lock_timeout_seconds
            )
            lock_acquired = False
            while not lock_acquired:
                result = await connection.execute(
                    "SELECT pg_try_advisory_lock(%s) AS locked",
                    (POSTGRES_CHECKPOINT_SETUP_LOCK_KEY,),
                )
                row = await result.fetchone()
                lock_acquired = bool(row and row["locked"])
                if lock_acquired:
                    break
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError("timed out waiting for the PostgreSQL checkpoint setup lock")
                await asyncio.sleep(min(0.2, remaining))
            try:
                await AsyncPostgresSaver(
                    connection,
                    serde=make_strict_serde(),
                ).setup()
            finally:
                await connection.execute(
                    "SELECT pg_advisory_unlock(%s)",
                    (POSTGRES_CHECKPOINT_SETUP_LOCK_KEY,),
                )
    except BaseException:
        await pool.close()
        raise

    _saver_cm = pool
    logger.info(
        "checkpointer: AsyncPostgresSaver (%s; max_pool_size=%d)",
        settings.database_display_url,
        max(1, settings.database_pool_size),
    )
    return AsyncPostgresSaver(pool, serde=make_strict_serde())


async def copy_sqlite_checkpoints(
    source_path: Path,
    target: Any,
) -> CheckpointCopyResult:
    """Idempotently copy a legacy SQLite saver into another durable saver."""
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    source_path = source_path.resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"checkpoint source does not exist: {source_path}")

    connection = await aiosqlite.connect(str(source_path))
    source = AsyncSqliteSaver(connection, serde=make_strict_serde())
    checkpoint_count = 0
    write_count = 0
    threads: set[str] = set()
    try:
        async for item in source.alist(None):
            configurable = item.config["configurable"]
            thread_id = str(configurable["thread_id"])
            checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
            parent_config = item.parent_config or {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                }
            }
            await target.aput(
                parent_config,
                item.checkpoint,
                item.metadata,
                item.checkpoint.get("channel_versions", {}),
            )

            writes_by_task: dict[str, list[tuple[str, Any]]] = {}
            for task_id, channel, value in item.pending_writes or []:
                writes_by_task.setdefault(task_id, []).append((channel, value))
                write_count += 1
            for task_id, writes in writes_by_task.items():
                await target.aput_writes(item.config, writes, task_id)

            checkpoint_count += 1
            threads.add(thread_id)
    finally:
        await connection.close()

    return CheckpointCopyResult(
        checkpoints=checkpoint_count,
        writes=write_count,
        threads=len(threads),
    )


async def ensure_waiting_approval_checkpoints(
    session_factory: Any,
    checkpointer: Any,
) -> int:
    """Fail closed when a waiting execution has no resumable checkpoint."""
    from sqlalchemy import select

    from app.db.models import Execution

    async with session_factory() as session:
        result = await session.execute(
            select(Execution.id, Execution.thread_id).where(Execution.status == "waiting_approval")
        )
        waiting = list(result.all())

    missing: list[str] = []
    try:
        for execution_id, thread_id in waiting:
            checkpoint = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
            if checkpoint is None:
                missing.append(execution_id)
    except Exception as exc:
        raise CheckpointerUnavailable(
            "failed to verify durable waiting approval checkpoints"
        ) from exc

    if missing:
        raise CheckpointerUnavailable(
            f"{len(missing)} waiting approval execution(s) have no durable checkpoint; "
            "run the legacy checkpoint migration before startup"
        )
    return len(waiting)


async def make_checkpointer(settings: Settings):
    """Return a process-wide durable checkpointer.

    Production: raises ``CheckpointerUnavailable`` on failure (no silent
    fallback). Development/test: falls back to ``MemorySaver`` and logs.
    """
    global _backend, _saver, _durable, _init_error
    if _saver is not None:
        return _saver
    _backend = settings.database_backend
    try:
        if _backend == "postgresql":
            _saver = await _try_build_postgres_saver(settings)
        else:
            _saver = await _try_build_sqlite_saver(settings)
        _durable = True
        _init_error = None
    except Exception as exc:
        _init_error = f"{_backend} checkpointer initialization failed ({type(exc).__name__})"
        _durable = False
        if settings.is_production:
            logger.error(
                "checkpointer init failed in production; refusing MemorySaver fallback: %s",
                _init_error,
            )
            raise CheckpointerUnavailable(
                f"durable {_backend} checkpointer unavailable in production ({type(exc).__name__})"
            ) from exc
        logger.error(
            "failed to init durable checkpointer; falling back to MemorySaver: %s",
            _init_error,
        )
        _saver = MemorySaver()
    return _saver


async def close_checkpointer() -> None:
    global _backend, _saver_cm, _saver, _durable, _init_error
    conn: Any = _saver_cm
    if conn is not None:
        try:
            await conn.close()
        except Exception:
            logger.exception("failed to close checkpointer")
    _saver_cm = None
    _saver = None
    _durable = None
    _init_error = None
    _backend = None


def checkpointer_status() -> dict[str, Any]:
    """Snapshot of checkpointer health for the readiness probe.

    ``state`` is one of ``ready`` (durable saver built), ``degraded``
    (in-memory fallback in use) or ``unavailable`` (init failed and no
    fallback was permitted — production fail-closed).
    """
    if _durable is True:
        return {"state": "ready", "backend": _backend}
    if _saver is not None and _durable is False:
        return {"state": "degraded", "backend": "memory"}
    if _init_error is not None:
        return {
            "state": "unavailable",
            "backend": _backend or "none",
            "error": _init_error,
        }
    return {"state": "unavailable", "backend": "none"}
