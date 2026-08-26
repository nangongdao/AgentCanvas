"""HTTP acceptance paths for D2 durable cost alerts and governance."""

from __future__ import annotations

import time
from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.core.model_budget import (
    ModelCallConfig,
    ModelCallGate,
    ModelCallTokenBudgetExceeded,
)
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import CostAlert, Workflow
from app.db.repositories import ExecutionRepo
from app.engine.execution_runner import ExecutionRunnerMixin
from app.main import create_app
from app.providers.base import BaseChatProvider, ChatMessage, StreamChunk, Usage


def _settings(tmp_path, **overrides) -> Settings:
    base = dict(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs=",
        local_embedding_dimensions=64,
        rate_limit_execution_requests=100,
    )
    base.update(overrides)
    return Settings(**base)


def _wait_status(client: TestClient, execution_id: str, expected: set[str]) -> dict:
    deadline = time.monotonic() + 25
    current: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/executions/{execution_id}")
        if response.status_code == 429:
            time.sleep(response.json().get("retry_after", 1))
            continue
        assert response.status_code == 200, response.text
        current = response.json()
        if current["status"] in expected:
            return current
        time.sleep(0.1)
    raise AssertionError(f"execution did not reach {expected}: {current}")


def _default_model(client: TestClient) -> dict:
    return next(m for m in client.get("/api/models").json() if m["id"] == "default")


def test_cost_ceiling_fails_execution_and_persists_durable_alert(tmp_path) -> None:
    settings = _settings(tmp_path, model_max_cost_usd_per_execution="0.000001")
    with TestClient(create_app(settings)) as client:
        # Point the default chat model at the mock provider with a price list so
        # the execution budget can price each model call against the ceiling.
        updated = client.put(
            f"/api/models/{_default_model(client)['id']}",
            json={
                "provider": "mock",
                "prompt_price_per_million_usd": "10",
                "completion_price_per_million_usd": "20",
                "pricing_version": "price-2026-01",
            },
        )
        assert updated.status_code == 200, updated.text

        execution_id = client.post(
            "/api/workflows/demo-linear/run",
            json={"inputs": {"user_query": "hi"}},
        ).json()["id"]
        row = _wait_status(client, execution_id, {"failed"})
        assert "cost budget exceeded" in (row.get("error") or "")

        alerts = client.get("/api/cost-alerts?limit=50").json()["items"]
        cost_alerts = [a for a in alerts if a["kind"] == "cost"]
        assert len(cost_alerts) >= 1
        alert = cost_alerts[0]
        assert alert["severity"] == "critical"
        assert alert["status"] == "open"
        assert alert["execution_id"] == execution_id
        assert alert["limit_value"] == "0.000001"

        governance = client.get("/api/cost-alerts/governance").json()
        assert governance["max_cost_usd_per_execution"] == "0.000001"
        assert governance["max_tokens_per_execution"] == 0
        assert governance["max_concurrent_per_execution"] == 0
        assert governance["max_calls_per_execution"] == settings.model_max_calls_per_execution
        assert governance["summary"]["critical"] >= 1

        ack = client.post(f"/api/cost-alerts/{alert['id']}/ack")
        assert ack.status_code == 200, ack.text
        assert ack.json()["status"] == "acknowledged"
        again = client.post(f"/api/cost-alerts/{alert['id']}/ack")
        assert again.status_code == 200
        assert again.json()["status"] == "acknowledged"
        assert client.post("/api/cost-alerts/missing/ack").status_code == 404


def test_successful_execution_without_ceiling_leaves_no_critical_alert(tmp_path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        execution_id = client.post(
            "/api/workflows/demo-linear/run",
            json={"inputs": {"user_query": "hi"}},
        ).json()["id"]
        row = _wait_status(client, execution_id, {"succeeded", "failed"})
        assert row["status"] == "succeeded", row

        alerts = client.get("/api/cost-alerts?limit=50").json()["items"]
        assert all(a["severity"] != "critical" for a in alerts)

        governance = client.get("/api/cost-alerts/governance").json()
        assert governance["max_tokens_per_execution"] == 0
        assert governance["max_cost_usd_per_execution"] is None
        assert governance["summary"]["total"] == 0


def test_cost_alert_rbac_gates_governance_actions(tmp_path) -> None:
    settings = _settings(
        tmp_path,
        auth_mode="token",
        admin_api_token="admin-token-with-more-than-16-characters",
        editor_api_token="editor-token",
        viewer_api_token="viewer-token",
    )
    with TestClient(create_app(settings)) as client:
        viewer = {"Authorization": "Bearer viewer-token"}
        editor = {"Authorization": "Bearer editor-token"}
        admin = {"Authorization": "Bearer admin-token-with-more-than-16-characters"}

        assert client.get("/api/cost-alerts?limit=10").status_code == 401
        assert client.get("/api/cost-alerts/governance").status_code == 401

        assert client.get("/api/cost-alerts?limit=10", headers=viewer).status_code == 200
        assert client.get("/api/cost-alerts/governance", headers=viewer).status_code == 200

        assert client.post("/api/cost-alerts/some/ack", headers=viewer).status_code == 403
        # editor/admin pass RBAC; the missing alert id yields 404.
        assert client.post("/api/cost-alerts/some/ack", headers=editor).status_code == 404
        assert client.post("/api/cost-alerts/some/ack", headers=admin).status_code == 404


class _UsageProvider(BaseChatProvider):
    """Emits a scripted token usage chunk so a budget can be driven to a ceiling."""

    def __init__(
        self,
        *,
        prompt: int = 0,
        completion: int = 0,
        price_prompt: str | None = None,
    ) -> None:
        super().__init__(
            model="usage",
            prompt_price_per_million_usd=price_prompt,
        )
        self.prompt = prompt
        self.completion = completion

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools=(),
        **params,
    ):
        yield StreamChunk(
            type="usage",
            usage=Usage(self.prompt, self.completion, self.prompt + self.completion),
        )
        yield StreamChunk(type="done")


class _Runner(ExecutionRunnerMixin):
    """Minimal host exposing the budget-alert persistence helpers."""

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory


async def _severity_counts(settings: Settings) -> dict[str, int]:
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            rows = (await session.execute(select(CostAlert))).scalars().all()
            counts = {"critical": 0, "warning": 0}
            for row in rows:
                counts[row.severity] = counts.get(row.severity, 0) + 1
            return counts
    finally:
        await engine.dispose()


async def _seed_execution(settings: Settings, workflow_id: str) -> str:
    """Create a workflow + running execution row so alert FKs are satisfiable."""
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            session.add(
                Workflow(
                    id=workflow_id,
                    name="Budget probe",
                    dsl_json={"version": "1.0", "name": "Budget probe", "nodes": [], "edges": []},
                )
            )
            await session.flush()
            row = await ExecutionRepo(session).create(workflow_id, {}, "running")
            await session.commit()
            return row.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_persist_budget_alert_records_critical_breach(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    runner = _Runner(create_session_factory(create_engine(settings)))
    execution_id = await _seed_execution(settings, "workflow-1")

    gate = ModelCallGate(
        ModelCallConfig(
            max_concurrent=2,
            max_calls_per_execution=5,
            timeout_seconds=1,
            max_tokens_per_execution=1,
        )
    )
    budget = gate.new_budget()
    with pytest.raises(ModelCallTokenBudgetExceeded):
        await budget.chat(_UsageProvider(prompt=5, completion=5), [])

    assert budget.breach is not None
    assert await runner._persist_budget_alert(execution_id, "workflow-1", budget) is True
    assert await runner._persist_budget_alert(execution_id, "workflow-1", None) is False

    counts = await _severity_counts(settings)
    assert counts == {"critical": 1, "warning": 0}


@pytest.mark.asyncio
async def test_persist_budget_warnings_records_soft_ceilings(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    runner = _Runner(create_session_factory(create_engine(settings)))
    execution_id = await _seed_execution(settings, "workflow-1")

    gate = ModelCallGate(
        ModelCallConfig(
            max_concurrent=2,
            max_calls_per_execution=5,
            timeout_seconds=1,
            max_tokens_per_execution=10,
            max_cost_usd_per_execution="0.000001",
        )
    )
    budget = gate.new_budget()
    # 8 of 10 tokens (80%) and 8e-7 of 1e-6 USD (80%).
    await budget.chat(_UsageProvider(prompt=8, price_prompt="0.1"), [])

    assert await runner._persist_budget_warnings(execution_id, "workflow-1", budget) == 2
    assert await runner._persist_budget_warnings(execution_id, "workflow-1", None) == 0

    counts = await _severity_counts(settings)
    assert counts == {"critical": 0, "warning": 2}
