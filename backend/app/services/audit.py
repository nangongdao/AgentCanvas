"""Safe, tenant-aware creation of administrative audit records."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorIdentity
from app.core.auth import Principal
from app.db.models.audit import AuditLog
from app.db.repositories.audit import AuditLogRepo
from app.db.repositories.tenant import ProjectRepo

_MAX_DETAILS_BYTES = 8 * 1024
_FORBIDDEN_DETAIL_KEYS = frozenset(
    {
        "api_key",
        "api_key_encrypted",
        "authorization",
        "credential",
        "credentials",
        "decision",
        "dsl",
        "env",
        "headers",
        "password",
        "payload",
        "plaintext",
        "secret",
        "token",
        "token_hash",
    }
)


def _validated_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    value = dict(details or {})

    def inspect(candidate: Any) -> None:
        if isinstance(candidate, Mapping):
            for key, nested in candidate.items():
                if not isinstance(key, str):
                    raise ValueError("audit detail keys must be strings")
                if key.strip().lower() in _FORBIDDEN_DETAIL_KEYS:
                    raise ValueError(f"sensitive audit detail key is not allowed: {key}")
                inspect(nested)
        elif isinstance(candidate, (list, tuple)):
            for nested in candidate:
                inspect(nested)

    inspect(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("audit details must be finite JSON data") from exc
    if len(encoded) > _MAX_DETAILS_BYTES:
        raise ValueError("audit details exceed the 8 KiB limit")
    return value


async def record_audit(
    session: AsyncSession,
    principal: Principal,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    resource_name: str | None = None,
    organization_id: str | None = None,
    project_id: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> AuditLog:
    """Append one safe audit row in the caller's transaction."""
    if project_id is not None:
        project = await ProjectRepo(session).get(project_id)
        if project is None:
            raise ValueError("audit project scope does not exist")
        if organization_id is not None and organization_id != project.organization_id:
            raise ValueError("audit project does not belong to the organization scope")
        organization_id = project.organization_id
    actor = ActorIdentity.from_principal(principal)
    return await AuditLogRepo(session).create(
        organization_id=organization_id,
        project_id=project_id,
        actor_user_id=actor.user_id,
        actor_key=actor.key,
        actor_subject=actor.subject,
        auth_method=principal.auth_method,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
        details=_validated_details(details),
    )


def summarize_human_approval(value: Mapping[str, Any]) -> dict[str, Any]:
    """Derive a coarse outcome without retaining the submitted approval payload."""
    candidate: Any = None
    for key in ("approved", "approve", "decision", "ok"):
        if key in value:
            candidate = value[key]
            break
    if isinstance(candidate, bool):
        outcome = "approved" if candidate else "rejected"
    elif isinstance(candidate, str):
        normalized = candidate.strip().lower()
        if normalized in {"approve", "approved", "yes", "true", "ok"}:
            outcome = "approved"
        elif normalized in {"reject", "rejected", "no", "false", "deny", "denied"}:
            outcome = "rejected"
        else:
            outcome = "submitted"
    else:
        outcome = "submitted"
    return {"outcome": outcome, "submitted_field_count": len(value)}


__all__ = ["record_audit", "summarize_human_approval"]
