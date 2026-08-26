"""Explicit release migration entrypoint.

The production Compose stack runs this command as a one-shot service before
starting any web replica. It is intentionally separate from the seed command
and from the long-running Uvicorn process.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.db.migrations import upgrade_database


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    await upgrade_database(settings)
    logging.getLogger(__name__).info("database migration completed at current head")


if __name__ == "__main__":
    asyncio.run(_main())
