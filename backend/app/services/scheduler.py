"""Dedicated expired execution-lease scheduler entrypoint."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.config import get_settings, validate_runtime_settings
from app.core.logging import setup_logging
from app.core.secret_providers import SecretResolver, build_secret_provider_chain
from app.core.security import create_secret_box
from app.db.base import create_engine, create_session_factory, init_db
from app.db.migrations import CURRENT_REVISION
from app.engine.execution_scheduler import ExecutionLeaseScheduler
from app.engine.workflow_callback_dispatcher import WorkflowCallbackDispatcher
from app.engine.workflow_schedule_scheduler import WorkflowScheduleScheduler
from app.services.process_runtime import wait_for_shutdown


async def _main() -> None:
    settings = get_settings()
    if settings.process_role != "scheduler":
        raise RuntimeError("scheduler service requires APP_PROCESS_ROLE=scheduler")
    validate_runtime_settings(settings)
    setup_logging(settings.log_level, json_logs=settings.log_format == "json")
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    scheduler = ExecutionLeaseScheduler(
        sessions,
        poll_seconds=settings.execution_scheduler_poll_seconds,
        batch_size=settings.execution_scheduler_batch_size,
        max_attempts=settings.execution_lease_max_attempts,
    )
    workflow_scheduler = WorkflowScheduleScheduler(
        sessions,
        poll_seconds=settings.workflow_schedule_poll_seconds,
        batch_size=settings.workflow_schedule_batch_size,
    )
    callback_dispatcher = WorkflowCallbackDispatcher(
        sessions,
        SecretResolver(
            create_secret_box(settings),
            build_secret_provider_chain(settings.docker_secret_dir),
        ),
        poll_seconds=settings.workflow_callback_poll_seconds,
        batch_size=settings.workflow_callback_batch_size,
        lease_seconds=settings.workflow_callback_lease_seconds,
        default_timeout_seconds=settings.workflow_callback_default_timeout_seconds,
        default_max_attempts=settings.workflow_callback_default_max_attempts,
        default_retry_delay_seconds=settings.workflow_callback_default_retry_delay_seconds,
        owner_id=settings.instance_id,
    )
    try:
        await init_db(engine)
        async with sessions() as session:
            result = await session.execute(text("SELECT version_num FROM alembic_version"))
            if result.scalar_one_or_none() != CURRENT_REVISION:
                raise RuntimeError("database is not at the current migration head")
        scheduler.start()
        workflow_scheduler.start()
        callback_dispatcher.start()
        await wait_for_shutdown()
    finally:
        await workflow_scheduler.stop()
        await callback_dispatcher.stop()
        await scheduler.stop()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
