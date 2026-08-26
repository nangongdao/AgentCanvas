"""Daily usage fact aggregation for metered billing exports (C7-1).

Facts are derived snapshots, not incremental counters: ``aggregate_day``
recomputes one whole UTC day from the authoritative sources and rewrites the
day's rows in a single transaction, so re-running it any number of times
yields identical contents. The rolling scheduler re-aggregates a bounded
trailing window of complete days each sweep, which absorbs executions that
finish after midnight and keeps facts converged without double counting.

Attribution dimensions and their sources:

- ``organization_id`` / ``project_id`` come from the workflow's project.
  Executions on project-less workflows and documents in global knowledge
  bases have no tenancy owner and are skipped — the same rule the project
  quota system applies.
- ``app_id`` comes from the execution's chat session (C3-5 attribution);
  executions that did not run inside an app land in the NULL bucket.
- ``model_config_id`` comes from the DSL's agent-node -> model mapping: each
  finished agent attempt's persisted provider usage is attributed to the
  model that produced it. Executions without agent activity land in the NULL
  model bucket with their execution count but zero tokens.

Cost semantics mirror the D2 estimator: an agent attempt that started but
never finished, a missing usage payload, or a missing rate marks the bucket's
cost unknown (``estimated_cost_usd`` NULL and the execution counted in
``cost_unknown_executions``) instead of silently pricing it as zero. Token
totals always count. Facts are frozen once a day falls out of the
re-aggregation window, so later model price edits cannot rewrite
already-exported billing history; the export digest makes any post-freeze
drift detectable for reconciliation.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ChatSession,
    Document,
    Execution,
    ExecutionEventRow,
    KnowledgeBase,
    Project,
    Workflow,
    WorkflowVersion,
)
from app.db.repositories.model_configs import ModelConfigRepo
from app.db.repositories.usage import UsageFactRepo
from app.engine.dsl_traversal import iter_located_workflow_nodes
from app.schemas.dsl import AgentConfig, NodeType, WorkflowDSL
from app.services.evaluation_costs import COST_QUANTUM_USD
from app.services.execution_inspection import extract_token_usage

TERMINAL_EXECUTION_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
EXECUTION_BATCH_SIZE = 250
TOKENS_PER_MILLION = Decimal(1_000_000)

_FALLBACK_DSL = WorkflowDSL.model_validate({"version": "1.0", "name": "unknown"})


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except Exception:  # noqa: BLE001 - malformed rates are data, not a crash
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


def _validated_dsl(raw: Any) -> WorkflowDSL:
    if not isinstance(raw, dict):
        return _FALLBACK_DSL
    try:
        return WorkflowDSL.model_validate(raw)
    except Exception:  # noqa: BLE001 - a broken snapshot prices as unknown
        return _FALLBACK_DSL


@dataclass
class _ModelAttempts:
    """Per-model token/cost accumulation for one execution."""

    started: int = 0
    finished: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: Decimal = Decimal(0)
    cost_known: bool = True


@dataclass(frozen=True)
class _FactKey:
    organization_id: str
    project_id: str
    app_id: str | None
    model_config_id: str | None


@dataclass
class _FactBucket:
    executions: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_unknown_executions: int = 0
    cost: Decimal = Decimal(0)
    cost_known: bool = True


@dataclass
class _DayAccumulator:
    day: date
    buckets: dict[_FactKey, _FactBucket] = field(default_factory=dict)
    storage_bytes: dict[tuple[str, str], int] = field(default_factory=dict)

    def bucket(self, key: _FactKey) -> _FactBucket:
        return self.buckets.setdefault(key, _FactBucket())

    def add_storage(self, organization_id: str, project_id: str, size_bytes: int) -> None:
        self.storage_bytes[(organization_id, project_id)] = (
            self.storage_bytes.get((organization_id, project_id), 0) + size_bytes
        )

    def _sort_key(self, key: _FactKey) -> tuple[str, str, str, str]:
        return (
            key.organization_id,
            key.project_id,
            key.app_id or "",
            key.model_config_id or "",
        )

    def rows(self) -> list[dict[str, Any]]:
        # Storage-only days (documents ingested, no executions) still need a
        # fact row so the delta is not lost. Zero executions price as a known
        # zero, not as unknown.
        represented = {(key.organization_id, key.project_id) for key in self.buckets}
        for organization_id, project_id in sorted(set(self.storage_bytes) - represented):
            self.bucket(_FactKey(organization_id, project_id, None, None))
        facts: list[dict[str, Any]] = []
        for key in sorted(self.buckets, key=self._sort_key):
            bucket = self.buckets[key]
            storage = (
                self.storage_bytes.get((key.organization_id, key.project_id), 0)
                if key.app_id is None and key.model_config_id is None
                else 0
            )
            facts.append(
                {
                    "organization_id": key.organization_id,
                    "project_id": key.project_id,
                    "app_id": key.app_id,
                    "model_config_id": key.model_config_id,
                    "day": self.day,
                    "executions": bucket.executions,
                    "prompt_tokens": bucket.prompt_tokens,
                    "completion_tokens": bucket.completion_tokens,
                    "total_tokens": bucket.total_tokens,
                    "cost_unknown_executions": bucket.cost_unknown_executions,
                    "storage_bytes_delta": storage,
                    # No retrieval counter is instrumented yet (documented gap).
                    "retrievals": 0,
                    "estimated_cost_usd": (
                        format(bucket.cost.quantize(COST_QUANTUM_USD), "f")
                        if bucket.cost_known
                        else None
                    ),
                }
            )
        return facts


def _agent_models_by_path(dsl: WorkflowDSL) -> dict[tuple[str, ...], str]:
    """Map canonical agent node paths to their model ids (estimator parity)."""
    models: dict[tuple[str, ...], str] = {}
    for located in iter_located_workflow_nodes(dsl):
        if located.node.type == NodeType.AGENT:
            models[located.segments] = AgentConfig.model_validate(
                located.node.config or {}
            ).model_config_id
    return models


def _resolve_agent_model(
    payload: dict[str, Any],
    node_id: str,
    agent_models: dict[tuple[str, ...], str],
    agent_models_by_joined: dict[str, str],
) -> str | None:
    raw_segments = payload.get("node_path_segments")
    if (
        isinstance(raw_segments, list)
        and all(isinstance(item, str) for item in raw_segments)
        and tuple(raw_segments) in agent_models
    ):
        return agent_models[tuple(raw_segments)]
    structured_path = payload.get("node_path")
    if isinstance(structured_path, str) and structured_path in agent_models_by_joined:
        return agent_models_by_joined[structured_path]
    if node_id in agent_models_by_joined:
        return agent_models_by_joined[node_id]
    normalized = re.sub(r"\[\d+\]", "", node_id)
    if normalized in agent_models_by_joined:
        return agent_models_by_joined[normalized]
    return None


async def aggregate_day(
    session: AsyncSession,
    *,
    day: date,
    now: datetime | None = None,
) -> int:
    """Recompute and durably rewrite all usage facts for one UTC day.

    Only executions that reached a terminal state are counted; an execution
    still running is picked up by a later sweep of the same day once it
    finishes (the scheduler's lookback window). Returns the number of fact
    rows written.
    """
    window_start = datetime.combine(day, time.min, tzinfo=UTC)
    window_end = window_start + timedelta(days=1)
    current = _as_utc(now or datetime.now(UTC))
    if window_end > current:
        raise ValueError("cannot aggregate an incomplete UTC day")
    accumulator = _DayAccumulator(day=day)

    dsls: dict[str, WorkflowDSL] = {}
    dsl_model_ids: dict[str, set[str]] = {}
    model_configs: dict[str, Any] = {}
    requested_model_ids: set[str] = set()
    cursor: tuple[datetime, str] | None = None
    while True:
        statement = (
            select(
                Execution.id,
                Execution.started_at,
                Execution.workflow_version_id,
                WorkflowVersion.dsl_json,
                Project.organization_id,
                Project.id.label("project_id"),
                ChatSession.app_id,
            )
            .join(Workflow, Workflow.id == Execution.workflow_id)
            .join(Project, Project.id == Workflow.project_id)
            .join(WorkflowVersion, WorkflowVersion.id == Execution.workflow_version_id)
            .outerjoin(ChatSession, ChatSession.id == Execution.session_id)
            .where(
                Execution.status.in_(TERMINAL_EXECUTION_STATUSES),
                Execution.started_at >= window_start,
                Execution.started_at < window_end,
            )
        )
        if cursor is not None:
            cursor_at, cursor_id = cursor
            statement = statement.where(
                (Execution.started_at > cursor_at)
                | ((Execution.started_at == cursor_at) & (Execution.id > cursor_id))
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
        cursor = (rows[-1][1], rows[-1][0])

        needed_models: set[str] = set()
        for row in rows:
            version_id = row[2]
            if version_id in dsls:
                continue
            dsl = _validated_dsl(row[3])
            ids = set(_agent_models_by_path(dsl).values())
            dsls[version_id] = dsl
            dsl_model_ids[version_id] = ids
        for ids in dsl_model_ids.values():
            needed_models |= ids
        missing = needed_models - requested_model_ids
        if missing:
            model_configs.update(await ModelConfigRepo(session).get_many(missing))
            requested_model_ids.update(missing)

        execution_ids = [row[0] for row in rows]
        events_by_execution: dict[str, list[ExecutionEventRow]] = defaultdict(list)
        event_rows = (
            (
                await session.execute(
                    select(ExecutionEventRow)
                    .where(ExecutionEventRow.execution_id.in_(execution_ids))
                    .order_by(ExecutionEventRow.execution_id.asc(), ExecutionEventRow.seq.asc())
                )
            )
            .scalars()
            .all()
        )
        for event in event_rows:
            events_by_execution[event.execution_id].append(event)

        for execution_id, _started_at, version_id, _raw_dsl, org_id, project_id, app_id in rows:
            agent_models = _agent_models_by_path(dsls[version_id])
            agent_models_by_joined = {
                ".".join(segments): model for segments, model in agent_models.items()
            }
            attempts: dict[str, _ModelAttempts] = {}

            for event in events_by_execution.get(execution_id, []):
                payload = event.payload_json if isinstance(event.payload_json, dict) else {}
                node_id = str(event.node_id or "")
                model_id = _resolve_agent_model(
                    payload, node_id, agent_models, agent_models_by_joined
                )
                if model_id is None:
                    continue
                event_type = str(event.event_type)
                if event_type == "node_started":
                    attempts.setdefault(model_id, _ModelAttempts()).started += 1
                    continue
                if event_type != "node_finished":
                    continue
                attempt = attempts.setdefault(model_id, _ModelAttempts())
                attempt.finished += 1
                prompt, completion, total, supplied = extract_token_usage(payload.get("output"))
                attempt.prompt_tokens += prompt
                attempt.completion_tokens += completion
                attempt.total_tokens += total
                model = model_configs.get(model_id)
                prompt_rate = _decimal(getattr(model, "prompt_price_per_million_usd", None))
                completion_rate = _decimal(getattr(model, "completion_price_per_million_usd", None))
                if (
                    not supplied
                    or (prompt and prompt_rate is None)
                    or (completion and completion_rate is None)
                ):
                    attempt.cost_known = False
                    continue
                attempt.cost += (
                    Decimal(prompt) * (prompt_rate or Decimal(0))
                    + Decimal(completion) * (completion_rate or Decimal(0))
                ) / TOKENS_PER_MILLION

            if not attempts:
                # No agent activity: the execution still counts once in the
                # NULL-model bucket. Agents declared but never started price
                # as unknown only when the run failed before reaching them.
                bucket = accumulator.bucket(
                    _FactKey(org_id, project_id, app_id, model_config_id=None)
                )
                bucket.executions += 1
                if agent_models:
                    bucket.cost_known = False
                    bucket.cost_unknown_executions += 1
                continue

            for model_id, attempt in attempts.items():
                key = _FactKey(org_id, project_id, app_id, model_config_id=model_id)
                target = accumulator.bucket(key)
                target.executions += 1
                target.prompt_tokens += attempt.prompt_tokens
                target.completion_tokens += attempt.completion_tokens
                target.total_tokens += attempt.total_tokens
                known = attempt.cost_known and attempt.started <= attempt.finished
                if known:
                    target.cost += attempt.cost
                else:
                    target.cost_known = False
                    target.cost_unknown_executions += 1

    document_rows = (
        await session.execute(
            select(Project.organization_id, Project.id, Document.size_bytes)
            .join(KnowledgeBase, KnowledgeBase.id == Document.kb_id)
            .join(Project, Project.id == KnowledgeBase.project_id)
            .where(
                Document.created_at >= window_start,
                Document.created_at < window_end,
            )
        )
    ).all()
    for organization_id, project_id, size_bytes in document_rows:
        accumulator.add_storage(organization_id, project_id, int(size_bytes))

    facts = accumulator.rows()
    await UsageFactRepo(session).upsert_day(facts, day=day)
    await session.flush()
    return len(facts)


def complete_days_before(now: datetime, *, count: int) -> list[date]:
    """The ``count`` most recent complete UTC days, oldest first."""
    current = _as_utc(now)
    today = current.date()
    return [today - timedelta(days=offset) for offset in range(count, 0, -1)]


__all__ = ["TERMINAL_EXECUTION_STATUSES", "aggregate_day", "complete_days_before"]
