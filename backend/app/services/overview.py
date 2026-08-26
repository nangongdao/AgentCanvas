"""Read-only operational overview aggregation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Execution, ExecutionEventRow, Workflow, WorkflowVersion
from app.db.repositories.model_configs import ModelConfigRepo
from app.engine.dsl_traversal import iter_located_workflow_nodes
from app.schemas.dsl import AgentConfig, NodeType, WorkflowDSL
from app.schemas.overview import (
    OverviewDayOut,
    OverviewExecutionSummaryOut,
    OverviewOut,
    OverviewWorkflowOut,
)
from app.services.evaluation_costs import COST_QUANTUM_USD, estimate_execution_cost

RECENT_WORKFLOW_LIMIT = 6
EXECUTION_BATCH_SIZE = 250
TERMINAL_EVENT_TYPES = frozenset(
    {"workflow_finished", "workflow_failed", "workflow_cancelled"}
)


@dataclass
class _DayBucket:
    executions: int = 0
    succeeded: int = 0
    failed: int = 0
    cost: Decimal = Decimal(0)
    cost_known: bool = True


@dataclass(frozen=True)
class _CostSnapshot:
    amount: Decimal | None
    known: bool


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_dsl(raw: Any) -> WorkflowDSL | None:
    if not isinstance(raw, dict):
        return None
    try:
        return WorkflowDSL.model_validate(raw)
    except Exception:  # noqa: BLE001 - one broken snapshot must not break the overview
        return None


def _model_ids(dsl: WorkflowDSL) -> set[str]:
    return {
        AgentConfig.model_validate(located.node.config or {}).model_config_id
        for located in iter_located_workflow_nodes(dsl)
        if located.node.type == NodeType.AGENT
    }


def _money(value: Decimal) -> str:
    return format(value.quantize(COST_QUANTUM_USD), "f")


def _cost_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


def _persisted_cost(events: list[ExecutionEventRow]) -> _CostSnapshot | None:
    """Read the immutable runtime price snapshot from the latest terminal event."""
    for event in reversed(events):
        if event.event_type not in TERMINAL_EVENT_TYPES:
            continue
        payload = event.payload_json if isinstance(event.payload_json, dict) else {}
        raw_cost = payload.get("cost")
        if not isinstance(raw_cost, dict):
            continue
        if raw_cost.get("cost_known") is not True:
            return _CostSnapshot(amount=None, known=False)
        amount = _cost_decimal(raw_cost.get("estimated_cost_usd"))
        return _CostSnapshot(amount=amount, known=amount is not None)
    return None


async def _load_events(
    session: AsyncSession,
    execution_ids: list[str],
    *,
    terminal_only: bool,
) -> dict[str, list[ExecutionEventRow]]:
    if not execution_ids:
        return {}
    statement = select(ExecutionEventRow).where(
        ExecutionEventRow.execution_id.in_(execution_ids)
    )
    if terminal_only:
        statement = statement.where(ExecutionEventRow.event_type.in_(TERMINAL_EVENT_TYPES))
    events = (
        (
            await session.execute(
                statement.order_by(
                    ExecutionEventRow.execution_id.asc(),
                    ExecutionEventRow.seq.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[str, list[ExecutionEventRow]] = defaultdict(list)
    for event in events:
        grouped[event.execution_id].append(event)
    return grouped


async def collect_overview(
    session: AsyncSession,
    *,
    project_id: str | None,
    days: int,
    now: datetime | None = None,
) -> OverviewOut:
    """Aggregate recent workflow health and estimated spend for one scope."""
    current = _as_utc(now or datetime.now(UTC))
    first_day = current.date() - timedelta(days=days - 1)
    since = datetime.combine(first_day, time.min, tzinfo=UTC)
    workflow_scope = (
        Workflow.project_id == project_id if project_id is not None else Workflow.project_id.is_(None)
    )

    recent = list(
        (
            await session.execute(
                select(Workflow)
                .where(Workflow.is_archived.is_(False), workflow_scope)
                .order_by(Workflow.updated_at.desc(), Workflow.id.asc())
                .limit(RECENT_WORKFLOW_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    dsls: dict[str, WorkflowDSL | None] = {}
    model_configs: dict[str, Any] = {}
    requested_model_ids: set[str] = set()
    buckets = {first_day + timedelta(days=offset): _DayBucket() for offset in range(days)}
    status_counts: dict[str, int] = defaultdict(int)
    total_cost = Decimal(0)
    all_costs_known = True
    total = 0
    cursor_started_at: datetime | None = None
    cursor_id = ""
    base_execution_query = (
        select(
            Execution.id,
            Execution.status,
            Execution.started_at,
            Execution.workflow_version_id,
            WorkflowVersion.dsl_json,
        )
        .join(Workflow, Workflow.id == Execution.workflow_id)
        .join(WorkflowVersion, WorkflowVersion.id == Execution.workflow_version_id)
        .where(
            workflow_scope,
            Execution.started_at >= since,
            Execution.started_at <= current,
        )
    )
    while True:
        statement = base_execution_query
        if cursor_started_at is not None:
            statement = statement.where(
                or_(
                    Execution.started_at > cursor_started_at,
                    and_(
                        Execution.started_at == cursor_started_at,
                        Execution.id > cursor_id,
                    ),
                )
            )
        rows = list(
            (
                await session.execute(
                    statement.order_by(Execution.started_at.asc(), Execution.id.asc()).limit(
                        EXECUTION_BATCH_SIZE
                    )
                )
            ).all()
        )
        if not rows:
            break
        cursor_started_at = rows[-1][2]
        cursor_id = rows[-1][0]
        execution_ids = [row[0] for row in rows]
        terminal_events = await _load_events(session, execution_ids, terminal_only=True)
        snapshots = {
            execution_id: snapshot
            for execution_id in execution_ids
            if (snapshot := _persisted_cost(terminal_events.get(execution_id, []))) is not None
        }
        fallback_ids = [execution_id for execution_id in execution_ids if execution_id not in snapshots]
        fallback_events = await _load_events(session, fallback_ids, terminal_only=False)

        needed_model_ids: set[str] = set()
        for execution_id, _status, _started_at, version_id, raw_dsl in rows:
            if execution_id in snapshots or version_id in dsls:
                continue
            dsl = _parse_dsl(raw_dsl)
            dsls[version_id] = dsl
            if dsl is not None:
                needed_model_ids.update(_model_ids(dsl))
        missing_model_ids = needed_model_ids - requested_model_ids
        if missing_model_ids:
            model_configs.update(await ModelConfigRepo(session).get_many(missing_model_ids))
            requested_model_ids.update(missing_model_ids)

        for execution_id, status, started_at, version_id, _raw_dsl in rows:
            total += 1
            status_counts[status] += 1
            bucket = buckets[_as_utc(started_at).date()]
            bucket.executions += 1
            if status == "succeeded":
                bucket.succeeded += 1
            elif status == "failed":
                bucket.failed += 1

            snapshot = snapshots.get(execution_id)
            if snapshot is not None:
                exact = snapshot.amount if snapshot.known else None
            else:
                dsl = dsls.get(version_id)
                if dsl is None:
                    exact = None
                else:
                    estimate = estimate_execution_cost(
                        fallback_events.get(execution_id, []),
                        dsl=dsl,
                        model_configs=model_configs,
                    )
                    exact = (
                        Decimal(estimate.estimated_cost_usd)
                        if estimate.cost_known and estimate.estimated_cost_usd is not None
                        else None
                    )
            if exact is None:
                bucket.cost_known = False
                all_costs_known = False
                continue
            bucket.cost += exact
            total_cost += exact

    succeeded = status_counts["succeeded"]
    failed = status_counts["failed"]
    cancelled = status_counts["cancelled"]
    terminal = succeeded + failed + cancelled
    summary = OverviewExecutionSummaryOut(
        total=total,
        succeeded=succeeded,
        failed=failed,
        cancelled=cancelled,
        active=total - terminal,
        success_rate=round(succeeded / terminal, 4) if terminal else None,
    )
    return OverviewOut(
        project_id=project_id,
        days=days,
        since=since,
        recent_workflows=[
            OverviewWorkflowOut(
                id=row.id,
                name=row.name,
                description=row.description or "",
                project_id=row.project_id,
                updated_at=row.updated_at,
            )
            for row in recent
        ],
        execution_summary=summary,
        estimated_cost_usd=_money(total_cost) if all_costs_known else None,
        cost_known=all_costs_known,
        daily=[
            OverviewDayOut(
                date=day.isoformat(),
                executions=bucket.executions,
                succeeded=bucket.succeeded,
                failed=bucket.failed,
                estimated_cost_usd=_money(bucket.cost) if bucket.cost_known else None,
                cost_known=bucket.cost_known,
            )
            for day, bucket in buckets.items()
        ],
    )


__all__ = ["EXECUTION_BATCH_SIZE", "RECENT_WORKFLOW_LIMIT", "collect_overview"]
