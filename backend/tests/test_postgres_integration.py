"""Real PostgreSQL migration, drift, and tenant-storage integration gate."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.config import RunnableConfig
from sqlalchemy import Table, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings, validate_runtime_settings
from app.core.container import _persist_events
from app.db.base import create_engine, create_session_factory
from app.db.migrations import (
    CURRENT_REVISION,
    CURRENT_TABLES,
    check_database_schema,
    upgrade_database,
)
from app.db.models import (
    Document,
    Execution,
    ExecutionEventRow,
    ExecutionQueueItem,
    KnowledgeBase,
    Organization,
    Project,
    RefreshToken,
    Session,
    User,
    Workflow,
    WorkflowVersion,
)
from app.db.repositories import ExecutionQueueRepo, ExecutionRepo, ProjectRepo, WorkflowRepo
from app.engine.checkpoint import (
    close_checkpointer,
    copy_sqlite_checkpoints,
    make_checkpointer,
)
from app.engine.events import EventBus
from app.engine.execution_lease import LeaseLost, WorkerLease
from app.engine.execution_scheduler import ExecutionLeaseScheduler
from app.engine.executor import ExecutionEngine
from app.main import create_app
from app.providers.base import BaseChatProvider, StreamChunk, Usage
from app.rag.splitter import TextChunk
from app.rag.store import SqlVectorStore, build_vector_store
from app.schemas.events import EventType, ExecutionEvent
from app.services.migrate_sqlite import migrate_sqlite_relational
from app.services.project_model_costs import ProjectModelCostMeter
from app.services.project_quotas import ProjectQuotaService

POSTGRES_URL = os.environ.get("POSTGRES_TEST_DATABASE_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_URL, reason="POSTGRES_TEST_DATABASE_URL is not configured"),
]

FERNET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="


@pytest.fixture
def _postgres_selector_loop() -> Iterator[None]:
    if sys.platform != "win32":
        yield
        return

    previous = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(previous)


class _PricedProvider(BaseChatProvider):
    def __init__(self) -> None:
        super().__init__(
            model="postgres-fencing-test",
            prompt_price_per_million_usd="1",
            completion_price_per_million_usd="1",
        )

    async def stream_chat(self, *_args: Any, **_kwargs: Any) -> AsyncIterator[StreamChunk]:
        if False:
            yield StreamChunk(type="done")


def _wait_status(client: TestClient, execution_id: str, expected: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    current: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/executions/{execution_id}")
        assert response.status_code == 200, response.text
        current = response.json()
        if current["status"] in expected:
            return current
        time.sleep(0.1)
    raise AssertionError(f"execution did not reach {expected}: {current}")


def _human_workflow(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "dsl": {
            "version": "1.0",
            "name": name,
            "variables": [],
            "settings": {
                "max_loop_iterations": 20,
                "timeout_seconds": 30,
                "recursion_limit": 50,
            },
            "nodes": [
                {"id": "start", "type": "start", "position": {"x": 0, "y": 0}},
                {
                    "id": "approval",
                    "type": "human",
                    "position": {"x": 200, "y": 0},
                    "config": {"title": "Release approval", "instruction": "Approve release"},
                },
                {"id": "end", "type": "end", "position": {"x": 400, "y": 0}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "approval"},
                {"id": "e2", "source": "approval", "target": "end"},
            ],
        },
    }


async def test_postgres_migrations_are_serialized_and_schema_matches_models(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        database_url=POSTGRES_URL,
        database_pool_size=3,
        database_max_overflow=1,
    )
    validate_runtime_settings(settings)

    # Two release jobs starting together must converge on one migration head.
    await asyncio.gather(upgrade_database(settings), upgrade_database(settings))

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
            revision = await connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert tables >= CURRENT_TABLES
            assert revision.scalar_one() == CURRENT_REVISION

        await check_database_schema(settings)

        suffix = uuid4().hex[:12]
        async with factory() as session:
            organization = Organization(name="Postgres integration", slug=f"pg-{suffix}")
            project = Project(
                organization=organization,
                name="Tenant boundary",
                slug="tenant-boundary",
            )
            session.add(project)
            await session.commit()

        async with factory() as session:
            stored = await session.scalar(
                select(Project).where(Project.organization_id == organization.id)
            )
            assert stored is not None
            assert stored.slug == "tenant-boundary"
    finally:
        await engine.dispose()


async def test_postgres_imports_current_head_sqlite_relational_data(tmp_path) -> None:
    database_name = f"agentcanvas_migrate_{uuid4().hex[:12]}"
    base_url = make_url(POSTGRES_URL)
    admin_url = base_url.set(database="postgres")
    target_url = base_url.set(database=database_name)
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    target_engine = None
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))

        source_settings = Settings(data_dir=tmp_path / "legacy")
        target_settings = Settings(
            data_dir=tmp_path / "target",
            environment="test",
            database_url=target_url.render_as_string(hide_password=False),
            database_pool_size=2,
            database_max_overflow=1,
        )
        await upgrade_database(source_settings)
        await upgrade_database(target_settings)

        source_engine = create_engine(source_settings)
        workflow_table = cast(Table, Workflow.__table__)
        version_table = cast(Table, WorkflowVersion.__table__)
        execution_table = cast(Table, Execution.__table__)
        event_table = cast(Table, ExecutionEventRow.__table__)
        user_table = cast(Table, User.__table__)
        session_table = cast(Table, Session.__table__)
        refresh_token_table = cast(Table, RefreshToken.__table__)
        try:
            async with source_engine.begin() as connection:
                await connection.execute(
                    workflow_table.insert(),
                    {
                        "id": "sqlite-workflow",
                        "name": "SQLite migration",
                        "description": "",
                        "dsl_json": {"version": "1.0", "nodes": [], "edges": []},
                        "version": 1,
                        "is_archived": False,
                    },
                )
                await connection.execute(
                    version_table.insert(),
                    {
                        "id": "sqlite-version",
                        "workflow_id": "sqlite-workflow",
                        "number": 1,
                        "status": "published",
                        "name": "SQLite migration",
                        "description": "",
                        "dsl_json": {"version": "1.0", "nodes": [], "edges": []},
                        "change_summary": "",
                    },
                )
                await connection.execute(
                    execution_table.insert(),
                    {
                        "id": "sqlite-execution",
                        "workflow_id": "sqlite-workflow",
                        "workflow_version_id": "sqlite-version",
                        "status": "succeeded",
                        "input_json": {},
                        "output_json": {},
                        "thread_id": "sqlite-thread",
                    },
                )
                await connection.execute(
                    event_table.insert(),
                    {
                        "id": 41,
                        "execution_id": "sqlite-execution",
                        "seq": 1,
                        "event_type": "workflow_finished",
                        "payload_json": {},
                    },
                )
                expires_at = datetime.now(UTC) + timedelta(days=30)
                await connection.execute(
                    user_table.insert(),
                    {
                        "id": "sqlite-user",
                        "email": "sqlite-migration@example.com",
                    },
                )
                await connection.execute(
                    session_table.insert(),
                    [
                        {
                            "id": "sqlite-session-old",
                            "token_hash": "old-session-hash",
                            "user_id": "sqlite-user",
                            "expires_at": expires_at,
                        },
                        {
                            "id": "sqlite-session-new",
                            "token_hash": "new-session-hash",
                            "user_id": "sqlite-user",
                            "expires_at": expires_at,
                        },
                    ],
                )
                # Keep the child row physically first in SQLite, then form the
                # valid rotation edge. PostgreSQL requires the replacement row
                # to be inserted first because this FK is not deferred.
                await connection.execute(
                    refresh_token_table.insert(),
                    {
                        "id": "sqlite-refresh-old",
                        "token_hash": "old-refresh-hash",
                        "user_id": "sqlite-user",
                        "session_id": "sqlite-session-old",
                        "family_id": "sqlite-refresh-family",
                        "expires_at": expires_at,
                    },
                )
                await connection.execute(
                    refresh_token_table.insert(),
                    {
                        "id": "sqlite-refresh-new",
                        "token_hash": "new-refresh-hash",
                        "user_id": "sqlite-user",
                        "session_id": "sqlite-session-new",
                        "family_id": "sqlite-refresh-family",
                        "expires_at": expires_at,
                    },
                )
                await connection.execute(
                    refresh_token_table.update()
                    .where(refresh_token_table.c.id == "sqlite-refresh-old")
                    .values(replaced_by_id="sqlite-refresh-new")
                )
        finally:
            await source_engine.dispose()

        result = await migrate_sqlite_relational(
            source_settings.data_dir / "app.db",
            target_settings,
        )
        assert result.table_counts["workflows"] == 1

        target_engine = create_engine(target_settings)
        async with target_engine.begin() as connection:
            name = await connection.scalar(select(Workflow.name))
            replacement_id = await connection.scalar(
                select(RefreshToken.replaced_by_id).where(RefreshToken.id == "sqlite-refresh-old")
            )
            event_id = await connection.scalar(
                event_table.insert()
                .values(
                    execution_id="sqlite-execution",
                    seq=2,
                    event_type="workflow_started",
                    payload_json={},
                )
                .returning(ExecutionEventRow.id)
            )
        assert name == "SQLite migration"
        assert replacement_id == "sqlite-refresh-new"
        assert event_id == 42
    finally:
        if target_engine is not None:
            await target_engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(
                text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"),
                {"name": database_name},
            )
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        await admin.dispose()


def test_postgres_waiting_approval_survives_full_application_restart(
    tmp_path, _postgres_selector_loop: None
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key=FERNET_KEY,
        database_url=POSTGRES_URL,
        database_pool_size=3,
        database_max_overflow=1,
        local_embedding_dimensions=64,
        rate_limit_execution_requests=100,
    )
    name = f"Postgres durable approval {uuid4().hex[:12]}"

    with TestClient(create_app(settings)) as first_client:
        ready = first_client.get("/readyz")
        assert ready.status_code == 200, ready.text
        assert ready.json()["checks"]["checkpointer"]["backend"] == "postgresql"

        created = first_client.post("/api/workflows", json=_human_workflow(name))
        assert created.status_code == 201, created.text
        execution = first_client.post(
            f"/api/workflows/{created.json()['id']}/run",
            json={},
        )
        assert execution.status_code == 201, execution.text
        execution_id = execution.json()["id"]
        _wait_status(first_client, execution_id, {"waiting_approval"})

    with TestClient(create_app(settings)) as restarted_client:
        persisted = restarted_client.get(f"/api/executions/{execution_id}")
        assert persisted.status_code == 200, persisted.text
        assert persisted.json()["status"] == "waiting_approval"

        resumed = restarted_client.post(
            f"/api/executions/{execution_id}/resume",
            json={"decision": {"approved": True}},
        )
        assert resumed.status_code == 200, resumed.text
        completed = _wait_status(restarted_client, execution_id, {"succeeded", "failed"})
        assert completed["status"] == "succeeded", completed


async def test_postgres_imports_legacy_sqlite_checkpoints(tmp_path) -> None:
    legacy_settings = Settings(data_dir=tmp_path / "legacy", environment="test")
    target_settings = Settings(
        data_dir=tmp_path / "target",
        environment="test",
        database_url=POSTGRES_URL,
        database_pool_size=2,
        database_max_overflow=1,
    )
    config = {
        "configurable": {
            "thread_id": "legacy-postgres-migration-thread",
            "checkpoint_ns": "",
        }
    }

    await close_checkpointer()
    source = await make_checkpointer(legacy_settings)
    checkpoint = empty_checkpoint()
    checkpoint["id"] = "legacy-checkpoint-0001"
    await source.aput(config, checkpoint, {"source": "legacy"}, {})
    await close_checkpointer()

    target = await make_checkpointer(target_settings)
    result = await copy_sqlite_checkpoints(legacy_settings.checkpoint_db_path, target)
    restored = await target.aget_tuple(config)
    assert result.checkpoints == 1
    assert result.threads == 1
    assert restored is not None
    assert restored.checkpoint["id"] == "legacy-checkpoint-0001"
    assert restored.metadata["source"] == "legacy"
    await close_checkpointer()


async def test_postgres_pgvector_ranks_in_database_and_is_shared(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        database_url=POSTGRES_URL,
        database_pool_size=3,
        database_max_overflow=1,
    )
    await upgrade_database(settings)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    suffix = uuid4().hex[:12]
    kb_id = f"vector-kb-{suffix}"
    document_id = f"vector-doc-{suffix}"
    try:
        async with factory() as session:
            session.add(
                KnowledgeBase(
                    id=kb_id,
                    name="Postgres vectors",
                    embedding_model_id="embedding-postgres",
                )
            )
            session.add(
                Document(
                    id=document_id,
                    kb_id=kb_id,
                    filename="postgres.txt",
                    file_path=f"{kb_id}/postgres.txt",
                    mime_type="text/plain",
                    size_bytes=20,
                    content_sha256="c" * 64,
                    status="ready",
                    chunk_count=2,
                )
            )
            await session.commit()

        store = build_vector_store(settings, factory)
        assert isinstance(store, SqlVectorStore)
        assert store.backend_name == "pgvector"
        await store.upsert_document(
            kb_id=kb_id,
            document_id=document_id,
            filename="postgres.txt",
            chunks=[
                TextChunk(index=0, text="north", start_char=0, end_char=5, page=1),
                TextChunk(index=1, text="east", start_char=6, end_char=10, page=1),
            ],
            embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        )

        # A separately constructed replica must see the committed vectors, and
        # pgvector's cosine operator must rank them before they reach Python.
        peer = SqlVectorStore(factory)
        hits = await peer.query(kb_id=kb_id, vector=[0.0, 1.0, 0.0], top_k=1)
        assert hits and hits[0].text == "east"
        assert hits[0].score == pytest.approx(1.0)
        health = await peer.health()
        assert health["state"] == "ready"
        assert health["backend"] == "pgvector"
        assert health["extension_version"]
        await store.delete_knowledge_base(kb_id)
    finally:
        await engine.dispose()


async def test_postgres_multi_owner_claim_scheduler_and_stale_fence(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        database_url=POSTGRES_URL,
        database_pool_size=4,
        database_max_overflow=2,
    )
    await upgrade_database(settings)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    execution_engine = ExecutionEngine(
        session_factory=factory,
        event_bus=EventBus(),
        secret_box=None,  # type: ignore[arg-type]
        settings=settings,
        start_worker=False,
    )
    suffix = uuid4().hex[:12]
    try:
        async with factory() as session:
            organization = Organization(
                name=f"Postgres queue org {suffix}",
                slug=f"pg-queue-org-{suffix}",
            )
            session.add(organization)
            await session.flush()
            project = await ProjectRepo(session).create(
                organization.id,
                f"Postgres queue project {suffix}",
                f"pg-queue-project-{suffix}",
            )
            workflow = await WorkflowRepo(session).create(
                f"Postgres queue {suffix}",
                {
                    "version": "1.0",
                    "name": f"Postgres queue {suffix}",
                    "nodes": [],
                    "edges": [],
                },
                project_id=project.id,
            )
            execution = await ExecutionRepo(session).create(
                workflow.id, {}, execution_id=f"pg-queue-{suffix}"
            )
            item = await ExecutionQueueRepo(session).enqueue(execution.id)
            await session.commit()
            item_id = item.id
            execution_id = execution.id
            project_id = project.id

        async def claim(owner_id: str):
            async with factory() as session:
                claimed = await ExecutionQueueRepo(session).claim_next(
                    owner_id=owner_id,
                    lease_seconds=30,
                )
                await session.commit()
                return claimed

        claims = await asyncio.gather(claim("pg-worker-a"), claim("pg-worker-b"))
        winners = [claimed for claimed in claims if claimed is not None]
        assert len(winners) == 1
        first = winners[0]
        old_lease = WorkerLease(item_id, str(first.owner_id), first.lease_generation)

        async with factory() as session:
            queue_item = await session.get(ExecutionQueueItem, item_id)
            assert queue_item is not None
            queue_item.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        assert await claim("pg-worker-before-scheduler") is None
        scheduler = ExecutionLeaseScheduler(factory, max_attempts=3)
        assert await scheduler.recover_once() == 1
        second = await claim("pg-worker-replacement")
        assert second is not None
        assert second.lease_generation == old_lease.generation + 1

        with pytest.raises(LeaseLost):
            await execution_engine._set_status(  # noqa: SLF001
                execution_id,
                "succeeded",
                output={"stale": True},
                lease=old_lease,
                event_type=EventType.WORKFLOW_FINISHED,
                event_payload={"output": {"stale": True}},
            )
        await _persist_events(
            factory,
            [
                ExecutionEvent(
                    execution_id=execution_id,
                    event_type=EventType.NODE_FINISHED,
                    seq=1,
                    payload={"stale": True},
                )
            ],
            {execution_id: old_lease},
        )
        stale_checkpointer = await execution_engine._checkpointer(old_lease)  # noqa: SLF001
        checkpoint_config: RunnableConfig = {
            "configurable": {"thread_id": execution_id, "checkpoint_ns": ""}
        }
        with pytest.raises(LeaseLost):
            await stale_checkpointer.aput(
                checkpoint_config,
                empty_checkpoint(),
                {"source": "loop"},
                {},
            )
        cost_meter = ProjectModelCostMeter(factory, project_id, lease=old_lease)
        priced_provider = _PricedProvider()
        with pytest.raises(LeaseLost):
            await cost_meter.record(
                priced_provider,
                Usage(prompt_tokens=1, total_tokens=1),
            )
        async with factory() as session:
            stored_execution = await ExecutionRepo(session).get(execution_id)
            assert stored_execution is not None
            assert stored_execution.status == "running"
            assert stored_execution.output_json is None
            assert await ExecutionRepo(session).list_events_after(execution_id) == []
            assert (await ProjectQuotaService(session).snapshot(project_id)).model_cost_units == 0
    finally:
        await execution_engine.shutdown(grace_period=0.1)
        await close_checkpointer()
        await engine.dispose()
