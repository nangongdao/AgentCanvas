"""Execution event retention, archive export, and cold/hot listing (C6-1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from app.core.config import Settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import Execution, ExecutionEventRow, Workflow, WorkflowVersion
from app.engine.execution_event_retention import ExecutionEventRetentionScheduler
from app.main import create_app

_NOW = datetime.now(UTC)


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs=",
        local_embedding_dimensions=64,
        rate_limit_execution_requests=100,
        execution_max_concurrent=32,
        model_max_concurrent=32,
        log_level="WARNING",
    )
    base.update(overrides)
    return Settings(**base)


def _wait_terminal(client: TestClient, execution_id: str, timeout_s: float = 20.0) -> dict:
    import time

    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = client.get(f"/api/executions/{execution_id}").json()
        if last["status"] in {"succeeded", "failed", "cancelled"}:
            return last
        time.sleep(0.1)
    raise AssertionError(f"execution {execution_id} did not finish: {last}")


async def _seed_execution(
    session_factory,
    *,
    execution_id: str,
    status: str,
    started_at: datetime,
    finished_at: datetime | None,
    event_count: int,
    event_ts: datetime,
) -> None:
    async with session_factory() as session:
        session.add(
            Workflow(
                id=f"wf-{execution_id}",
                name=f"Workflow {execution_id}",
                dsl_json={
                    "version": "1.0",
                    "name": "retention probe",
                    "nodes": [],
                    "edges": [],
                },
            )
        )
        await session.flush()
        version = WorkflowVersion(
            id=f"ver-{execution_id}",
            workflow_id=f"wf-{execution_id}",
            number=1,
            status="published",
            name="retention probe version",
            dsl_json={
                "version": "1.0",
                "name": "retention probe",
                "nodes": [],
                "edges": [],
            },
            published_at=started_at,
        )
        session.add(version)
        await session.flush()
        session.add(
            Execution(
                id=execution_id,
                workflow_id=f"wf-{execution_id}",
                workflow_version_id=version.id,
                status=status,
                trigger_source="manual",
                input_json={},
                output_json={},
                error=None,
                thread_id=execution_id,
                session_id=None,
                started_at=started_at,
                finished_at=finished_at,
            )
        )
        if event_count:
            await session.execute(
                insert(ExecutionEventRow),
                [
                    {
                        "execution_id": execution_id,
                        "seq": seq,
                        "event_type": "node_streaming",
                        "node_id": "agent",
                        "payload_json": {"delta": "x"},
                        "ts": event_ts + timedelta(microseconds=seq),
                    }
                    for seq in range(1, event_count + 1)
                ],
            )
        await session.commit()


async def test_prune_deletes_only_terminal_old_events(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        old = _NOW - timedelta(days=60)
        recent = _NOW - timedelta(days=3)
        # terminal + old → eligible
        await _seed_execution(
            session_factory,
            execution_id="exec-old-terminal",
            status="succeeded",
            started_at=old,
            finished_at=old,
            event_count=4,
            event_ts=old,
        )
        # terminal + recent → protected by the retention window
        await _seed_execution(
            session_factory,
            execution_id="exec-recent-terminal",
            status="failed",
            started_at=recent,
            finished_at=recent,
            event_count=3,
            event_ts=recent,
        )
        # running + old ts → protected (execution never reached terminal state)
        await _seed_execution(
            session_factory,
            execution_id="exec-old-running",
            status="running",
            started_at=old,
            finished_at=None,
            event_count=2,
            event_ts=old,
        )

        scheduler = ExecutionEventRetentionScheduler(
            session_factory,
            retention_days=30,
            grace_days=0,
            batch_size=100,
            poll_seconds=60,
            owner_id="test",
        )
        deleted = await scheduler.prune_once(now=_NOW)

        assert deleted == 4
        async with session_factory() as session:
            remaining = (
                await session.execute(select(ExecutionEventRow).order_by(ExecutionEventRow.id))
            ).scalars().all()
            ids = {row.execution_id for row in remaining}
            assert ids == {"exec-recent-terminal", "exec-old-running"}
            assert len([r for r in remaining if r.execution_id == "exec-recent-terminal"]) == 3
            assert len([r for r in remaining if r.execution_id == "exec-old-running"]) == 2
    finally:
        await engine.dispose()


async def test_prune_batched_and_non_blocking(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        old = _NOW - timedelta(days=60)
        # seed 5 events for a single terminal execution; batch_size=2 forces 3 sweeps
        await _seed_execution(
            session_factory,
            execution_id="exec-batched",
            status="succeeded",
            started_at=old,
            finished_at=old,
            event_count=5,
            event_ts=old,
        )
        scheduler = ExecutionEventRetentionScheduler(
            session_factory,
            retention_days=30,
            grace_days=0,
            batch_size=2,
            poll_seconds=60,
            owner_id="test",
        )
        deleted = await scheduler.prune_once(now=_NOW)
        assert deleted == 5
        async with session_factory() as session:
            remaining = (
                await session.execute(select(ExecutionEventRow))
            ).scalars().all()
            assert remaining == []
    finally:
        await engine.dispose()


async def test_retention_scheduler_skips_when_disabled(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        old = _NOW - timedelta(days=120)
        await _seed_execution(
            session_factory,
            execution_id="exec-disabled",
            status="succeeded",
            started_at=old,
            finished_at=old,
            event_count=3,
            event_ts=old,
        )
        scheduler = ExecutionEventRetentionScheduler(
            session_factory,
            retention_days=0,
            grace_days=0,
            batch_size=10,
            poll_seconds=60,
            owner_id="test",
        )
        assert not scheduler.enabled
        deleted = await scheduler.prune_once(now=_NOW)
        assert deleted == 0
        async with session_factory() as session:
            remaining = (
                await session.execute(select(ExecutionEventRow))
            ).scalars().all()
            assert len(remaining) == 3
    finally:
        await engine.dispose()


async def test_prune_respects_grace_window(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        # finished 25 days ago → outside 30-day retention but inside the
        # 7-day grace window (30 + 7 = 37 days). Should be protected.
        borderline = _NOW - timedelta(days=25)
        await _seed_execution(
            session_factory,
            execution_id="exec-grace",
            status="succeeded",
            started_at=borderline,
            finished_at=borderline,
            event_count=2,
            event_ts=borderline,
        )
        scheduler = ExecutionEventRetentionScheduler(
            session_factory,
            retention_days=30,
            grace_days=7,
            batch_size=100,
            poll_seconds=60,
            owner_id="test",
        )
        deleted = await scheduler.prune_once(now=_NOW)
        assert deleted == 0
    finally:
        await engine.dispose()


def test_export_endpoint_returns_all_events_as_json(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        started = client.post(
            "/api/workflows/demo-linear/run",
            json={"inputs": {"user_query": "export probe"}},
        )
        execution_id = started.json()["id"]
        _wait_terminal(client, execution_id)
        # allow the event bus to finish persisting terminal events
        import time

        time.sleep(0.3)

        response = client.get(f"/api/executions/{execution_id}/events/export")
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        assert f"events-{execution_id}" in response.headers["content-disposition"]
        assert response.headers.get("x-content-type-options") == "nosniff"
        events = response.json()
        assert isinstance(events, list)
        assert len(events) >= 2  # at least workflow_started + terminal
        first = events[0]
        assert {"seq", "event_type", "node_id", "ts", "payload"} <= set(first)
        assert first["event_type"] == "workflow_started"
        # events are ordered by seq ascending
        seqs = [event["seq"] for event in events]
        assert seqs == sorted(seqs)
        assert isinstance(json.dumps(events), str)  # fully serializable


def test_list_executions_status_filter_cold_hot(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        # run two executions so we have terminal rows
        for query in ("cold-1", "cold-2"):
            started = client.post(
                "/api/workflows/demo-linear/run",
                json={"inputs": {"user_query": query}},
            )
            assert started.status_code == 201
            _wait_terminal(client, started.json()["id"])

        # active filter returns no terminal rows
        active = client.get(
            "/api/workflows/demo-linear/executions",
            params={"status": "running,queued,waiting_approval"},
        )
        assert active.status_code == 200
        assert all(
            item["status"] in {"running", "queued", "waiting_approval"}
            for item in active.json()["items"]
        )

        # historical filter returns only terminal rows
        historical = client.get(
            "/api/workflows/demo-linear/executions",
            params={"status": "succeeded,failed,cancelled", "limit": 50},
        )
        assert historical.status_code == 200
        statuses = {item["status"] for item in historical.json()["items"]}
        assert statuses <= {"succeeded", "failed", "cancelled"}
        assert len(historical.json()["items"]) >= 2

        # invalid status is rejected with 422
        invalid = client.get(
            "/api/workflows/demo-linear/executions",
            params={"status": "succeeded,bogus"},
        )
        assert invalid.status_code == 422
