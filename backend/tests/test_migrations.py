"""Database migration and startup-recovery tests."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, select

from alembic import command
from app.core.config import BACKEND_DIR, Settings
from app.core.container import RESTART_RECOVERY_ERROR, recover_interrupted_executions
from app.db.base import create_engine, create_session_factory
from app.db.migrations import (
    BASELINE_REVISION,
    CURRENT_REVISION,
    CURRENT_TABLES,
    _run_command,
    check_database_schema,
    upgrade_database,
)
from app.db.models import (
    Execution,
    ExecutionEventRow,
    Session,
    User,
    Workflow,
    WorkflowCallback,
    WorkflowVersion,
)
from app.db.repositories import ExecutionRepo

HISTORICAL_REVISIONS = (
    "0001_p0_p3",
    "0002_mcp_secrets",
    "0003_rag",
    "0004_chat",
    "0005_ingest_jobs",
    "0006_workflow_versions",
    "0007_workflow_templates",
    "0008_execution_reruns",
    "0009_evaluations",
    "0010_evaluation_comparisons",
    "0011_cost_alerts",
    "0012_multi_user_identity",
    "0013_organization_tenancy",
    "0014_service_accounts",
    "0015_refresh_oidc",
    "0016_identity_hardening",
    "0017_workflow_reviews",
    "0018_audit_logs",
)
REDACTED_SNAPSHOT = Path(__file__).parent / "fixtures" / "redacted_pre_alembic.sql"


def _alembic_config(settings: Settings) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.effective_database_url)
    return config


async def _schema(settings: Settings) -> tuple[set[str], str | None]:
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
            version = None
            if "alembic_version" in tables:
                result = await connection.exec_driver_sql("SELECT version_num FROM alembic_version")
                version = result.scalar_one()
            return tables, version
    finally:
        await engine.dispose()


def test_current_revision_matches_alembic_head() -> None:
    """``CURRENT_REVISION`` must track the real alembic head.

    A stale constant is silently destructive: it gates ``/readyz`` (503 on every
    deployment at head), ``services/scheduler.py`` (scheduled workflows stop),
    and ``services/relay.py`` (event relay never starts). Asking alembic for the
    head instead of hard-coding it here keeps the check honest across future
    migrations.
    """
    script = ScriptDirectory.from_config(_alembic_config(Settings()))
    heads = script.get_heads()

    assert len(heads) == 1, f"expected a single alembic head, found {heads}"
    assert heads[0] == CURRENT_REVISION, (
        f"CURRENT_REVISION is {CURRENT_REVISION!r} but the alembic head is "
        f"{heads[0]!r}. Bump CURRENT_REVISION in app/db/migrations.py when "
        "adding a migration."
    )


async def test_upgrade_empty_database(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)

    await upgrade_database(settings)

    tables, version = await _schema(settings)
    assert tables >= CURRENT_TABLES
    assert version == CURRENT_REVISION


async def test_callback_activation_boundary_backfills_and_roundtrips(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    config = _alembic_config(settings)
    await asyncio.to_thread(command.upgrade, config, "0029_execution_trigger_source")
    engine = create_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                """
                INSERT INTO workflows (
                    id, name, description, dsl_json, version, is_archived,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "callback-migration-workflow",
                    "Callback migration",
                    "",
                    '{"version":"1.0","name":"Callback","nodes":[],"edges":[]}',
                    1,
                    0,
                    "2026-08-01 00:00:00",
                    "2026-08-01 00:00:00",
                ),
            )
            await connection.exec_driver_sql(
                """
                INSERT INTO workflow_callbacks (
                    id, workflow_id, project_id, url, secret_encrypted,
                    event_types_json, status, timeout_seconds, max_attempts,
                    retry_delay_seconds, created_at, updated_at, last_delivered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-callback",
                    "callback-migration-workflow",
                    None,
                    "https://callback.example.test/receive",
                    "encrypted-secret",
                    '["workflow_finished"]',
                    "active",
                    10,
                    5,
                    10,
                    "2026-08-02 03:04:05",
                    "2026-08-02 03:04:05",
                    None,
                ),
            )
    finally:
        await engine.dispose()

    await upgrade_database(settings)
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            activated_at = await connection.scalar(
                select(WorkflowCallback.activated_at).where(
                    WorkflowCallback.id == "legacy-callback"
                )
            )
            assert activated_at is not None
            assert activated_at.replace(tzinfo=UTC) == datetime(2026, 8, 2, 3, 4, 5, tzinfo=UTC)
    finally:
        await engine.dispose()

    await asyncio.to_thread(command.downgrade, config, "0029_execution_trigger_source")
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns("workflow_callbacks")
                }
            )
            assert "activated_at" not in columns
    finally:
        await engine.dispose()

    await upgrade_database(settings)
    _, version = await _schema(settings)
    assert version == CURRENT_REVISION


async def test_alembic_commands_are_serialized_within_process(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(data_dir=tmp_path)
    first_entered = threading.Event()
    release_first = threading.Event()
    active_lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_upgrade(_config: Config, _revision: str) -> None:
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
            is_first = not first_entered.is_set()
            first_entered.set()
        try:
            if is_first:
                assert release_first.wait(timeout=5)
        finally:
            with active_lock:
                active -= 1

    monkeypatch.setattr(command, "upgrade", fake_upgrade)
    first = asyncio.create_task(asyncio.to_thread(_run_command, settings, "upgrade"))
    assert await asyncio.to_thread(first_entered.wait, 5)
    second = asyncio.create_task(asyncio.to_thread(_run_command, settings, "upgrade"))
    await asyncio.sleep(0.05)
    release_first.set()
    await asyncio.gather(first, second)

    assert max_active == 1


async def test_oidc_only_user_can_downgrade_to_pre_oidc_revision(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    config = _alembic_config(settings)
    await upgrade_database(settings)
    engine = create_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                """
                INSERT INTO users (
                    id, email, display_name, password_hash, role, status,
                    created_at, last_login_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "oidc-only-user",
                    "oidc-only@example.com",
                    "OIDC Only",
                    None,
                    "viewer",
                    "active",
                    "2026-08-07 00:00:00",
                    None,
                ),
            )
    finally:
        await engine.dispose()

    await asyncio.to_thread(command.downgrade, config, "0014_service_accounts")
    tables, version = await _schema(settings)
    assert version == "0014_service_accounts"
    assert "refresh_tokens" not in tables
    assert "oidc_identities" not in tables

    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            password_hash = await connection.scalar(
                select(User.password_hash).where(User.id == "oidc-only-user")
            )
        assert password_hash == ""
    finally:
        await engine.dispose()


async def test_identity_hardening_caps_existing_long_sessions(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    config = _alembic_config(settings)
    await asyncio.to_thread(command.upgrade, config, "0015_refresh_oidc")
    engine = create_engine(settings)
    created_at = "2026-08-07 00:00:00"
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                """
                INSERT INTO users (
                    id, email, display_name, password_hash, role, status,
                    created_at, last_login_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-long-session-user",
                    "legacy-session@example.com",
                    "Legacy Session",
                    "unusable",
                    "viewer",
                    "active",
                    created_at,
                    None,
                ),
            )
            await connection.exec_driver_sql(
                """
                INSERT INTO sessions (
                    id, token_hash, user_id, created_at, expires_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-long-session",
                    "a" * 64,
                    "legacy-long-session-user",
                    created_at,
                    "2099-01-01 00:00:00",
                    None,
                ),
            )
    finally:
        await engine.dispose()

    before_upgrade = datetime.now(UTC)
    await upgrade_database(settings)
    after_upgrade = datetime.now(UTC)
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            expires_at = await connection.scalar(
                select(Session.expires_at).where(Session.id == "legacy-long-session")
            )
        assert expires_at is not None
        normalized = expires_at.replace(tzinfo=UTC)
        assert before_upgrade + timedelta(minutes=14, seconds=50) <= normalized
        assert normalized <= after_upgrade + timedelta(minutes=15, seconds=10)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("source_revision", HISTORICAL_REVISIONS)
async def test_upgrade_historical_revision_preserves_data(tmp_path, source_revision: str) -> None:
    settings = Settings(data_dir=tmp_path)
    await asyncio.to_thread(command.upgrade, _alembic_config(settings), source_revision)
    engine = create_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                """
                INSERT INTO workflows (
                    id, name, description, dsl_json, version, is_archived,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"probe-{source_revision}",
                    "Migration probe",
                    "Historical revision probe",
                    '{"version":"1.0","name":"Probe","nodes":[],"edges":[]}',
                    1,
                    0,
                    "2026-01-01 00:00:00",
                    "2026-01-01 00:00:00",
                ),
            )
            if source_revision not in {
                "0001_p0_p3",
                "0002_mcp_secrets",
                "0003_rag",
                "0004_chat",
                "0005_ingest_jobs",
            }:
                await connection.exec_driver_sql(
                    """
                    INSERT INTO workflow_versions (
                        id, workflow_id, number, status, name, description,
                        dsl_json, change_summary, created_at, published_at, archived_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "probe-version-0006",
                        f"probe-{source_revision}",
                        1,
                        "draft",
                        "Migration probe",
                        "Historical revision probe",
                        '{"version":"1.0","name":"Probe","nodes":[],"edges":[]}',
                        "Historical fixture",
                        "2026-01-01 00:00:00",
                        None,
                        None,
                    ),
                )
    finally:
        await engine.dispose()

    await upgrade_database(settings)

    tables, version = await _schema(settings)
    assert tables >= CURRENT_TABLES
    assert version == CURRENT_REVISION
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            workflow = await session.get(Workflow, f"probe-{source_revision}")
            assert workflow is not None
            assert workflow.name == "Migration probe"
            result = await session.execute(
                select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id)
            )
            version_row = result.scalar_one()
            assert version_row.number == workflow.version
            assert version_row.dsl_json == workflow.dsl_json
    finally:
        await engine.dispose()


async def test_upgrade_redacted_real_shape_snapshot(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)

    def load_snapshot() -> None:
        with sqlite3.connect(tmp_path / "app.db") as connection:
            connection.executescript(REDACTED_SNAPSHOT.read_text(encoding="utf-8"))

    await asyncio.to_thread(load_snapshot)
    await upgrade_database(settings)

    tables, version = await _schema(settings)
    assert tables >= CURRENT_TABLES
    assert version == CURRENT_REVISION
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            workflow = await session.get(Workflow, "redacted-workflow")
            execution = await session.get(Execution, "redacted-execution")
            assert workflow is not None
            assert workflow.name == "Redacted workflow"
            assert execution is not None
            assert execution.status == "succeeded"
            assert execution.workflow_version_id
            version_row = await session.get(WorkflowVersion, execution.workflow_version_id)
            assert version_row is not None
            assert version_row.workflow_id == workflow.id
    finally:
        await engine.dispose()


async def test_upgrade_adopts_legacy_create_all_database(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    config = _alembic_config(settings)
    await asyncio.to_thread(command.upgrade, config, BASELINE_REVISION)
    engine = create_engine(settings)
    # Insert via raw SQL because the baseline (0001) workflows table predates
    # the model's project_id column; a genuine legacy database would look the same.
    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            """
            INSERT INTO workflows (
                id, name, description, dsl_json, version, is_archived,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy",
                "Legacy",
                "",
                '{"version":"1.0","name":"Legacy","nodes":[],"edges":[]}',
                1,
                0,
                "2026-01-01 00:00:00",
                "2026-01-01 00:00:00",
            ),
        )
        await connection.exec_driver_sql("DROP TABLE alembic_version")
    await engine.dispose()

    await upgrade_database(settings)

    _, version = await _schema(settings)
    assert version == CURRENT_REVISION
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            assert await session.get(Workflow, "legacy") is not None
    finally:
        await engine.dispose()


async def test_alembic_schema_matches_models(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    config = _alembic_config(settings)

    await asyncio.to_thread(command.check, config)


async def test_schema_check_ignores_langgraph_owned_checkpoint_tables(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    try:
        async with engine.begin() as connection:
            for table_name in (
                "checkpoint_migrations",
                "checkpoints",
                "checkpoint_blobs",
                "checkpoint_writes",
            ):
                await connection.exec_driver_sql(
                    f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY)"
                )
            await connection.exec_driver_sql(
                "CREATE INDEX checkpoints_thread_id_idx ON checkpoints (id)"
            )
    finally:
        await engine.dispose()

    await check_database_schema(settings)


async def test_recover_interrupted_executions_preserves_waiting(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            workflow = Workflow(
                id="workflow",
                name="Recovery",
                dsl_json={"version": "1.0", "name": "Recovery", "nodes": [], "edges": []},
            )
            session.add(workflow)
            await session.flush()
            running = await ExecutionRepo(session).create("workflow", {}, "running")
            waiting = await ExecutionRepo(session).create("workflow", {}, "waiting")
            # create() now leaves rows queued; simulate a pre-queue crash.
            running.status = "running"
            waiting.status = "waiting_approval"
            session.add(
                ExecutionEventRow(
                    execution_id=running.id,
                    seq=3,
                    event_type="node_finished",
                    payload_json={},
                )
            )
            await session.commit()

        assert await recover_interrupted_executions(session_factory) == 1

        async with session_factory() as session:
            running_row = await session.get(Execution, "running")
            waiting_row = await session.get(Execution, "waiting")
            assert running_row is not None
            assert running_row.status == "failed"
            assert running_row.error == RESTART_RECOVERY_ERROR
            assert running_row.finished_at is not None
            assert waiting_row is not None
            assert waiting_row.status == "waiting_approval"
            result = await session.execute(
                select(ExecutionEventRow)
                .where(ExecutionEventRow.execution_id == "running")
                .order_by(ExecutionEventRow.seq)
            )
            events = list(result.scalars())
            assert events[-1].seq == 4
            assert events[-1].event_type == "workflow_failed"
            assert events[-1].payload_json["reason"] == "backend_restart"
    finally:
        await engine.dispose()
