"""Usage metering fact reads, exports, and billing reconciliation (C7-1).

Reads are pure queries over ``usage_daily_facts`` — the billing source of
truth written by the aggregation scheduler. Authorization:

- ``project_id`` filters require project VIEWER access (and must match the
  ``organization_id`` filter when both are given).
- ``organization_id`` filters require organization VIEWER membership.
- Unfiltered (platform-wide) queries are global-admin only; they are the
  substrate for the C7-3 platform admin console.

The reconciliation endpoint derives a deterministic SHA-256 digest over a
canonical serialization of a month's facts so a billing system can poll
idempotently: an unchanged month yields an unchanged digest, and the weak
ETag lets repeat polls collapse to empty 304s.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from json import dumps
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ViewerDep, get_session
from app.api.etag import etag_json_response
from app.api.tenant_deps import authorize_project, require_org_member
from app.core.auth import Principal, Role
from app.db.models import UsageDailyFact
from app.db.repositories.usage import UsageFactRepo
from app.schemas.usage import (
    MAX_USAGE_FACT_WINDOW_DAYS,
    UsageDailyOut,
    UsageFactOut,
    UsageReconciliationDayOut,
    UsageReconciliationOut,
    UsageReconciliationTotalsOut,
)

router = APIRouter(prefix="/api/usage", tags=["usage"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

_FACT_COLUMNS = (
    "day",
    "organization_id",
    "project_id",
    "app_id",
    "model_config_id",
    "executions",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost_unknown_executions",
    "storage_bytes_delta",
    "retrievals",
    "estimated_cost_usd",
)


def _is_global_admin(principal: Principal) -> bool:
    return principal.role == Role.ADMIN and principal.project_id is None


async def _authorize_scope(
    session: AsyncSession,
    principal: Principal,
    *,
    organization_id: str | None,
    project_id: str | None,
) -> None:
    if project_id is not None:
        project = await authorize_project(session, principal, project_id, required=Role.VIEWER)
        if organization_id is not None and project.organization_id != organization_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="project does not belong to the given organization",
            )
        return
    if organization_id is not None:
        await require_org_member(session, principal, organization_id, required=Role.VIEWER)
        return
    if not _is_global_admin(principal):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="organization_id or project_id scope required",
        )


def _check_window(from_day: date, to_day: date) -> None:
    if from_day > to_day:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="from_day must not be after to_day",
        )
    if (to_day - from_day).days > MAX_USAGE_FACT_WINDOW_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"window is limited to {MAX_USAGE_FACT_WINDOW_DAYS} days",
        )


def _fact_to_dict(row: UsageDailyFact) -> dict[str, Any]:
    return {
        "day": row.day,
        "organization_id": row.organization_id,
        "project_id": row.project_id,
        "app_id": row.app_id,
        "model_config_id": row.model_config_id,
        "executions": row.executions,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "total_tokens": row.total_tokens,
        "cost_unknown_executions": row.cost_unknown_executions,
        "storage_bytes_delta": row.storage_bytes_delta,
        "retrievals": row.retrievals,
        "estimated_cost_usd": row.estimated_cost_usd,
    }


def _canonical_fact_line(row: UsageDailyFact) -> str:
    data = _fact_to_dict(row)
    return "|".join("" if data[column] is None else str(data[column]) for column in _FACT_COLUMNS)


def _digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _csv_bytes(rows: Sequence[UsageDailyFact]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(_FACT_COLUMNS)
    for row in rows:
        values = (_fact_to_dict(row)[column] for column in _FACT_COLUMNS)
        writer.writerow(
            "" if value is None else value.isoformat() if isinstance(value, date) else value
            for value in values
        )
    return buffer.getvalue().encode("utf-8")


@router.get("/daily")
async def get_usage_daily(
    request: Request,
    session: SessionDep,
    principal: ViewerDep,
    from_day: Annotated[date, Query()],
    to_day: Annotated[date, Query()],
    organization_id: Annotated[str | None, Query(max_length=32)] = None,
    project_id: Annotated[str | None, Query(max_length=32)] = None,
) -> Any:
    await _authorize_scope(
        session, principal, organization_id=organization_id, project_id=project_id
    )
    _check_window(from_day, to_day)
    rows = await UsageFactRepo(session).list_range(
        from_day=from_day,
        to_day=to_day,
        organization_id=organization_id,
        project_id=project_id,
    )
    payload = UsageDailyOut(
        from_day=from_day,
        to_day=to_day,
        facts=[UsageFactOut.model_validate(row) for row in rows],
    )
    return etag_json_response(request, payload.model_dump(mode="json"))


@router.get("/export")
async def export_usage(
    session: SessionDep,
    principal: ViewerDep,
    from_day: Annotated[date, Query()],
    to_day: Annotated[date, Query()],
    format: Annotated[str, Query(pattern="^(csv|json)$")] = "csv",
    organization_id: Annotated[str | None, Query(max_length=32)] = None,
    project_id: Annotated[str | None, Query(max_length=32)] = None,
) -> StreamingResponse:
    await _authorize_scope(
        session, principal, organization_id=organization_id, project_id=project_id
    )
    _check_window(from_day, to_day)
    rows = await UsageFactRepo(session).list_range(
        from_day=from_day,
        to_day=to_day,
        organization_id=organization_id,
        project_id=project_id,
    )
    scope_suffix = organization_id or project_id or "all"
    filename = f"usage-{scope_suffix}-{from_day.isoformat()}-{to_day.isoformat()}.{format}"
    if format == "csv":
        content = _csv_bytes(rows)
        media_type = "text/csv"
    else:
        payload = UsageDailyOut(
            from_day=from_day,
            to_day=to_day,
            facts=[UsageFactOut.model_validate(row) for row in rows],
        )
        content = dumps(payload.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
        media_type = "application/json"

    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/reconciliation")
async def get_usage_reconciliation(
    request: Request,
    session: SessionDep,
    principal: ViewerDep,
    month: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")],
    organization_id: Annotated[str | None, Query(max_length=32)] = None,
    project_id: Annotated[str | None, Query(max_length=32)] = None,
) -> Any:
    await _authorize_scope(
        session, principal, organization_id=organization_id, project_id=project_id
    )
    year, month_number = (int(part) for part in month.split("-"))
    month_first = date(year, month_number, 1)
    rows = await UsageFactRepo(session).list_month(
        month_first,
        organization_id=organization_id,
        project_id=project_id,
    )
    by_day: dict[date, list[UsageDailyFact]] = defaultdict(list)
    for row in rows:
        by_day[row.day].append(row)

    day_outs: list[UsageReconciliationDayOut] = []
    month_lines: list[str] = []
    totals = UsageReconciliationTotalsOut()
    total_cost = Decimal(0)
    total_cost_known = True
    for day in sorted(by_day):
        day_rows = by_day[day]
        lines = [_canonical_fact_line(row) for row in day_rows]
        day_digest = _digest([str(len(day_rows)), *lines])
        day_outs.append(UsageReconciliationDayOut(day=day, rows=len(day_rows), digest=day_digest))
        month_lines.append(f"{day.isoformat()}:{day_digest}")
        for row in day_rows:
            totals.executions += row.executions
            totals.prompt_tokens += row.prompt_tokens
            totals.completion_tokens += row.completion_tokens
            totals.total_tokens += row.total_tokens
            totals.cost_unknown_executions += row.cost_unknown_executions
            totals.storage_bytes_delta += row.storage_bytes_delta
            totals.retrievals += row.retrievals
            if row.estimated_cost_usd is None:
                if row.executions or row.cost_unknown_executions:
                    total_cost_known = False
            else:
                total_cost += Decimal(row.estimated_cost_usd)
    if total_cost_known:
        totals.estimated_cost_usd = format(total_cost, "f")
    payload = UsageReconciliationOut(
        month=month,
        digest=_digest(month_lines) if month_lines else _digest([]),
        days=day_outs,
        totals=totals,
        scope={"organization_id": organization_id, "project_id": project_id},
    )
    return etag_json_response(request, payload.model_dump(mode="json"))


__all__ = ["router"]
