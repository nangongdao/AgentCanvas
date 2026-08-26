"""Workflow schedule API, cron, and durable dispatch coverage."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import CostAlert, Execution, ExecutionQueueItem, WorkflowSchedule
from app.db.repositories import WorkflowRepo, WorkflowScheduleRepo, WorkflowVersionRepo
from app.engine.workflow_schedule_scheduler import WorkflowScheduleScheduler
from app.main import create_app
from app.services.schedule_time import next_cron_run

EDITOR_TOKEN = "schedule-editor-token-with-more-than-16-characters"
VIEWER_TOKEN = "schedule-viewer-token-with-more-than-16-characters"


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        editor_api_token=EDITOR_TOKEN,
        viewer_api_token=VIEWER_TOKEN,
        admin_api_token="schedule-admin-token-with-more-than-16-characters",
        workflow_schedule_poll_seconds=3600,
    )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _workflow_body(*, required: bool = True) -> dict:
    return {
        "name": "Scheduled workflow",
        "dsl": {
            "version": "1.0",
            "name": "Scheduled workflow",
            "variables": [],
            "settings": {"max_loop_iterations": 20, "timeout_seconds": 30, "recursion_limit": 50},
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "position": {"x": 0, "y": 0},
                    "config": {
                        "input_schema": [
                            {"name": "message", "type": "string", "required": required}
                        ]
                    },
                },
                {"id": "end", "type": "end", "position": {"x": 200, "y": 0}},
            ],
            "edges": [{"id": "edge", "source": "start", "target": "end"}],
        },
    }


def test_schedule_api_requires_published_version_and_manages_lifecycle(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        editor = _headers(EDITOR_TOKEN)
        viewer = _headers(VIEWER_TOKEN)
        workflow = client.post("/api/workflows", headers=editor, json=_workflow_body()).json()
        url = f"/api/workflows/{workflow['id']}/schedules"
        payload = {
            "name": "weekday sync",
            "cron_expression": "0 9 * * 1-5",
            "timezone": "Asia/Shanghai",
            "inputs": {"message": "hello"},
            "misfire_policy": "catch_up",
            "failure_policy": "retry",
            "retry_delay_seconds": 30,
        }

        unpublished = client.post(url, headers=editor, json=payload)
        assert unpublished.status_code == 409
        assert (
            client.post(
                url, headers=editor, json={**payload, "timezone": "Mars/Olympus"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                url, headers=editor, json={**payload, "cron_expression": "not cron"}
            ).status_code
            == 422
        )
        assert (
            client.post(f"/api/workflows/{workflow['id']}/publish", headers=editor).status_code
            == 200
        )

        created = client.post(url, headers=editor, json=payload)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["published_version_number"] == 1
        assert body["timezone"] == "Asia/Shanghai"
        assert body["status"] == "active"
        assert client.post(url, headers=editor, json=payload).status_code == 409
        assert client.get(url, headers=viewer).json()[0]["id"] == body["id"]

        disabled = client.put(f"{url}/{body['id']}", headers=editor, json={"enabled": False})
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "disabled"
        assert client.delete(f"{url}/{body['id']}", headers=viewer).status_code == 403
        assert client.delete(f"{url}/{body['id']}", headers=editor).status_code == 204


def test_republish_rebinds_compatible_schedule_and_pauses_incompatible_one(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        editor = _headers(EDITOR_TOKEN)
        workflow = client.post("/api/workflows", headers=editor, json=_workflow_body()).json()
        workflow_id = workflow["id"]
        assert (
            client.post(f"/api/workflows/{workflow_id}/publish", headers=editor).status_code == 200
        )
        schedule_url = f"/api/workflows/{workflow_id}/schedules"
        created = client.post(
            schedule_url,
            headers=editor,
            json={
                "name": "every minute",
                "cron_expression": "* * * * *",
                "inputs": {"message": "hello"},
            },
        ).json()

        compatible = _workflow_body()["dsl"]
        compatible["name"] = "Scheduled workflow v2"
        assert (
            client.put(
                f"/api/workflows/{workflow_id}",
                headers=editor,
                json={"dsl": compatible, "version": 1},
            ).status_code
            == 200
        )
        published_v2 = client.post(f"/api/workflows/{workflow_id}/publish", headers=editor)
        assert published_v2.status_code == 200
        rebound = client.get(schedule_url, headers=editor).json()[0]
        assert rebound["published_version_number"] == 2
        assert rebound["status"] == "active"

        incompatible = _workflow_body()["dsl"]
        incompatible["nodes"][0]["config"]["input_schema"].append(
            {"name": "tenant", "type": "string", "required": True}
        )
        assert (
            client.put(
                f"/api/workflows/{workflow_id}",
                headers=editor,
                json={"dsl": incompatible, "version": 2},
            ).status_code
            == 200
        )
        published_v3 = client.post(f"/api/workflows/{workflow_id}/publish", headers=editor)
        assert published_v3.status_code == 200
        paused = client.get(schedule_url, headers=editor).json()[0]
        assert paused["id"] == created["id"]
        assert paused["published_version_number"] == 3
        assert paused["status"] == "error"
        assert "tenant" in paused["last_error"]

        invalid_resume = client.put(
            f"{schedule_url}/{created['id']}",
            headers=editor,
            json={"enabled": True},
        )
        assert invalid_resume.status_code == 422
        resumed = client.put(
            f"{schedule_url}/{created['id']}",
            headers=editor,
            json={"enabled": True, "inputs": {"message": "hello", "tenant": "acme"}},
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "active"
        assert resumed.json()["last_error"] is None


def test_cron_calculation_is_timezone_aware_across_dst() -> None:
    before_spring_forward = datetime(2026, 3, 8, 6, 30, tzinfo=UTC)
    next_run = next_cron_run("0 3 * * *", "America/New_York", before_spring_forward)
    assert next_run == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)


async def _schedule_runtime(tmp_path, *, failure_policy: str = "skip"):
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    due = datetime.now(UTC) - timedelta(minutes=3)
    async with sessions() as session:
        workflow = await WorkflowRepo(session).create(
            "Scheduler",
            _workflow_body()["dsl"],
        )
        version = await WorkflowVersionRepo(session).publish(workflow)
        row = await WorkflowScheduleRepo(session).create(
            workflow_id=workflow.id,
            published_version_id=version.id,
            name="due",
            cron_expression="* * * * *",
            timezone="UTC",
            input_json={"message": "scheduled"},
            misfire_policy="skip",
            failure_policy=failure_policy,
            retry_delay_seconds=30,
            next_run_at=due,
            enabled=True,
        )
        await session.commit()
        return engine, sessions, row.id, due


async def test_two_schedulers_dispatch_one_due_slot_exactly_once(tmp_path) -> None:
    engine, sessions, schedule_id, due = await _schedule_runtime(tmp_path)
    try:
        now = datetime.now(UTC)
        first = WorkflowScheduleScheduler(sessions)
        second = WorkflowScheduleScheduler(sessions)
        results = await asyncio.gather(first.dispatch_once(now=now), second.dispatch_once(now=now))
        assert sum(results) == 1
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Execution)) == 1
            assert await session.scalar(select(func.count()).select_from(ExecutionQueueItem)) == 1
            row = await session.get(WorkflowSchedule, schedule_id)
            assert row is not None
            assert row.last_run_at is not None
            assert row.last_execution_id is not None
            execution = await session.get(Execution, row.last_execution_id)
            assert execution is not None
            assert execution.trigger_source == "schedule"
            assert row.next_run_at.replace(tzinfo=UTC) > now
            assert row.next_run_at.replace(tzinfo=UTC) > due
    finally:
        await engine.dispose()


async def test_two_schedulers_compete_for_one_thousand_due_slots(tmp_path) -> None:
    engine, sessions, schedule_id, _due = await _schedule_runtime(tmp_path)
    try:
        async with sessions() as session:
            source = await session.get(WorkflowSchedule, schedule_id)
            assert source is not None
            for index in range(999):
                session.add(
                    WorkflowSchedule(
                        workflow_id=source.workflow_id,
                        published_version_id=source.published_version_id,
                        name=f"due-{index}",
                        cron_expression=source.cron_expression,
                        timezone=source.timezone,
                        status="active",
                        input_json=dict(source.input_json),
                        misfire_policy=source.misfire_policy,
                        failure_policy=source.failure_policy,
                        retry_delay_seconds=source.retry_delay_seconds,
                        next_run_at=source.next_run_at,
                    )
                )
            await session.commit()

        now = datetime.now(UTC)
        first = WorkflowScheduleScheduler(sessions, batch_size=1000)
        second = WorkflowScheduleScheduler(sessions, batch_size=1000)
        results = await asyncio.gather(first.dispatch_once(now=now), second.dispatch_once(now=now))
        assert sum(results) == 1000
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Execution)) == 1000
            assert (
                await session.scalar(select(func.count()).select_from(ExecutionQueueItem)) == 1000
            )
            remaining = await session.scalar(
                select(func.count())
                .select_from(WorkflowSchedule)
                .where(WorkflowSchedule.pending_run_at.is_not(None))
            )
            assert remaining == 0
    finally:
        await engine.dispose()


async def test_sqlite_scheduler_retries_transient_claim_lock(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, sessions, schedule_id, _due = await _schedule_runtime(tmp_path)
    original_claim = WorkflowScheduleRepo.claim_due
    attempts = 0

    async def locked_then_claim(
        repo: WorkflowScheduleRepo, candidate_id: str, scheduled_for: datetime
    ) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OperationalError(
                "UPDATE workflow_schedules",
                {},
                sqlite3.OperationalError("database is locked"),
            )
        return await original_claim(repo, candidate_id, scheduled_for)

    monkeypatch.setattr(WorkflowScheduleRepo, "claim_due", locked_then_claim)
    try:
        scheduler = WorkflowScheduleScheduler(sessions)
        assert await scheduler.dispatch_once(now=datetime.now(UTC)) == 1
        assert attempts == 3
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Execution)) == 1
    finally:
        await engine.dispose()


async def test_schedule_retry_policy_retains_failure_and_defers_slot(tmp_path) -> None:
    engine, sessions, schedule_id, due = await _schedule_runtime(tmp_path, failure_policy="retry")
    try:
        async with sessions() as session:
            row = await session.get(WorkflowSchedule, schedule_id)
            assert row is not None
            row.input_json = {"message": 42}
            await session.commit()

        now = datetime.now(UTC)
        scheduler = WorkflowScheduleScheduler(sessions)
        assert await scheduler.dispatch_once(now=now) == 0
        async with sessions() as session:
            row = await session.get(WorkflowSchedule, schedule_id)
            assert row is not None
            assert row.status == "active"
            assert row.failure_count == 1
            assert "must be string" in (row.last_error or "")
            assert row.pending_run_at is None
            assert row.next_run_at.replace(tzinfo=UTC) >= now + timedelta(seconds=29)
            assert row.last_run_at is None
            assert row.next_run_at.replace(tzinfo=UTC) > due
            assert await session.scalar(select(func.count()).select_from(Execution)) == 0
    finally:
        await engine.dispose()


async def test_schedule_alert_policy_creates_durable_operational_alert(tmp_path) -> None:
    engine, sessions, schedule_id, _due = await _schedule_runtime(tmp_path, failure_policy="alert")
    try:
        async with sessions() as session:
            row = await session.get(WorkflowSchedule, schedule_id)
            assert row is not None
            row.input_json = {"message": 42}
            workflow_id = row.workflow_id
            await session.commit()

        scheduler = WorkflowScheduleScheduler(sessions)
        assert await scheduler.dispatch_once(now=datetime.now(UTC)) == 0
        async with sessions() as session:
            row = await session.get(WorkflowSchedule, schedule_id)
            alert = (await session.execute(select(CostAlert))).scalar_one()
            assert row is not None
            assert row.status == "error"
            assert alert.workflow_id == workflow_id
            assert alert.execution_id is None
            assert alert.kind == "schedule"
            assert alert.severity == "critical"
            assert alert.status == "open"
            assert schedule_id in alert.message
            assert row.last_error in alert.message
    finally:
        await engine.dispose()
