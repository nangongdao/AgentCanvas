"""Persistence helpers for versioned datasets and evaluation reports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    EvaluationCaseResult,
    EvaluationComparison,
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationRun,
)
from app.db.pagination import PageSlice, PageSpec, paginate_select


class EvaluationDatasetRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_page(self, spec: PageSpec) -> PageSlice[EvaluationDataset]:
        return await paginate_select(
            self.session,
            select(EvaluationDataset).options(selectinload(EvaluationDataset.versions)),
            id_column=EvaluationDataset.id,
            columns={
                "created_at": EvaluationDataset.created_at,
                "updated_at": EvaluationDataset.updated_at,
                "name": EvaluationDataset.name,
                "id": EvaluationDataset.id,
            },
            spec=spec,
            search_columns=(
                EvaluationDataset.name,
                EvaluationDataset.description,
                EvaluationDataset.id,
            ),
        )

    async def get(self, dataset_id: str) -> EvaluationDataset | None:
        result = await self.session.execute(
            select(EvaluationDataset)
            .options(selectinload(EvaluationDataset.versions))
            .where(EvaluationDataset.id == dataset_id)
        )
        return result.scalar_one_or_none()

    async def get_version(self, version_id: str) -> EvaluationDatasetVersion | None:
        return await self.session.get(EvaluationDatasetVersion, version_id)

    async def create(
        self,
        *,
        name: str,
        description: str,
        cases: list[dict[str, Any]],
        change_summary: str,
    ) -> EvaluationDataset:
        row = EvaluationDataset(name=name, description=description, current_version=1)
        self.session.add(row)
        await self.session.flush()
        self.session.add(
            EvaluationDatasetVersion(
                dataset_id=row.id,
                number=1,
                cases_json=cases,
                change_summary=change_summary,
            )
        )
        await self.session.flush()
        return (await self.get(row.id)) or row

    async def update(
        self,
        row: EvaluationDataset,
        *,
        name: str,
        description: str,
        cases: list[dict[str, Any]],
        change_summary: str,
    ) -> EvaluationDataset:
        row.name = name
        row.description = description
        row.current_version += 1
        row.updated_at = datetime.now(UTC)
        row.versions.append(
            EvaluationDatasetVersion(
                number=row.current_version,
                cases_json=cases,
                change_summary=change_summary,
            )
        )
        await self.session.flush()
        return row

    async def delete(self, row: EvaluationDataset) -> None:
        await self.session.delete(row)
        await self.session.flush()


class EvaluationRunRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_page(self, spec: PageSpec) -> PageSlice[EvaluationRun]:
        return await paginate_select(
            self.session,
            self._detail_statement(),
            id_column=EvaluationRun.id,
            columns={
                "created_at": EvaluationRun.created_at,
                "status": EvaluationRun.status,
                "evaluator_type": EvaluationRun.evaluator_type,
                "id": EvaluationRun.id,
            },
            spec=spec,
            search_columns=(
                EvaluationRun.id,
                EvaluationRun.status,
                EvaluationRun.evaluator_type,
            ),
        )

    async def get(self, run_id: str) -> EvaluationRun | None:
        result = await self.session.execute(
            self._detail_statement().where(EvaluationRun.id == run_id)
        )
        return result.scalar_one_or_none()

    async def get_latest_completed(
        self, dataset_version_id: str, workflow_version_id: str
    ) -> EvaluationRun | None:
        """Most recent completed run for a dataset version + workflow version.

        Used by the publish gate to check pass rate against the configured
        threshold (Backlog: eval gate on publish).
        """
        result = await self.session.execute(
            self._detail_statement()
            .where(
                EvaluationRun.dataset_version_id == dataset_version_id,
                EvaluationRun.workflow_version_id == workflow_version_id,
                EvaluationRun.status == "completed",
            )
            .order_by(EvaluationRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    def _detail_statement(self):
        return select(EvaluationRun).options(
            selectinload(EvaluationRun.case_results),
            selectinload(EvaluationRun.dataset_version).selectinload(
                EvaluationDatasetVersion.dataset
            ),
            selectinload(EvaluationRun.workflow_version),
        )

    async def create(
        self,
        *,
        dataset_version_id: str,
        workflow_version_id: str,
        evaluator_type: str,
        evaluator_config: dict[str, Any],
        cases: list[dict[str, Any]],
    ) -> EvaluationRun:
        row = EvaluationRun(
            dataset_version_id=dataset_version_id,
            workflow_version_id=workflow_version_id,
            evaluator_type=evaluator_type,
            evaluator_config_json=evaluator_config,
            status="queued",
        )
        self.session.add(row)
        await self.session.flush()
        for index, case in enumerate(cases):
            self.session.add(
                EvaluationCaseResult(
                    run_id=row.id,
                    case_id=str(case["id"]),
                    case_index=index,
                    name=str(case.get("name") or ""),
                    input_json=dict(case.get("inputs") or {}),
                    expected_json=case.get("expected"),
                )
            )
        await self.session.flush()
        return row


class EvaluationComparisonRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_page(self, spec: PageSpec) -> PageSlice[EvaluationComparison]:
        return await paginate_select(
            self.session,
            self._detail_statement(),
            id_column=EvaluationComparison.id,
            columns={
                "created_at": EvaluationComparison.created_at,
                "status": EvaluationComparison.status,
                "id": EvaluationComparison.id,
            },
            spec=spec,
            search_columns=(
                EvaluationComparison.id,
                EvaluationComparison.status,
            ),
        )

    async def get(self, comparison_id: str) -> EvaluationComparison | None:
        result = await self.session.execute(
            self._detail_statement().where(EvaluationComparison.id == comparison_id)
        )
        return result.scalar_one_or_none()

    def _detail_statement(self):
        return select(EvaluationComparison).options(
            selectinload(EvaluationComparison.dataset_version).selectinload(
                EvaluationDatasetVersion.dataset
            ),
            selectinload(EvaluationComparison.variant_a_run).selectinload(
                EvaluationRun.case_results
            ),
            selectinload(EvaluationComparison.variant_a_run).selectinload(
                EvaluationRun.workflow_version
            ),
            selectinload(EvaluationComparison.variant_a_run)
            .selectinload(EvaluationRun.dataset_version)
            .selectinload(EvaluationDatasetVersion.dataset),
            selectinload(EvaluationComparison.variant_b_run).selectinload(
                EvaluationRun.case_results
            ),
            selectinload(EvaluationComparison.variant_b_run).selectinload(
                EvaluationRun.workflow_version
            ),
            selectinload(EvaluationComparison.variant_b_run)
            .selectinload(EvaluationRun.dataset_version)
            .selectinload(EvaluationDatasetVersion.dataset),
        )

    async def create(
        self,
        *,
        dataset_version_id: str,
        variant_a_run_id: str,
        variant_b_run_id: str,
    ) -> EvaluationComparison:
        row = EvaluationComparison(
            dataset_version_id=dataset_version_id,
            variant_a_run_id=variant_a_run_id,
            variant_b_run_id=variant_b_run_id,
            status="queued",
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def mark_running(self, row: EvaluationComparison) -> None:
        row.status = "running"
        row.started_at = datetime.now(UTC)
        await self.session.flush()

    async def mark_completed(self, row: EvaluationComparison, summary: dict[str, Any]) -> None:
        row.status = "completed"
        row.summary_json = summary
        row.error = None
        row.finished_at = datetime.now(UTC)
        await self.session.flush()

    async def mark_failed(self, row: EvaluationComparison, error: str) -> None:
        row.status = "failed"
        row.error = error[:4000]
        row.finished_at = datetime.now(UTC)
        await self.session.flush()


__all__ = ["EvaluationComparisonRepo", "EvaluationDatasetRepo", "EvaluationRunRepo"]
