"""Dedicated durable-queue worker process entrypoint."""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.container import build_container
from app.core.logging import setup_logging
from app.services.process_runtime import wait_for_shutdown


async def _main() -> None:
    settings = get_settings()
    if settings.process_role != "worker":
        raise RuntimeError("worker service requires APP_PROCESS_ROLE=worker")
    setup_logging(settings.log_level, json_logs=settings.log_format == "json")
    container = await build_container(settings)
    try:
        await wait_for_shutdown()
    finally:
        await container.shutdown()


if __name__ == "__main__":
    asyncio.run(_main())
