"""Per-application usage aggregation for the app usage view (C3-5).

Sessions, messages, and feedback aggregate directly over chat tables scoped by
``chat_sessions.app_id``. Token and cost attribution reuses the D2 cost
governance estimator over each execution's persisted events, so an app's usage
is priced with exactly the same rules as evaluations and the cost pages:
missing provider usage or missing rates make the total unknown instead of
silently becoming zero.

Window semantics: every metric counts rows whose own timestamp falls inside the
trailing ``days`` window — sessions by ``created_at``, messages and feedback by
their ``created_at``, executions by ``started_at``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    App,
    ChatMessageFeedback,
    ChatMessageRow,
    ChatSession,
    Execution,
    ExecutionEventRow,
)
from app.db.repositories.model_configs import ModelConfigRepo
from app.engine.dsl_traversal import iter_located_workflow_nodes
from app.schemas.dsl import AgentConfig, NodeType, WorkflowDSL
from app.services.chat_citations import citation_reference_counts
from app.services.evaluation_costs import COST_QUANTUM_USD, estimate_execution_cost

MAX_USAGE_DAYS = 90
_FALLBACK_DSL = WorkflowDSL.model_validate({"version": "1.0", "name": "unknown"})


@dataclass(frozen=True)
class AppUsageDay:
    date: str
    sessions: int = 0
    messages: int = 0
    executions: int = 0
    total_tokens: int = 0
    estimated_cost_usd: str | None = None


@dataclass
class AppUsage:
    app_id: str
    days: int
    since: datetime
    sessions: int = 0
    user_messages: int = 0
    assistant_messages: int = 0
    executions: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: str | None = None
    cost_known: bool = True
    positive_feedback: int = 0
    negative_feedback: int = 0
    available_citations: int = 0
    referenced_citations: int = 0
    daily: list[AppUsageDay] = field(default_factory=list)

    @property
    def feedback_rate(self) -> float | None:
        """Share of positive ratings; None when no feedback exists yet."""
        total = self.positive_feedback + self.negative_feedback
        if total == 0:
            return None
        return round(self.positive_feedback / total, 4)

    @property
    def citation_coverage(self) -> float | None:
        """Share of available source labels referenced by assistant answers."""
        if self.available_citations == 0:
            return None
        return round(self.referenced_citations / self.available_citations, 4)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _day_key(value: datetime | None) -> str:
    return (value or _utcnow()).astimezone(UTC).date().isoformat()


class _DailyAccumulator:
    """Mutable per-day buckets converted to frozen rows at the end."""

    def __init__(self) -> None:
        self.days: dict[str, dict[str, Any]] = {}

    def _bucket(self, key: str) -> dict[str, Any]:
        return self.days.setdefault(
            key,
            {
                "sessions": 0,
                "messages": 0,
                "executions": 0,
                "total_tokens": 0,
                "cost": Decimal(0),
                "cost_known": True,
            },
        )

    def add_session(self, moment: datetime | None) -> None:
        self._bucket(_day_key(moment))["sessions"] += 1

    def add_message(self, moment: datetime | None) -> None:
        self._bucket(_day_key(moment))["messages"] += 1

    def add_execution(
        self, moment: datetime | None, total_tokens: int, cost: Decimal | None
    ) -> None:
        bucket = self._bucket(_day_key(moment))
        bucket["executions"] += 1
        bucket["total_tokens"] += total_tokens
        if cost is None:
            bucket["cost_known"] = False
        else:
            bucket["cost"] += cost

    def rows(self) -> list[AppUsageDay]:
        return [
            AppUsageDay(
                date=key,
                sessions=bucket["sessions"],
                messages=bucket["messages"],
                executions=bucket["executions"],
                total_tokens=bucket["total_tokens"],
                estimated_cost_usd=(
                    format(bucket["cost"].quantize(COST_QUANTUM_USD), "f")
                    if bucket["cost_known"]
                    else None
                ),
            )
            for key, bucket in sorted(self.days.items())
        ]


def _agent_model_ids(dsl: WorkflowDSL) -> set[str]:
    """Return the agent model ids referenced by a DSL."""
    models: set[str] = set()
    for located in iter_located_workflow_nodes(dsl):
        if located.node.type == NodeType.AGENT:
            models.add(AgentConfig.model_validate(located.node.config or {}).model_config_id)
    return models


def _validated_dsl(raw: Any) -> WorkflowDSL:
    if not isinstance(raw, dict):
        return _FALLBACK_DSL
    try:
        return WorkflowDSL.model_validate(raw)
    except Exception:  # noqa: BLE001 - a broken snapshot must not break the
        # whole usage view; its tokens still count with unknown cost.
        return _FALLBACK_DSL


async def collect_app_usage(session: AsyncSession, app: App, *, days: int) -> AppUsage:
    """Aggregate the app's usage over the trailing ``days`` window."""
    bounded_days = max(1, min(days, MAX_USAGE_DAYS))
    since = _utcnow() - timedelta(days=bounded_days)
    usage = AppUsage(app_id=app.id, days=bounded_days, since=since)
    daily = _DailyAccumulator()

    all_session_ids = list(
        (await session.execute(select(ChatSession.id).where(ChatSession.app_id == app.id)))
        .scalars()
        .all()
    )

    sessions_in_window = list(
        (
            await session.execute(
                select(ChatSession)
                .where(
                    ChatSession.app_id == app.id,
                    ChatSession.created_at >= since,
                )
                .order_by(ChatSession.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    usage.sessions = len(sessions_in_window)
    for row in sessions_in_window:
        daily.add_session(row.created_at)

    if all_session_ids:
        message_rows = (
            (
                await session.execute(
                    select(ChatMessageRow)
                    .where(
                        ChatMessageRow.session_id.in_(all_session_ids),
                        ChatMessageRow.created_at >= since,
                    )
                    .order_by(ChatMessageRow.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        for message in message_rows:
            daily.add_message(message.created_at)
            if message.role == "user":
                usage.user_messages += 1
            elif message.role == "assistant":
                usage.assistant_messages += 1
                referenced, available = citation_reference_counts(
                    message.content, message.citations_json
                )
                usage.available_citations += available
                usage.referenced_citations += referenced

        feedback_rows = (
            await session.execute(
                select(ChatMessageFeedback.rating, func.count())
                .where(
                    ChatMessageFeedback.session_id.in_(all_session_ids),
                    ChatMessageFeedback.created_at >= since,
                )
                .group_by(ChatMessageFeedback.rating)
            )
        ).all()
        for rating, count in feedback_rows:
            if rating == "positive":
                usage.positive_feedback += int(count)
            elif rating == "negative":
                usage.negative_feedback += int(count)

    # Attribute execution tokens/cost through the app's chat sessions; the
    # executions themselves are bounded by their own start time so a long-lived
    # visitor session still contributes today's runs to today's bucket.
    execution_rows = (
        (
            await session.execute(
                select(Execution)
                .where(
                    Execution.session_id.in_(all_session_ids or {"-"}),
                    Execution.started_at >= since,
                )
                .order_by(Execution.started_at.asc())
            )
        )
        .scalars()
        .all()
    )

    if execution_rows:
        model_ids: set[str] = set()
        dsls: dict[str, WorkflowDSL] = {}
        for execution in execution_rows:
            version = execution.workflow_version
            if version is None:
                continue
            if execution.workflow_version_id not in dsls:
                validated = _validated_dsl(version.dsl_json)
                dsls[execution.workflow_version_id] = validated
                model_ids |= _agent_model_ids(validated)
        model_configs = await ModelConfigRepo(session).get_many(model_ids)

        total_cost = Decimal(0)
        for execution in execution_rows:
            usage.executions += 1
            events = list(
                (
                    await session.execute(
                        select(ExecutionEventRow)
                        .where(ExecutionEventRow.execution_id == execution.id)
                        .order_by(ExecutionEventRow.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
            dsl: WorkflowDSL | None = dsls.get(execution.workflow_version_id)
            if dsl is None:
                # No resolvable version snapshot: tokens cannot be priced.
                usage.cost_known = False
                daily.add_execution(execution.started_at, 0, None)
                continue
            cost = estimate_execution_cost(events, dsl=dsl, model_configs=model_configs)
            usage.prompt_tokens += cost.prompt_tokens
            usage.completion_tokens += cost.completion_tokens
            usage.total_tokens += cost.total_tokens
            if cost.cost_known and cost.estimated_cost_usd is not None:
                exact = Decimal(cost.estimated_cost_usd)
                total_cost += exact
                daily.add_execution(execution.started_at, cost.total_tokens, exact)
            else:
                usage.cost_known = False
                daily.add_execution(execution.started_at, cost.total_tokens, None)

        if usage.cost_known:
            usage.estimated_cost_usd = format(total_cost.quantize(COST_QUANTUM_USD), "f")

    usage.daily.extend(daily.rows())
    return usage
