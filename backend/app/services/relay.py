"""Dedicated PostgreSQL-to-Redis execution event relay entrypoint."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.config import get_settings, validate_runtime_settings
from app.core.logging import setup_logging
from app.db.base import create_engine, create_session_factory, init_db
from app.db.migrations import CURRENT_REVISION
from app.engine.event_relay import EventRelay
from app.services.process_runtime import wait_for_shutdown


async def _main() -> None:
    settings = get_settings()
    if settings.process_role != "relay":
        raise RuntimeError("relay service requires APP_PROCESS_ROLE=relay")
    validate_runtime_settings(settings)
    setup_logging(settings.log_level, json_logs=settings.log_format == "json")
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    relay = EventRelay(
        sessions,
        redis_url=settings.redis_url,
        poll_seconds=settings.event_relay_poll_seconds,
        batch_size=settings.event_relay_batch_size,
        stream_maxlen=settings.event_stream_maxlen,
    )
    try:
        await init_db(engine)
        async with sessions() as session:
            result = await session.execute(text("SELECT version_num FROM alembic_version"))
            if result.scalar_one_or_none() != CURRENT_REVISION:
                raise RuntimeError("database is not at the current migration head")
        await relay.start()
        await wait_for_shutdown()
    finally:
        await relay.stop()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
