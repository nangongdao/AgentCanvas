"""Atomic project quota policy and reconciliation tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import (
    Document,
    KnowledgeBase,
    Organization,
    ProjectQuotaReservation,
)
from app.db.repositories import ExecutionRepo, ProjectRepo, WorkflowRepo
from app.services.project_quotas import (
    ProjectQuotaExceeded,
    ProjectQuotaReservationConflict,
    ProjectQuotaService,
)

SessionFactory = async_sessionmaker[AsyncSession]


async def _database(tmp_path: Path) -> tuple[AsyncEngine, SessionFactory, str]:
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    async with sessions() as session:
        organization = Organization(name="Quota Org", slug="quota-org")
        session.add(organization)
        await session.flush()
        project = await ProjectRepo(session).create(organization.id, "Quota Project", "quota")
        await session.commit()
        return engine, sessions, project.id


async def _configure(sessions: SessionFactory, project_id: str, **limits: int | None) -> None:
    async with sessions() as session:
        await ProjectQuotaService(session).configure(project_id, **limits)
        await session.commit()


async def _reserve_once(
    sessions: SessionFactory,
    project_id: str,
    kind: Any,
    resource_id: str,
    amount: int,
) -> tuple[str, bool]:
    async with sessions() as session:
        try:
            created = await ProjectQuotaService(session).reserve(
                project_id, kind, resource_id, amount
            )
            await session.commit()
            return resource_id, created
        except ProjectQuotaExceeded:
            await session.rollback()
            return resource_id, False


@pytest.mark.parametrize(
    ("kind", "limit_name", "usage_name", "limit", "amount", "expected_count"),
    [
        ("execution", "concurrent_execution_limit", "concurrent_executions", 3, 1, 3),
        ("document_storage", "storage_bytes_limit", "storage_bytes", 10, 4, 2),
        ("stdio_mcp_process", "stdio_mcp_process_limit", "stdio_mcp_processes", 2, 1, 2),
    ],
)
async def test_realtime_reservations_are_atomic_and_idempotent(
    tmp_path: Path,
    kind: Any,
    limit_name: str,
    usage_name: str,
    limit: int,
    amount: int,
    expected_count: int,
) -> None:
    engine, sessions, project_id = await _database(tmp_path)
    try:
        await _configure(sessions, project_id, **{limit_name: limit})
        outcomes = await asyncio.gather(
            *(
                _reserve_once(sessions, project_id, kind, f"resource-{index}", amount)
                for index in range(8)
            )
        )
        accepted = [resource_id for resource_id, created in outcomes if created]
        assert len(accepted) == expected_count

        async with sessions() as session:
            service = ProjectQuotaService(session)
            snapshot = await service.snapshot(project_id)
            assert getattr(snapshot, usage_name) == expected_count * amount
            reservation_count = await session.scalar(
                select(func.count(ProjectQuotaReservation.id)).where(
                    ProjectQuotaReservation.project_id == project_id,
                    ProjectQuotaReservation.kind == kind,
                )
            )
            assert reservation_count == expected_count
            assert not await service.reserve(project_id, kind, accepted[0], amount)
            with pytest.raises(ProjectQuotaReservationConflict):
                await service.reserve(project_id, kind, accepted[0], amount + 1)
            assert await service.release(project_id, kind, accepted[0])
            assert not await service.release(project_id, kind, accepted[0])
            await session.commit()

        async with sessions() as session:
            snapshot = await ProjectQuotaService(session).snapshot(project_id)
            assert getattr(snapshot, usage_name) == (expected_count - 1) * amount
    finally:
        await engine.dispose()


async def _charge_once(
    sessions: SessionFactory,
    project_id: str,
    kind: Any,
    amount: int,
) -> bool:
    async with sessions() as session:
        try:
            await ProjectQuotaService(session).charge_monthly(project_id, kind, amount)
            await session.commit()
            return True
        except ProjectQuotaExceeded:
            await session.rollback()
            return False


@pytest.mark.parametrize(
    ("kind", "limit_name", "usage_name"),
    [
        (
            "embedding_input_bytes",
            "monthly_embedding_input_bytes_limit",
            "embedding_input_bytes",
        ),
        ("model_cost_units", "monthly_model_cost_units_limit", "model_cost_units"),
    ],
)
async def test_monthly_usage_is_atomic_under_concurrent_charges(
    tmp_path: Path,
    kind: Any,
    limit_name: str,
    usage_name: str,
) -> None:
    engine, sessions, project_id = await _database(tmp_path)
    try:
        await _configure(sessions, project_id, **{limit_name: 10})
        results = await asyncio.gather(
            *(_charge_once(sessions, project_id, kind, 4) for _index in range(8))
        )
        assert sum(results) == 2
        async with sessions() as session:
            service = ProjectQuotaService(session)
            snapshot = await service.snapshot(project_id)
            assert getattr(snapshot, usage_name) == 8
            with pytest.raises(ProjectQuotaExceeded):
                await service.charge_monthly(project_id, kind, 4)
            snapshot = await service.snapshot(project_id)
            assert getattr(snapshot, usage_name) == 8
    finally:
        await engine.dispose()


async def test_model_cost_can_record_unavoidable_overage(tmp_path: Path) -> None:
    engine, sessions, project_id = await _database(tmp_path)
    try:
        await _configure(sessions, project_id, monthly_model_cost_units_limit=10)
        async with sessions() as session:
            service = ProjectQuotaService(session)
            await service.charge_monthly(project_id, "model_cost_units", 8)
            await service.charge_monthly(project_id, "model_cost_units", 4, allow_overage=True)
            with pytest.raises(ProjectQuotaExceeded):
                await service.charge_monthly(project_id, "model_cost_units", 1)
            assert (await service.snapshot(project_id)).model_cost_units == 12
            await session.commit()
    finally:
        await engine.dispose()


async def test_reconciliation_rebuilds_authoritative_realtime_usage(tmp_path: Path) -> None:
    engine, sessions, project_id = await _database(tmp_path)
    dsl = {"version": "1.0", "name": "Quota", "nodes": [], "edges": []}
    try:
        async with sessions() as session:
            workflow = await WorkflowRepo(session).create("Quota", dsl, project_id=project_id)
            running = await ExecutionRepo(session).create(workflow.id, {})
            waiting = await ExecutionRepo(session).create(workflow.id, {})
            waiting.status = "waiting_approval"
            knowledge_base = KnowledgeBase(
                project_id=project_id,
                name="Project KB",
                embedding_model_id="default-embedding",
            )
            session.add(knowledge_base)
            await session.flush()
            document = Document(
                kb_id=knowledge_base.id,
                filename="quota.txt",
                file_path="project/quota.txt",
                mime_type="text/plain",
                size_bytes=17,
                content_sha256="a" * 64,
            )
            session.add(document)
            await session.flush()
            service = ProjectQuotaService(session)
            await service.reserve(project_id, "execution", "stale-execution")
            await service.reserve(project_id, "stdio_mcp_process", "stale-mcp")
            await session.commit()
            running_id = running.id
            document_id = document.id

        async with sessions() as session:
            assert await ProjectQuotaService(session).reconcile(project_id) == 1
            await session.commit()

        async with sessions() as session:
            service = ProjectQuotaService(session)
            snapshot = await service.snapshot(project_id)
            assert snapshot.concurrent_executions == 1
            assert snapshot.storage_bytes == 17
            assert snapshot.stdio_mcp_processes == 0
            reservations = list(
                (
                    await session.scalars(
                        select(ProjectQuotaReservation).where(
                            ProjectQuotaReservation.project_id == project_id
                        )
                    )
                ).all()
            )
            assert {(row.kind, row.resource_id, row.amount) for row in reservations} == {
                ("execution", running_id, 1),
                ("document_storage", document_id, 17),
            }
    finally:
        await engine.dispose()
