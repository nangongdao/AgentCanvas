"""Background orchestration for immutable, auditable evaluation runs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    EvaluationCaseResult,
    EvaluationComparison,
    EvaluationRun,
    ModelConfig,
    WorkflowVersion,
)
from app.db.repositories import (
    EvaluationComparisonRepo,
    EvaluationDatasetRepo,
    EvaluationRunRepo,
    ExecutionRepo,
)
from app.engine.dsl_traversal import iter_workflow_nodes
from app.engine.executor import ExecutionEngine
from app.schemas.dsl import NodeType, WorkflowDSL
from app.schemas.evaluation import EvaluationComparisonCreate, EvaluationRunCreate
from app.services.evaluation_costs import estimate_execution_cost
from app.services.evaluation_reports import build_comparison_summary, summarize_run
from app.services.evaluators import evaluate_deterministic, evaluate_with_llm, resolve_actual
from app.services.execution_rerun import node_side_effect_reason
from app.services.rag_evaluation import (
    assert_rag_corpus_unchanged,
    evaluate_rag_events,
    prepare_rag_config,
    validate_rag_cases,
)


class EvaluationManager:
    """Own evaluation tasks separately from workflow execution bookkeeping."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        execution_engine: ExecutionEngine,
    ) -> None:
        self.session_factory = session_factory
        self.execution_engine = execution_engine
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._comparison_tasks: dict[str, asyncio.Task[None]] = {}
        self._shutting_down = False

    async def recover_interrupted(self) -> int:
        message = "backend restarted before evaluation completed"
        async with self.session_factory() as session:
            active_result = await session.execute(
                select(EvaluationRun.id).where(EvaluationRun.status.in_(("queued", "running")))
            )
            active_ids = list(active_result.scalars())
            recovered = 0
            if active_ids:
                result = await session.execute(
                    update(EvaluationRun)
                    .where(EvaluationRun.id.in_(active_ids))
                    .values(status="failed", error=message, finished_at=datetime.now(UTC))
                )
                recovered += int(getattr(result, "rowcount", 0) or 0)
                await session.execute(
                    update(EvaluationCaseResult)
                    .where(
                        EvaluationCaseResult.run_id.in_(active_ids),
                        EvaluationCaseResult.status.in_(("pending", "running")),
                    )
                    .values(status="error", message=message, finished_at=datetime.now(UTC))
                )
            comparison_result = await session.execute(
                update(EvaluationComparison)
                .where(EvaluationComparison.status.in_(("queued", "running")))
                .values(status="failed", error=message, finished_at=datetime.now(UTC))
            )
            recovered += int(getattr(comparison_result, "rowcount", 0) or 0)
            await session.commit()
            return recovered

    async def create_run(self, body: EvaluationRunCreate) -> EvaluationRun:
        if self._shutting_down:
            raise RuntimeError("evaluation manager is shutting down")
        async with self.session_factory() as session:
            row, _version = await self._create_run_row(session, body)
            await session.commit()
            run_id = row.id
        self._schedule_run(run_id)
        async with self.session_factory() as session:
            return (await EvaluationRunRepo(session).get(run_id)) or row

    async def create_comparison(self, body: EvaluationComparisonCreate) -> EvaluationComparison:
        if self._shutting_down:
            raise RuntimeError("evaluation manager is shutting down")
        async with self.session_factory() as session:
            run_a, version_a = await self._create_run_row(
                session, body.run_input(body.workflow_version_a_id)
            )
            run_b, version_b = await self._create_run_row(
                session, body.run_input(body.workflow_version_b_id)
            )
            if version_a.workflow_id != version_b.workflow_id:
                raise ValueError("comparison requires versions of the same workflow")
            comparison = await EvaluationComparisonRepo(session).create(
                dataset_version_id=body.dataset_version_id,
                variant_a_run_id=run_a.id,
                variant_b_run_id=run_b.id,
            )
            await session.commit()
            comparison_id = comparison.id
            run_ids = (run_a.id, run_b.id)
        for run_id in run_ids:
            self._schedule_run(run_id)
        self._schedule_comparison(comparison_id)
        async with self.session_factory() as session:
            row = await EvaluationComparisonRepo(session).get(comparison_id)
            if row is None:
                raise RuntimeError("evaluation comparison disappeared after creation")
            return row

    async def _create_run_row(
        self, session: AsyncSession, body: EvaluationRunCreate
    ) -> tuple[EvaluationRun, WorkflowVersion]:
        dataset_version = await EvaluationDatasetRepo(session).get_version(body.dataset_version_id)
        if dataset_version is None:
            raise KeyError("evaluation dataset version not found")
        workflow_version = await session.get(WorkflowVersion, body.workflow_version_id)
        if workflow_version is None:
            raise KeyError("workflow version not found")
        if workflow_version.published_at is None:
            raise ValueError("evaluation requires a version that has been published")
        dsl = WorkflowDSL.model_validate(workflow_version.dsl_json)
        self._validate_workflow_policy(dsl, allow_side_effects=body.allow_side_effects)
        if body.evaluator_type == "llm_judge":
            model = await session.get(ModelConfig, body.model_config_id)
            if model is None or model.kind != "chat":
                raise ValueError("LLM judge requires an existing chat model")
        config = body.evaluator_config()
        if body.evaluator_type == "rag":
            validate_rag_cases(list(dataset_version.cases_json or []))
            config = await prepare_rag_config(session, dsl, config)
        config["allow_side_effects"] = body.allow_side_effects
        row = await EvaluationRunRepo(session).create(
            dataset_version_id=dataset_version.id,
            workflow_version_id=workflow_version.id,
            evaluator_type=body.evaluator_type,
            evaluator_config=config,
            cases=list(dataset_version.cases_json or []),
        )
        return row, workflow_version

    def _validate_workflow_policy(self, dsl: WorkflowDSL, *, allow_side_effects: bool) -> None:
        for node in iter_workflow_nodes(dsl):
            if node.type == NodeType.HUMAN:
                raise ValueError(
                    f"evaluation blocked at node '{node.id}': human approval cannot run unattended"
                )
            reason = node_side_effect_reason(node)
            if reason and not allow_side_effects:
                raise ValueError(
                    f"evaluation blocked at node '{node.id}': {reason}; explicit opt-in required"
                )

    def _schedule_run(self, run_id: str) -> None:
        task = asyncio.create_task(self._execute(run_id), name=f"evaluation-{run_id}")
        self._tasks[run_id] = task

        def forget(_task: asyncio.Task[None], rid: str = run_id) -> None:
            self._tasks.pop(rid, None)

        task.add_done_callback(forget)

    def _schedule_comparison(self, comparison_id: str) -> None:
        task = asyncio.create_task(
            self._execute_comparison(comparison_id),
            name=f"evaluation-comparison-{comparison_id}",
        )
        self._comparison_tasks[comparison_id] = task

        def forget(_task: asyncio.Task[None], cid: str = comparison_id) -> None:
            self._comparison_tasks.pop(cid, None)

        task.add_done_callback(forget)

    async def _execute(self, run_id: str) -> None:
        try:
            async with self.session_factory() as session:
                run = await EvaluationRunRepo(session).get(run_id)
                if run is None:
                    return
                run.status = "running"
                run.started_at = datetime.now(UTC)
                case_ids = [row.id for row in run.case_results]
                workflow_version_id = run.workflow_version_id
                evaluator_type = run.evaluator_type
                config = dict(run.evaluator_config_json or {})
                await session.commit()

            for case_result_id in case_ids:
                await self._execute_case(
                    run_id,
                    case_result_id,
                    workflow_version_id=workflow_version_id,
                    evaluator_type=evaluator_type,
                    config=config,
                )
            await self._finish_run(run_id)
        except asyncio.CancelledError:
            await self._fail_run(run_id, "evaluation cancelled", status="cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - durable background failure boundary
            await self._fail_run(run_id, str(exc))

    async def _execute_case(
        self,
        run_id: str,
        case_result_id: str,
        *,
        workflow_version_id: str,
        evaluator_type: str,
        config: dict[str, Any],
    ) -> None:
        async with self.session_factory() as session:
            row = await session.get(EvaluationCaseResult, case_result_id)
            if row is None:
                return
            row.status = "running"
            inputs = dict(row.input_json or {})
            expected = row.expected_json
            case_id = row.case_id
            await session.commit()

        duration_ms: int | None = None
        try:
            if evaluator_type == "rag":
                corpus = config.get("rag_corpus")
                if not isinstance(corpus, dict):
                    raise ValueError("RAG evaluator config is missing its corpus snapshot")
                async with self.session_factory() as session:
                    await assert_rag_corpus_unchanged(session, corpus)
            execution_id = await self.execution_engine.start_version(
                workflow_version_id,
                inputs,
                idempotency_key=f"evaluation:{run_id}:{case_id}",
            )
            async with self.session_factory() as session:
                row = await session.get(EvaluationCaseResult, case_result_id)
                if row is not None:
                    row.execution_id = execution_id
                    await session.commit()
            execution = await self._wait_for_execution(execution_id)
            duration_ms = self._duration_ms(execution.started_at, execution.finished_at)
            await self._snapshot_cost(
                case_result_id,
                execution_id=execution_id,
                workflow_version_id=workflow_version_id,
            )
            if execution.status != "succeeded":
                raise ValueError(execution.error or f"workflow ended with {execution.status}")
            if evaluator_type == "rag":
                async with self.session_factory() as session:
                    events = await ExecutionRepo(session).list_events_after(execution_id)
                outcome, actual = evaluate_rag_events(
                    events,
                    expected=expected,
                    config=config,
                )
            else:
                actual = resolve_actual(execution.output_json, str(config.get("actual_path") or ""))
            if evaluator_type == "llm_judge":
                provider = await self.execution_engine.load_provider(str(config["model_config_id"]))
                outcome = await evaluate_with_llm(
                    provider,
                    actual=actual,
                    expected=expected,
                    rubric=str(config.get("rubric") or ""),
                    threshold=float(config.get("threshold", 0.5)),
                )
            elif evaluator_type != "rag":
                outcome = evaluate_deterministic(
                    evaluator_type,
                    actual=actual,
                    expected=expected,
                    case_sensitive=bool(config.get("case_sensitive", True)),
                )
            async with self.session_factory() as session:
                row = await session.get(EvaluationCaseResult, case_result_id)
                if row is not None:
                    row.actual_json = actual
                    row.status = "passed" if outcome.passed else "failed"
                    row.score = outcome.score
                    row.message = outcome.message
                    row.duration_ms = duration_ms
                    row.finished_at = datetime.now(UTC)
                    await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad case must not abort the dataset
            async with self.session_factory() as session:
                row = await session.get(EvaluationCaseResult, case_result_id)
                if row is not None:
                    row.status = "error"
                    row.message = str(exc)[:4000]
                    row.duration_ms = duration_ms
                    row.finished_at = datetime.now(UTC)
                    await session.commit()

    async def _snapshot_cost(
        self,
        case_result_id: str,
        *,
        execution_id: str,
        workflow_version_id: str,
    ) -> None:
        async with self.session_factory() as session:
            version = await session.get(WorkflowVersion, workflow_version_id)
            if version is None:
                return
            events = await ExecutionRepo(session).list_events_after(execution_id)
            model_result = await session.execute(select(ModelConfig))
            models = {row.id: row for row in model_result.scalars()}
            cost = estimate_execution_cost(
                events,
                dsl=WorkflowDSL.model_validate(version.dsl_json),
                model_configs=models,
            )
            row = await session.get(EvaluationCaseResult, case_result_id)
            if row is None:
                return
            row.prompt_tokens = cost.prompt_tokens
            row.completion_tokens = cost.completion_tokens
            row.total_tokens = cost.total_tokens
            row.estimated_cost_usd = cost.estimated_cost_usd
            row.cost_known = cost.cost_known
            row.cost_error_bound_usd = cost.cost_error_bound_usd
            row.price_versions_json = list(cost.price_versions)
            await session.commit()

    async def _wait_for_execution(self, execution_id: str):
        while True:
            async with self.session_factory() as session:
                row = await ExecutionRepo(session).get(execution_id)
                if row is None:
                    raise ValueError("evaluation execution disappeared")
                terminal = row.status not in {"running", "queued"}
            if terminal:
                await self.execution_engine.event_bus.flush()
                return row
            await asyncio.sleep(0.05)

    async def _finish_run(self, run_id: str) -> None:
        async with self.session_factory() as session:
            run = await EvaluationRunRepo(session).get(run_id)
            if run is None:
                return
            run.summary_json = summarize_run(run.case_results)
            run.status = "completed"
            run.finished_at = datetime.now(UTC)
            await session.commit()

    async def _execute_comparison(self, comparison_id: str) -> None:
        try:
            async with self.session_factory() as session:
                repo = EvaluationComparisonRepo(session)
                row = await repo.get(comparison_id)
                if row is None:
                    return
                await repo.mark_running(row)
                await session.commit()

            while True:
                async with self.session_factory() as session:
                    row = await EvaluationComparisonRepo(session).get(comparison_id)
                    if row is None:
                        return
                    statuses = (row.variant_a_run.status, row.variant_b_run.status)
                    if all(status == "completed" for status in statuses):
                        summary = build_comparison_summary(row.variant_a_run, row.variant_b_run)
                        await EvaluationComparisonRepo(session).mark_completed(row, summary)
                        await session.commit()
                        return
                    if any(status in {"failed", "cancelled"} for status in statuses):
                        errors = [
                            run.error or run.status
                            for run in (row.variant_a_run, row.variant_b_run)
                            if run.status in {"failed", "cancelled"}
                        ]
                        raise ValueError("; ".join(errors))
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            await self._fail_comparison(comparison_id, "comparison cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - durable background boundary
            await self._fail_comparison(comparison_id, str(exc))

    async def _fail_comparison(self, comparison_id: str, error: str) -> None:
        async with self.session_factory() as session:
            repo = EvaluationComparisonRepo(session)
            row = await repo.get(comparison_id)
            if row is not None:
                await repo.mark_failed(row, error)
                await session.commit()

    async def _fail_run(self, run_id: str, error: str, *, status: str = "failed") -> None:
        async with self.session_factory() as session:
            row = await session.get(EvaluationRun, run_id)
            if row is not None:
                row.status = status
                row.error = error[:4000]
                row.finished_at = datetime.now(UTC)
                await session.commit()

    @staticmethod
    def _duration_ms(started_at: datetime | None, finished_at: datetime | None) -> int | None:
        if started_at is None or finished_at is None:
            return None
        return max(0, round((finished_at - started_at).total_seconds() * 1000))

    async def shutdown(self, grace_period: float = 15.0) -> None:
        self._shutting_down = True
        tasks = [*self._tasks.values(), *self._comparison_tasks.values()]
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=grace_period)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.exception() if not task.cancelled() else None


__all__ = ["EvaluationManager"]
