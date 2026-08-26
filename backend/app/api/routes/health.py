"""Health and readiness probes.

* ``/healthz`` — liveness, unchanged: the process is up and the app loaded.
  Used by compose ``healthcheck`` and load balancers to restart hung pods.
* ``/livez`` — same cheap liveness signal, explicit Kubernetes-style name.
* ``/readyz`` — readiness: verifies the dependencies a request actually
  needs (database round-trip, Alembic migration head, durable checkpointer,
  vector store reachability, config-safety invariants). Returns 503 with a
  per-check breakdown until everything is ready so a container is never
  routed traffic while half-initialized or after a dependency failure
  (R-08 / U2-5). Redis is *not* readiness-blocking because the memory store
  is designed to degrade to SQLite, but the active backend is reported.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text

from app.api.deps import get_container
from app.core.container import ServiceContainer
from app.db.migrations import CURRENT_REVISION

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


async def _database_check_async(container: ServiceContainer) -> dict[str, Any]:
    backend = container.settings.database_backend
    try:
        async with container.session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — readiness must report any failure
        return {"state": "unavailable", "backend": backend, "error": str(exc)[:200]}
    return {"state": "ready", "backend": backend}


async def _migration_check(container: ServiceContainer) -> dict[str, Any]:
    """Confirm the schema is stamped at the current Alembic head."""
    try:
        async with container.session_factory() as session:
            result = await session.execute(text("SELECT version_num FROM alembic_version"))
            version = result.scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        return {"state": "unavailable", "error": str(exc)[:200]}
    if version != CURRENT_REVISION:
        return {
            "state": "unavailable",
            "error": f"expected {CURRENT_REVISION}, got {version}",
        }
    return {"state": "ready", "version": version}


def _checkpointer_check() -> dict[str, Any]:
    from app.engine.checkpoint import checkpointer_status

    status = checkpointer_status()
    # ``degraded`` (in-memory fallback) is allowed only outside production;
    # production build_container() would already have failed fast. Readiness
    # still surfaces the degradation so operators can see it.
    if status.get("state") == "unavailable":
        return status
    return {"state": "ready", "backend": status.get("backend")}


async def _vector_store_check(container: ServiceContainer) -> dict[str, Any]:
    """Best-effort reachability of the configured vector store backend.

    Failure is reported but not fatal for non-RAG requests, so we expose it as
    an informational check. The store's public ``health()`` surface is used so
    readiness no longer depends on Chroma private collection handles.
    """
    try:
        store = container.rag_service.vector_store
        result = await store.health()
        if "backend" not in result:
            result = {
                **result,
                "backend": getattr(store, "backend_name", "unknown"),
            }
        return result
    except Exception as exc:  # noqa: BLE001
        return {
            "state": "unavailable",
            "backend": getattr(
                getattr(container.rag_service, "vector_store", None),
                "backend_name",
                "unknown",
            ),
            "error": str(exc)[:200],
        }


def _config_safety_check(container: ServiceContainer) -> dict[str, Any]:
    """Catch ``/readyz``-time config-safety regressions."""
    settings = container.settings
    problems: list[str] = []
    if settings.is_production:
        if not settings.secret_key:
            problems.append("SECRET_KEY missing in production")
        if settings.auth_mode != "token":
            problems.append("AUTH_MODE must be token in production")
    if problems:
        return {"state": "unavailable", "errors": problems}
    return {"state": "ready", "process_role": settings.process_role}


def _sandbox_check(container: ServiceContainer) -> dict[str, Any]:
    """Surface the active process sandbox (C8-1).

    Informational, never readiness-blocking: a degraded sandbox (Windows dev,
    stripped image) does not stop the API from serving, but it *must* be visible
    so an operator cannot mistake unisolated code execution for sandboxed. The
    enforcement flag distinguishes "OS-isolated, permissions mandatory" from
    "environment cleanup only".
    """
    status = container.sandbox.describe()
    status["enforce_permissions"] = container.settings.sandbox_enforce_permissions
    return status


async def _run_ready_checks(container: ServiceContainer) -> dict[str, Any]:
    """Run all readiness checks and return a structured report.

    Database + migration + checkpointer + config are *blocking*: a 503 is
    returned while any of them is unavailable. The vector store and sandbox are
    informational (RAG-/code-only) and reported but never flip the overall state.
    """
    db = await _database_check_async(container)
    migration = await _migration_check(container)
    checkpointer = _checkpointer_check()
    config = _config_safety_check(container)
    vector = await _vector_store_check(container)
    sandbox = _sandbox_check(container)

    checks: dict[str, Any] = {
        "database": db,
        "migrations": migration,
        "checkpointer": checkpointer,
        "config": config,
        "vector_store": vector,
        "memory_backend": getattr(container.memory_store, "backend_name", "none"),
        "sandbox": sandbox,
    }
    blocking = ("database", "migrations", "checkpointer", "config")
    ready = all(checks[name].get("state") == "ready" for name in blocking)
    checks["ready"] = ready
    return checks


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "app": "agentcanvas"}


@router.get("/livez")
async def livez() -> dict[str, str]:
    return {"status": "alive", "app": "agentcanvas"}


@router.get("/readyz")
async def readyz(
    response: Response,
    container: ContainerDep,
) -> dict[str, Any]:
    checks = await _run_ready_checks(container)
    if not checks["ready"]:
        response.status_code = 503
    return {
        "status": "ready" if checks["ready"] else "unavailable",
        "app": "agentcanvas",
        "instance_id": container.settings.instance_id,
        "process_role": container.settings.process_role,
        "checks": checks,
    }
