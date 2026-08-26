"""CLI entrypoint: `python -m app.services.seeds` to (re)seed demo workflows."""

from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.services.seeds import seed_workflows


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        created = await seed_workflows(session_factory)
        print(f"seeded {created} new workflow(s); existing rows left untouched.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
