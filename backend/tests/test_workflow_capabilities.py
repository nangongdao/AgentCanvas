"""Workflow capability derivation and write-boundary enforcement."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.models import ModelConfig
from app.main import create_app
from app.schemas.dsl import AgentConfig, WorkflowDSL
from app.services.workflow_capabilities import (
    model_chain_ids,
    required_agent_capabilities,
    workflow_model_ids,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs=",
    )


def _agent_dsl(model_id: str, **config: Any) -> dict[str, Any]:
    agent_config = {"model_config_id": model_id, **config}
    return {
        "version": "1.0",
        "name": "Capability workflow",
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}},
            {
                "id": "agent",
                "type": "agent",
                "position": {"x": 160, "y": 0},
                "config": agent_config,
            },
            {"id": "end", "type": "end", "position": {"x": 320, "y": 0}},
        ],
        "edges": [
            {"id": "start-agent", "source": "start", "target": "agent"},
            {"id": "agent-end", "source": "agent", "target": "end"},
        ],
    }


def _create_model(
    client: TestClient,
    model_id: str,
    provider: str,
    *,
    kind: str = "chat",
) -> None:
    response = client.post(
        "/api/models",
        json={
            "id": model_id,
            "name": model_id,
            "provider": provider,
            "model_name": model_id,
            "kind": kind,
        },
    )
    assert response.status_code == 201, response.text


def _assert_capability_error(
    response,
    *,
    model_id: str,
    missing: list[str] | None = None,
    code: str = "capability_mismatch",
) -> None:
    assert response.status_code == 422, response.text
    errors = response.json()["detail"]
    assert len(errors) == 1
    assert errors[0]["node_id"] == "agent"
    assert errors[0]["model_config_id"] == model_id
    assert errors[0]["code"] == code
    if missing is not None:
        assert errors[0]["missing"] == missing


def test_agent_requirements_are_derived_from_real_config() -> None:
    assert required_agent_capabilities(AgentConfig()) == frozenset({"stream"})
    assert required_agent_capabilities(
        AgentConfig.model_validate(
            {
                "tools": [{"server_id": "mcp", "tool_name": "search"}],
                "output": {"format": "json"},
                "params": {"reasoning_effort": "high"},
                "requirements": {"cost": True},
            }
        )
    ) == frozenset({"stream", "tools", "json_mode", "reasoning", "usage", "cost"})
    assert required_agent_capabilities(
        AgentConfig.model_validate(
            {
                "params": {
                    "response_format": {"type": "json_schema"},
                    "reasoning": "off",
                }
            }
        )
    ) == frozenset({"stream", "json_mode"})
    assert "json_mode" in required_agent_capabilities(AgentConfig(agent_mode="supervisor"))


def test_model_fallback_chain_is_ordered_unique_and_discoverable() -> None:
    config = AgentConfig(
        model_config_id="primary",
        fallback_model_config_ids=["fallback-a", "fallback-b"],
    )
    assert model_chain_ids(config) == ("primary", "fallback-a", "fallback-b")
    dsl = WorkflowDSL.model_validate(
        _agent_dsl(
            "primary",
            fallback_model_config_ids=["fallback-a", "fallback-b"],
        )
    )
    assert workflow_model_ids(dsl) == frozenset({"primary", "fallback-a", "fallback-b"})
    with pytest.raises(ValueError, match="duplicate"):
        AgentConfig(
            model_config_id="primary",
            fallback_model_config_ids=["primary"],
        )


def test_iteration_subgraph_models_are_discoverable() -> None:
    raw = _agent_dsl("outer")
    raw["nodes"][1] = {
        "id": "each",
        "type": "iteration",
        "config": {
            "items": "{{input.items}}",
            "subgraph": {
                "nodes": _agent_dsl("nested")["nodes"],
                "edges": _agent_dsl("nested")["edges"],
            },
        },
    }

    assert workflow_model_ids(WorkflowDSL.model_validate(raw)) == frozenset({"nested"})


def test_fallback_models_are_capability_checked_before_workflow_write(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _create_model(client, "primary-openai", "openai_compat")
        _create_model(client, "fallback-limited", "mock")

        missing = client.post(
            "/api/workflows",
            json={
                "name": "Missing fallback",
                "dsl": _agent_dsl(
                    "primary-openai",
                    fallback_model_config_ids=["missing-fallback"],
                ),
            },
        )
        _assert_capability_error(
            missing,
            model_id="missing-fallback",
            code="model_not_found",
        )

        insufficient = client.post(
            "/api/workflows",
            json={
                "name": "Insufficient fallback",
                "dsl": _agent_dsl(
                    "primary-openai",
                    fallback_model_config_ids=["fallback-limited"],
                    tools=[{"server_id": "mcp", "tool_name": "search"}],
                ),
            },
        )
        _assert_capability_error(
            insufficient,
            model_id="fallback-limited",
            missing=["tools"],
        )


def test_missing_wrong_kind_unknown_and_insufficient_models_fail_per_node(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        _create_model(client, "mock-limited", "mock")
        _create_model(client, "embedding-only", "openai_compat", kind="embedding")
        _create_model(client, "unknown-provider", "mock")

        async def corrupt_provider() -> None:
            container = cast(FastAPI, client.app).state.container
            async with container.session_factory() as session:
                row = await session.get(ModelConfig, "unknown-provider")
                assert row is not None
                row.provider = "unregistered"
                await session.commit()

        asyncio.run(corrupt_provider())

        missing = client.post(
            "/api/workflows",
            json={"name": "Missing", "dsl": _agent_dsl("missing-model")},
        )
        _assert_capability_error(missing, model_id="missing-model", code="model_not_found")

        wrong_kind = client.post(
            "/api/workflows",
            json={"name": "Embedding", "dsl": _agent_dsl("embedding-only")},
        )
        _assert_capability_error(wrong_kind, model_id="embedding-only", code="model_not_chat")

        unknown = client.post(
            "/api/workflows",
            json={"name": "Unknown", "dsl": _agent_dsl("unknown-provider")},
        )
        _assert_capability_error(unknown, model_id="unknown-provider", code="invalid_model_profile")

        tools = client.post(
            "/api/workflows",
            json={
                "name": "Tools",
                "dsl": _agent_dsl(
                    "mock-limited",
                    tools=[{"server_id": "mcp", "tool_name": "search"}],
                ),
            },
        )
        _assert_capability_error(tools, model_id="mock-limited", missing=["tools"])


def test_json_reasoning_and_cost_requirements_fail_closed(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _create_model(client, "anthropic-limited", "anthropic")
        _create_model(client, "unpriced-openai", "openai_compat")

        json_response = client.post(
            "/api/workflows",
            json={
                "name": "JSON",
                "dsl": _agent_dsl("anthropic-limited", output={"format": "json"}),
            },
        )
        _assert_capability_error(json_response, model_id="anthropic-limited", missing=["json_mode"])

        reasoning_response = client.post(
            "/api/workflows",
            json={
                "name": "Reasoning",
                "dsl": _agent_dsl("anthropic-limited", params={"reasoning_effort": "high"}),
            },
        )
        _assert_capability_error(
            reasoning_response, model_id="anthropic-limited", missing=["reasoning"]
        )

        cost_response = client.post(
            "/api/workflows",
            json={
                "name": "Cost",
                "dsl": _agent_dsl("unpriced-openai", requirements={"cost": True}),
            },
        )
        _assert_capability_error(cost_response, model_id="unpriced-openai", missing=["cost"])


def test_every_workflow_write_boundary_rejects_narrowed_model_without_writes(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _create_model(client, "write-gate-model", "openai_compat")
        created = client.post(
            "/api/workflows",
            json={
                "name": "Write gate",
                "dsl": _agent_dsl("write-gate-model"),
            },
        )
        assert created.status_code == 201, created.text
        workflow = created.json()
        workflow_id = workflow["id"]
        version_one = client.get(f"/api/workflows/{workflow_id}/versions").json()[0]

        template = client.post(
            "/api/workflow-templates",
            json={
                "name": "Write gate template",
                "category": "test",
                "workflow_id": workflow_id,
                "version_id": version_one["id"],
            },
        )
        assert template.status_code == 201, template.text

        advanced = client.put(
            f"/api/workflows/{workflow_id}",
            json={"name": "Write gate v2", "version": 1},
        )
        assert advanced.status_code == 200, advanced.text
        assert advanced.json()["version"] == 2

        narrowed = client.put(
            "/api/models/write-gate-model",
            json={"capability_overrides": {"stream": False}},
        )
        assert narrowed.status_code == 200, narrowed.text

        versions_before = client.get(f"/api/workflows/{workflow_id}/versions").json()
        workflows_before = [
            row["id"] for row in client.get("/api/workflows", params={"limit": 200}).json()["items"]
        ]
        audits_before = [
            row["id"]
            for row in client.get("/api/audit-logs", params={"limit": 200}).json()["items"]
        ]
        base_version = next(row for row in versions_before if row["number"] == 1)
        failures = [
            client.post(
                "/api/workflows",
                json={"name": "Rejected create", "dsl": _agent_dsl("write-gate-model")},
            ),
            client.put(
                f"/api/workflows/{workflow_id}",
                json={"name": "Rejected update", "version": 2},
            ),
            client.post(
                "/api/workflows/import",
                json={
                    "format": "agentcanvas-workflow",
                    "format_version": 1,
                    "name": "Rejected import",
                    "dsl": _agent_dsl("write-gate-model"),
                },
            ),
            client.post(
                f"/api/workflow-templates/{template.json()['id']}/instantiate",
                json={"name": "Rejected template"},
            ),
            client.post(f"/api/workflows/{workflow_id}/publish"),
            client.post(
                f"/api/workflows/{workflow_id}/versions/{base_version['id']}/rollback",
                json={"change_summary": "Rejected rollback"},
            ),
            client.post(
                f"/api/workflows/{workflow_id}/clone",
                json={"version_id": base_version["id"], "name": "Rejected clone"},
            ),
            client.post(
                f"/api/workflows/{workflow_id}/versions/merge",
                json={
                    "base_version_id": base_version["id"],
                    "remote_version": 2,
                    "local_name": base_version["name"],
                    "local_dsl": deepcopy(base_version["dsl"]),
                },
            ),
        ]
        for response in failures:
            _assert_capability_error(response, model_id="write-gate-model", missing=["stream"])

        current = client.get(f"/api/workflows/{workflow_id}").json()
        assert current["version"] == 2
        assert current["name"] == "Write gate v2"
        assert client.get(f"/api/workflows/{workflow_id}/versions").json() == versions_before
        assert [
            row["id"] for row in client.get("/api/workflows", params={"limit": 200}).json()["items"]
        ] == workflows_before
        assert [
            row["id"]
            for row in client.get("/api/audit-logs", params={"limit": 200}).json()["items"]
        ] == audits_before
