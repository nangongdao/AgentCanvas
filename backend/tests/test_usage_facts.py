"""Usage fact aggregation, export, and billing reconciliation (C7-1)."""

from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from app.core.config import Settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import (
    App,
    ChatSession,
    Document,
    Execution,
    ExecutionEventRow,
    KnowledgeBase,
    ModelConfig,
    Organization,
    Project,
    UsageDailyFact,
    Workflow,
    WorkflowVersion,
)
from app.engine.usage_fact_scheduler import UsageFactScheduler
from app.main import create_app
from app.services.usage_facts import aggregate_day, complete_days_before

FERNET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="

_NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
_DAY = date(2026, 8, 19)
_DAY_BEFORE = date(2026, 8, 18)


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key=FERNET_KEY,
        local_embedding_dimensions=64,
        rate_limit_execution_requests=100,
        execution_max_concurrent=32,
        model_max_concurrent=32,
        log_level="WARNING",
    )
    base.update(overrides)
    return Settings(**base)


def _agent_dsl() -> dict:
    """Two agents on different models so per-model attribution is observable."""
    return {
        "version": "1.0",
        "name": "usage facts probe",
        "settings": {"max_loop_iterations": 20, "timeout_seconds": 30, "recursion_limit": 50},
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "position": {"x": 0, "y": 0},
                "config": {
                    "input_schema": [{"name": "user_query", "type": "string", "required": True}]
                },
            },
            {
                "id": "agent-a",
                "type": "agent",
                "position": {"x": 200, "y": 0},
                "config": {
                    "model_config_id": "model-a",
                    "user_prompt": "{{input.user_query}}",
                },
            },
            {
                "id": "agent-b",
                "type": "agent",
                "position": {"x": 400, "y": 0},
                "config": {
                    "model_config_id": "model-b",
                    "user_prompt": "{{nodes.agent-a.output}}",
                },
            },
            {
                "id": "end",
                "type": "end",
                "position": {"x": 600, "y": 0},
                "config": {"output_template": {"answer": "{{nodes.agent-b.output}}"}},
            },
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "agent-a"},
            {"id": "e2", "source": "agent-a", "target": "agent-b"},
            {"id": "e3", "source": "agent-b", "target": "end"},
        ],
    }


def _echo_dsl() -> dict:
    return {
        "version": "1.0",
        "name": "usage facts echo",
        "settings": {"max_loop_iterations": 20, "timeout_seconds": 30, "recursion_limit": 50},
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "position": {"x": 0, "y": 0},
                "config": {
                    "input_schema": [{"name": "user_query", "type": "string", "required": True}]
                },
            },
            {
                "id": "end",
                "type": "end",
                "position": {"x": 200, "y": 0},
                "config": {"output_template": {"answer": "{{nodes.start.output.user_query}}"}},
            },
        ],
        "edges": [{"id": "e1", "source": "start", "target": "end"}],
    }


def _unpriced_dsl() -> dict:
    return {
        "version": "1.0",
        "name": "usage facts unpriced",
        "settings": {"max_loop_iterations": 20, "timeout_seconds": 30, "recursion_limit": 50},
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "position": {"x": 0, "y": 0},
                "config": {
                    "input_schema": [{"name": "user_query", "type": "string", "required": True}]
                },
            },
            {
                "id": "agent-a",
                "type": "agent",
                "position": {"x": 200, "y": 0},
                "config": {
                    "model_config_id": "model-c",
                    "user_prompt": "{{input.user_query}}",
                },
            },
            {
                "id": "end",
                "type": "end",
                "position": {"x": 400, "y": 0},
                "config": {"output_template": {"answer": "{{nodes.agent-a.output}}"}},
            },
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "agent-a"},
            {"id": "e2", "source": "agent-a", "target": "end"},
        ],
    }


async def _seed_tenant(
    session_factory,
    *,
    org_id: str,
    project_id: str,
    workflow_id: str,
    version_id: str,
    dsl: dict,
) -> None:
    async with session_factory() as session:
        if await session.get(Organization, org_id) is None:
            session.add(Organization(id=org_id, name=f"Org {org_id}", slug=f"slug-{org_id}"))
            await session.flush()
        if await session.get(Project, project_id) is None:
            session.add(
                Project(
                    id=project_id,
                    name=f"Project {project_id}",
                    slug=f"slug-{project_id}",
                    organization_id=org_id,
                )
            )
            await session.flush()
        session.add(
            Workflow(
                id=workflow_id,
                name=f"Workflow {workflow_id}",
                project_id=project_id,
                dsl_json=dsl,
            )
        )
        await session.flush()
        session.add(
            WorkflowVersion(
                id=version_id,
                workflow_id=workflow_id,
                number=1,
                status="published",
                name=f"Version {version_id}",
                dsl_json=dsl,
                published_at=_NOW - timedelta(days=5),
            )
        )
        await session.commit()


async def _seed_models(session_factory) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                ModelConfig(
                    id="model-a",
                    name="Model A",
                    provider="openai_compat",
                    model_name="model-a-1",
                    prompt_price_per_million_usd="2",
                    completion_price_per_million_usd="4",
                    pricing_version="prices-v1",
                ),
                ModelConfig(
                    id="model-b",
                    name="Model B",
                    provider="openai_compat",
                    model_name="model-b-1",
                    prompt_price_per_million_usd="1",
                    completion_price_per_million_usd="2",
                    pricing_version="prices-v1",
                ),
                # No pricing: executions touching it price as unknown.
                ModelConfig(
                    id="model-c",
                    name="Model C",
                    provider="openai_compat",
                    model_name="model-c-1",
                ),
            ]
        )
        await session.commit()


async def _seed_execution(
    session_factory,
    *,
    execution_id: str,
    workflow_id: str,
    version_id: str,
    status: str,
    started_at: datetime,
    session_id: str | None = None,
    events: list[tuple[str, str, dict]] | None = None,
) -> None:
    async with session_factory() as session:
        session.add(
            Execution(
                id=execution_id,
                workflow_id=workflow_id,
                workflow_version_id=version_id,
                status=status,
                trigger_source="manual",
                input_json={},
                thread_id=execution_id,
                session_id=session_id,
                started_at=started_at,
                finished_at=started_at + timedelta(seconds=5),
            )
        )
        if events:
            await session.execute(
                insert(ExecutionEventRow),
                [
                    {
                        "execution_id": execution_id,
                        "seq": seq,
                        "event_type": event_type,
                        "node_id": node_id,
                        "payload_json": payload,
                        "ts": started_at + timedelta(microseconds=seq),
                    }
                    for seq, (event_type, node_id, payload) in enumerate(events, start=1)
                ],
            )
        await session.commit()


def _agent_events(*usage_by_node: tuple[str, dict]) -> list[tuple[str, str, dict]]:
    events: list[tuple[str, str, dict]] = []
    for node_id, usage in usage_by_node:
        events.append(("node_started", node_id, {}))
        events.append(("node_finished", node_id, {"output": {"meta": {"usage": usage}}}))
    return events


def _usage(prompt: int, completion: int) -> dict:
    return {"prompt": prompt, "completion": completion, "total": prompt + completion}


async def _read_facts(session_factory, day: date) -> list[UsageDailyFact]:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(UsageDailyFact)
                    .where(UsageDailyFact.day == day)
                    .order_by(
                        UsageDailyFact.organization_id.asc(),
                        UsageDailyFact.project_id.asc(),
                        UsageDailyFact.app_id.asc(),
                        UsageDailyFact.model_config_id.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        # Detach so post-transaction attribute access keeps working.
        for row in rows:
            session.expunge(row)
        return list(rows)


async def test_aggregate_day_attributes_models_apps_and_storage(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        await _seed_tenant(
            session_factory,
            org_id="org-1",
            project_id="prj-1",
            workflow_id="wf-agents",
            version_id="ver-agents",
            dsl=_agent_dsl(),
        )
        await _seed_tenant(
            session_factory,
            org_id="org-1",
            project_id="prj-1",
            workflow_id="wf-echo",
            version_id="ver-echo",
            dsl=_echo_dsl(),
        )
        await _seed_tenant(
            session_factory,
            org_id="org-1",
            project_id="prj-1",
            workflow_id="wf-unpriced",
            version_id="ver-unpriced",
            dsl=_unpriced_dsl(),
        )
        await _seed_models(session_factory)

        async with session_factory() as session:
            session.add(
                App(
                    id="app-1",
                    project_id="prj-1",
                    name="Usage App",
                    type="chatbot",
                    visibility="project",
                    status="active",
                    slug="usage-app",
                )
            )
            await session.flush()
            session.add(
                ChatSession(
                    id="chat-1",
                    title="usage chat",
                    workflow_id="wf-agents",
                    app_id="app-1",
                )
            )
            await session.flush()
            session.add(KnowledgeBase(id="kb-1", name="usage kb", project_id="prj-1"))
            await session.flush()
            session.add(
                Document(
                    id="doc-1",
                    kb_id="kb-1",
                    filename="usage.txt",
                    file_path=str(tmp_path / "usage.txt"),
                    mime_type="text/plain",
                    size_bytes=1500,
                    content_sha256="a" * 64,
                    status="ready",
                    created_at=datetime.combine(_DAY, datetime.min.time(), tzinfo=UTC)
                    + timedelta(hours=1),
                )
            )
            await session.commit()

        day_start = datetime.combine(_DAY, datetime.min.time(), tzinfo=UTC)
        # Two-model execution inside the app session.
        await _seed_execution(
            session_factory,
            execution_id="exec-app",
            workflow_id="wf-agents",
            version_id="ver-agents",
            status="succeeded",
            started_at=day_start + timedelta(hours=1),
            session_id="chat-1",
            events=_agent_events(
                ("agent-a", _usage(1000, 500)),  # 1000*2 + 500*4 = 4000 per 1M
                ("agent-b", _usage(200, 100)),  # 200*1 + 100*2 = 400 per 1M
            ),
        )
        # No-agent execution: NULL-model bucket, known-zero cost.
        await _seed_execution(
            session_factory,
            execution_id="exec-echo",
            workflow_id="wf-echo",
            version_id="ver-echo",
            status="succeeded",
            started_at=day_start + timedelta(hours=2),
        )
        # Failed before any agent event: unknown cost in NULL-model bucket.
        await _seed_execution(
            session_factory,
            execution_id="exec-failed",
            workflow_id="wf-agents",
            version_id="ver-agents",
            status="failed",
            started_at=day_start + timedelta(hours=3),
        )
        # Unpriced model: tokens count, cost unknown.
        await _seed_execution(
            session_factory,
            execution_id="exec-unpriced",
            workflow_id="wf-unpriced",
            version_id="ver-unpriced",
            status="succeeded",
            started_at=day_start + timedelta(hours=4),
            events=[
                ("node_started", "agent-a", {}),
                ("node_finished", "agent-a", {"output": {"meta": {"usage": _usage(10, 5)}}}),
            ],
        )
        # Non-terminal: excluded entirely.
        await _seed_execution(
            session_factory,
            execution_id="exec-running",
            workflow_id="wf-agents",
            version_id="ver-agents",
            status="running",
            started_at=day_start + timedelta(hours=5),
        )
        # A different day: must not leak into _DAY facts.
        await _seed_execution(
            session_factory,
            execution_id="exec-earlier",
            workflow_id="wf-echo",
            version_id="ver-echo",
            status="succeeded",
            started_at=datetime.combine(_DAY_BEFORE, datetime.min.time(), tzinfo=UTC),
        )

        async with session_factory() as session:
            written = await aggregate_day(session, day=_DAY, now=_NOW)
            await session.commit()
        assert written == 4

        facts = await _read_facts(session_factory, _DAY)
        by_key = {(fact.app_id, fact.model_config_id): fact for fact in facts}

        # app + model-a bucket
        fact = by_key[("app-1", "model-a")]
        assert fact.organization_id == "org-1"
        assert fact.project_id == "prj-1"
        assert fact.executions == 1
        assert fact.prompt_tokens == 1000
        assert fact.completion_tokens == 500
        assert fact.total_tokens == 1500
        assert fact.estimated_cost_usd == "0.004000000000"
        assert fact.cost_unknown_executions == 0

        # app + model-b bucket
        fact = by_key[("app-1", "model-b")]
        assert fact.executions == 1
        assert fact.total_tokens == 300
        assert fact.estimated_cost_usd == "0.000400000000"

        # no-app + model-c bucket from the unpriced-model execution: tokens
        # count, cost is unknown because model-c has no rates.
        fact = by_key[(None, "model-c")]
        assert fact.executions == 1
        assert fact.total_tokens == 15
        assert fact.estimated_cost_usd is None
        assert fact.cost_unknown_executions == 1

        # NULL model bucket: echo execution (known zero) + failed-before-agent
        # execution (unknown). Storage delta lands here too.
        fact = by_key[(None, None)]
        assert fact.executions == 2
        assert fact.total_tokens == 0
        assert fact.storage_bytes_delta == 1500
        assert fact.cost_unknown_executions == 1
        assert fact.estimated_cost_usd is None

        # The earlier day aggregates into its own fact day.
        async with session_factory() as session:
            await aggregate_day(session, day=_DAY_BEFORE, now=_NOW)
            await session.commit()
        earlier = await _read_facts(session_factory, _DAY_BEFORE)
        assert [(fact.app_id, fact.model_config_id, fact.executions) for fact in earlier] == [
            (None, None, 1)
        ]
    finally:
        await engine.dispose()


async def test_aggregate_day_idempotent_and_storage_only(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        await _seed_tenant(
            session_factory,
            org_id="org-2",
            project_id="prj-2",
            workflow_id="wf-echo2",
            version_id="ver-echo2",
            dsl=_echo_dsl(),
        )
        day_start = datetime.combine(_DAY, datetime.min.time(), tzinfo=UTC)
        await _seed_execution(
            session_factory,
            execution_id="exec-echo2",
            workflow_id="wf-echo2",
            version_id="ver-echo2",
            status="succeeded",
            started_at=day_start + timedelta(hours=1),
        )
        async with session_factory() as session:
            session.add(KnowledgeBase(id="kb-2", name="kb2", project_id="prj-2"))
            await session.flush()
            session.add(
                Document(
                    id="doc-2",
                    kb_id="kb-2",
                    filename="doc2.txt",
                    file_path=str(tmp_path / "doc2.txt"),
                    mime_type="text/plain",
                    size_bytes=700,
                    content_sha256="b" * 64,
                    status="ready",
                    created_at=day_start + timedelta(hours=2),
                )
            )
            await session.commit()

        for _ in range(2):
            async with session_factory() as session:
                written = await aggregate_day(session, day=_DAY, now=_NOW)
                await session.commit()
            assert written == 1

        def _snapshot(facts: list[UsageDailyFact]) -> str:
            return json.dumps(
                [
                    [
                        fact.organization_id,
                        fact.project_id,
                        fact.app_id,
                        fact.model_config_id,
                        fact.executions,
                        fact.total_tokens,
                        fact.estimated_cost_usd,
                        fact.storage_bytes_delta,
                    ]
                    for fact in facts
                ]
            )

        first = _snapshot(await _read_facts(session_factory, _DAY))
        async with session_factory() as session:
            await aggregate_day(session, day=_DAY, now=_NOW)
            await session.commit()
        assert _snapshot(await _read_facts(session_factory, _DAY)) == first

        facts = await _read_facts(session_factory, _DAY)
        assert len(facts) == 1
        assert facts[0].executions == 1
        assert facts[0].storage_bytes_delta == 700
        assert facts[0].estimated_cost_usd == "0.000000000000"

        # A storage-only day (no executions at all) still yields a fact row.
        async with session_factory() as session:
            written = await aggregate_day(session, day=_DAY_BEFORE, now=_NOW)
            await session.commit()
        assert written == 0
        assert await _read_facts(session_factory, _DAY_BEFORE) == []
    finally:
        await engine.dispose()


async def test_aggregate_day_rejects_incomplete_day(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            try:
                await aggregate_day(session, day=_NOW.date(), now=_NOW)
            except ValueError:
                pass
            else:
                raise AssertionError("incomplete day must be rejected")
            await session.rollback()
    finally:
        await engine.dispose()


async def test_scheduler_window_rewrites_trailing_days(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        await _seed_tenant(
            session_factory,
            org_id="org-3",
            project_id="prj-3",
            workflow_id="wf-echo3",
            version_id="ver-echo3",
            dsl=_echo_dsl(),
        )
        await _seed_execution(
            session_factory,
            execution_id="exec-window",
            workflow_id="wf-echo3",
            version_id="ver-echo3",
            status="succeeded",
            started_at=datetime.combine(_DAY_BEFORE, datetime.min.time(), tzinfo=UTC),
        )
        scheduler = UsageFactScheduler(
            session_factory,
            lookback_days=2,
            poll_seconds=60,
            owner_id="test",
        )
        assert complete_days_before(_NOW, count=2) == [_DAY_BEFORE, _DAY]
        written = await scheduler.aggregate_window(now=_NOW)
        assert written == 1
        facts = await _read_facts(session_factory, _DAY_BEFORE)
        assert len(facts) == 1
        assert facts[0].executions == 1

        # A late-finishing execution started in the window day is absorbed by
        # the next sweep rewriting the same day.
        await _seed_execution(
            session_factory,
            execution_id="exec-late",
            workflow_id="wf-echo3",
            version_id="ver-echo3",
            status="succeeded",
            started_at=datetime.combine(_DAY_BEFORE, datetime.min.time(), tzinfo=UTC)
            + timedelta(hours=3),
        )
        await scheduler.aggregate_window(now=_NOW)
        facts = await _read_facts(session_factory, _DAY_BEFORE)
        assert len(facts) == 1
        assert facts[0].executions == 2
    finally:
        await engine.dispose()


def _api_settings(tmp_path: Path) -> Settings:
    return _settings(
        tmp_path,
        auth_mode="token",
        admin_api_token="test-admin-token-usage-facts",
        rate_limit_default_requests=1000,
    )


def _admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-admin-token-usage-facts"}


def _seed_fact_rows(client: TestClient) -> dict[str, str]:
    """Create a real org/project via the API and insert fact rows directly."""
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "usage-admin@example.test",
            "password": "usage-password",
            "display_name": "Usage Admin",
            "role": "admin",
        },
    )
    assert registered.status_code == 201, registered.text
    org = client.post("/api/organizations", json={"name": "Usage Facts Org"})
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]
    project = client.post(
        f"/api/organizations/{org_id}/projects", json={"name": "Usage Facts Project"}
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    container = client.app.state.container

    async def _insert() -> None:
        async with container.session_factory() as session:
            session.add_all(
                [
                    UsageDailyFact(
                        organization_id=org_id,
                        project_id=project_id,
                        app_id=None,
                        model_config_id="model-a",
                        day=_DAY,
                        executions=2,
                        prompt_tokens=100,
                        completion_tokens=50,
                        total_tokens=150,
                        cost_unknown_executions=0,
                        storage_bytes_delta=0,
                        retrievals=0,
                        estimated_cost_usd="0.000000300000",
                    ),
                    UsageDailyFact(
                        organization_id=org_id,
                        project_id=project_id,
                        app_id=None,
                        model_config_id=None,
                        day=_DAY,
                        executions=1,
                        prompt_tokens=0,
                        completion_tokens=0,
                        total_tokens=0,
                        cost_unknown_executions=1,
                        storage_bytes_delta=2048,
                        retrievals=0,
                        estimated_cost_usd=None,
                    ),
                ]
            )
            await session.commit()

    asyncio.run(_insert())
    return {"org_id": org_id, "project_id": project_id}


def test_usage_daily_scope_authorization(tmp_path: Path) -> None:
    settings = _api_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        ids = _seed_fact_rows(client)

        # Unfiltered platform-wide view: the bootstrap admin session and the
        # static admin token are both platform-level admins.
        unfiltered_session = client.get(
            "/api/usage/daily",
            params={"from_day": _DAY.isoformat(), "to_day": _DAY.isoformat()},
        )
        assert unfiltered_session.status_code == 200
        assert len(unfiltered_session.json()["facts"]) == 2
        unfiltered = client.get(
            "/api/usage/daily",
            params={"from_day": _DAY.isoformat(), "to_day": _DAY.isoformat()},
            headers=_admin_headers(),
        )
        assert unfiltered.status_code == 200, unfiltered.text
        assert len(unfiltered.json()["facts"]) == 2

        scoped = client.get(
            "/api/usage/daily",
            params={
                "organization_id": ids["org_id"],
                "from_day": _DAY.isoformat(),
                "to_day": _DAY.isoformat(),
            },
        )
        assert scoped.status_code == 200, scoped.text
        assert len(scoped.json()["facts"]) == 2

        project_scoped = client.get(
            "/api/usage/daily",
            params={
                "project_id": ids["project_id"],
                "from_day": _DAY.isoformat(),
                "to_day": _DAY.isoformat(),
            },
        )
        assert project_scoped.status_code == 200
        assert len(project_scoped.json()["facts"]) == 2

        mismatched = client.get(
            "/api/usage/daily",
            params={
                "organization_id": "org-does-not-exist",
                "project_id": ids["project_id"],
                "from_day": _DAY.isoformat(),
                "to_day": _DAY.isoformat(),
            },
        )
        assert mismatched.status_code == 422

        reversed_window = client.get(
            "/api/usage/daily",
            params={
                "organization_id": ids["org_id"],
                "from_day": _DAY.isoformat(),
                "to_day": _DAY_BEFORE.isoformat(),
            },
        )
        assert reversed_window.status_code == 422

        missing_window = client.get("/api/usage/daily", params={"organization_id": ids["org_id"]})
        assert missing_window.status_code == 422

        # A non-admin user session: unscoped queries are rejected, and an
        # organization they are not a member of stays invisible (403).
        viewer = client.post(
            "/api/auth/register",
            json={
                "email": "usage-viewer@example.test",
                "password": "usage-password",
                "display_name": "Usage Viewer",
                "role": "viewer",
            },
        )
        assert viewer.status_code == 201, viewer.text
        # Non-bootstrap registrations do not issue a session; log in as the
        # viewer to switch the client's session cookie.
        viewer_login = client.post(
            "/api/auth/login",
            json={"email": "usage-viewer@example.test", "password": "usage-password"},
        )
        assert viewer_login.status_code == 200, viewer_login.text
        unscoped_viewer = client.get(
            "/api/usage/daily",
            params={"from_day": _DAY.isoformat(), "to_day": _DAY.isoformat()},
        )
        assert unscoped_viewer.status_code == 403
        foreign_org = client.get(
            "/api/usage/daily",
            params={
                "organization_id": ids["org_id"],
                "from_day": _DAY.isoformat(),
                "to_day": _DAY.isoformat(),
            },
        )
        assert foreign_org.status_code == 403

        # Back on the org admin session the scoped view still works.
        login = client.post(
            "/api/auth/login",
            json={"email": "usage-admin@example.test", "password": "usage-password"},
        )
        assert login.status_code == 200, login.text
        member_scoped = client.get(
            "/api/usage/daily",
            params={
                "organization_id": ids["org_id"],
                "from_day": _DAY.isoformat(),
                "to_day": _DAY.isoformat(),
            },
        )
        assert member_scoped.status_code == 200
        assert len(member_scoped.json()["facts"]) == 2


def test_usage_daily_etag_304(tmp_path: Path) -> None:
    settings = _api_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        ids = _seed_fact_rows(client)
        params = {
            "organization_id": ids["org_id"],
            "from_day": _DAY.isoformat(),
            "to_day": _DAY.isoformat(),
        }
        first = client.get("/api/usage/daily", params=params)
        assert first.status_code == 200
        etag = first.headers["etag"]
        assert etag
        replay = client.get("/api/usage/daily", params=params, headers={"if-none-match": etag})
        assert replay.status_code == 304
        assert replay.content == b""


def test_usage_export_csv_and_json(tmp_path: Path) -> None:
    settings = _api_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        ids = _seed_fact_rows(client)
        params = {
            "organization_id": ids["org_id"],
            "from_day": _DAY.isoformat(),
            "to_day": _DAY.isoformat(),
        }

        csv_response = client.get("/api/usage/export", params={**params, "format": "csv"})
        assert csv_response.status_code == 200
        assert csv_response.headers["content-type"].startswith("text/csv")
        assert "attachment" in csv_response.headers["content-disposition"]
        assert csv_response.headers.get("x-content-type-options") == "nosniff"
        rows = list(csv.reader(io.StringIO(csv_response.text)))
        header, data = rows[0], rows[1:]
        assert "organization_id" in header and "estimated_cost_usd" in header
        assert len(data) == 2
        model_row = next(row for row in data if row[header.index("model_config_id")] == "model-a")
        assert model_row[header.index("total_tokens")] == "150"
        assert model_row[header.index("estimated_cost_usd")] == "0.000000300000"

        json_response = client.get("/api/usage/export", params={**params, "format": "json"})
        assert json_response.status_code == 200
        payload = json_response.json()
        assert payload["from_day"] == _DAY.isoformat()
        assert len(payload["facts"]) == 2


def test_usage_reconciliation_digest_and_totals(tmp_path: Path) -> None:
    settings = _api_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        ids = _seed_fact_rows(client)
        params = {"month": _DAY.strftime("%Y-%m"), "organization_id": ids["org_id"]}

        first = client.get("/api/usage/reconciliation", params=params)
        assert first.status_code == 200, first.text
        payload = first.json()
        assert payload["month"] == "2026-08"
        assert len(payload["days"]) == 1
        assert payload["days"][0]["rows"] == 2
        assert payload["totals"]["executions"] == 3
        assert payload["totals"]["total_tokens"] == 150
        assert payload["totals"]["storage_bytes_delta"] == 2048
        assert payload["totals"]["estimated_cost_usd"] is None  # unknown row present
        assert payload["scope"]["organization_id"] == ids["org_id"]

        digest = payload["digest"]
        replay = client.get("/api/usage/reconciliation", params=params)
        assert replay.json()["digest"] == digest

        etag = first.headers["etag"]
        cached = client.get(
            "/api/usage/reconciliation", params=params, headers={"if-none-match": etag}
        )
        assert cached.status_code == 304

        empty_month = client.get(
            "/api/usage/reconciliation",
            params={"month": "2025-01", "organization_id": ids["org_id"]},
        )
        assert empty_month.status_code == 200
        assert empty_month.json()["days"] == []
        assert empty_month.json()["totals"]["executions"] == 0
        assert empty_month.json()["totals"]["estimated_cost_usd"] == "0"

        invalid_month = client.get("/api/usage/reconciliation", params={"month": "2026-13"})
        assert invalid_month.status_code == 422
