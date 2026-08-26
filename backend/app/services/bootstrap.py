"""Explicit one-shot data bootstrap after release migrations."""

from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings, validate_runtime_settings
from app.core.container import (
    migrate_stored_secrets,
    seed_default_model,
    seed_demo_mcp_servers,
)
from app.core.secret_providers import SecretResolver, build_secret_provider_chain
from app.core.security import create_secret_box
from app.db.base import create_engine, create_session_factory, init_db
from app.services import seed_workflows
from app.services.template_seeds import seed_workflow_templates


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    validate_runtime_settings(settings)
    if settings.startup_migrations:
        raise RuntimeError("bootstrap requires STARTUP_MIGRATIONS=false after release migration")
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    secret_box = create_secret_box(settings)
    secret_resolver = SecretResolver(
        secret_box,
        build_secret_provider_chain(settings.docker_secret_dir),
    )
    try:
        await init_db(engine)
        await migrate_stored_secrets(sessions, secret_resolver)
        await seed_default_model(sessions, settings, secret_box, secret_resolver)
        await seed_demo_mcp_servers(sessions, settings, secret_box, secret_resolver)
        workflows = await seed_workflows(sessions)
        templates = await seed_workflow_templates(sessions)
        logging.getLogger(__name__).info(
            "release bootstrap complete (workflows=%d templates=%d)",
            workflows,
            templates,
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
