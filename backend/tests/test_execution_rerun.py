"""D2 failed-node rerun planning and compiler entry tests."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.engine.compiler import WorkflowCompiler
from app.main import create_app
from app.schemas.dsl import WorkflowDSL
from app.schemas.events import EventType
from app.services.execution_rerun import ExecutionRerunError, build_execution_rerun_plan
from tests.conftest import FakeChatProvider, FakeEmitter, make_ctx
from tests.test_auth import EDITOR_TOKEN, VIEWER_TOKEN, auth_settings, bearer


def _event(seq: int, event_type: str, node_id: str, payload: dict[str, Any] | None = None):
    return SimpleNamespace(
        seq=seq,
        event_type=event_type,
        node_id=node_id,
        payload_json=payload or {},
    )


def _dsl(nodes: list[dict[str, Any]], edges: list[tuple[str, str]]) -> WorkflowDSL:
    return WorkflowDSL.model_validate(
        {
            "name": "rerun",
            "nodes": nodes,
            "edges": [
                {"id": f"edge-{index}", "source": source, "target": target}
                for index, (source, target) in enumerate(edges)
            ],
        }
    )


def _linear(node: dict[str, Any] | None = None) -> WorkflowDSL:
    replay = node or {"id": "agent", "type": "agent", "config": {}}
    return _dsl(
        [
            {"id": "start", "type": "start", "config": {}},
            replay,
            {"id": "end", "type": "end", "config": {}},
        ],
        [("start", replay["id"]), (replay["id"], "end")],
    )


def test_plan_uses_latest_failed_attempt_and_completed_output_snapshots() -> None:
    events = [
        _event(
            1,
            "node_started",
            "agent",
            {"input": {"inputs": {"query": "old"}, "upstream_outputs": {"start": {}}}},
        ),
        _event(2, "node_failed", "agent", {"error": "old failure"}),
        _event(
            3,
            "node_started",
            "agent",
            {"input": {"inputs": {"query": "new"}, "upstream_outputs": {"start": {}}}},
        ),
        _event(4, "node_finished", "start", {"output": {"query": "new"}}),
        _event(5, "node_failed", "agent", {"error": "latest failure"}),
    ]

    plan = build_execution_rerun_plan(_linear(), events)

    assert plan.node_id == "agent"
    assert plan.node_outputs["start"] == {"query": "new"}
    assert plan.reachable_node_ids == frozenset({"agent", "end"})
    assert plan.reused_node_ids == ("start",)


def test_plan_rejects_truncated_failed_input_snapshot() -> None:
    events = [
        _event(1, "node_started", "agent", {"input": {"_truncated": True}}),
        _event(2, "node_failed", "agent"),
    ]
    with pytest.raises(ExecutionRerunError, match="input snapshot is truncated"):
        build_execution_rerun_plan(_linear(), events)


@pytest.mark.parametrize(
    ("node", "reason"),
    [
        ({"id": "replay", "type": "tool", "config": {}}, "tool calls"),
        ({"id": "replay", "type": "human", "config": {}}, "human approval"),
        (
            {
                "id": "replay",
                "type": "agent",
                "config": {"tools": [{"server_id": "srv", "tool_name": "write"}]},
            },
            "MCP tool calls",
        ),
        (
            {
                "id": "replay",
                "type": "agent",
                "config": {"memory": {"enabled": True}},
            },
            "memory writes",
        ),
        (
            {
                "id": "replay",
                "type": "iteration",
                "config": {
                    "items": "{{input.items}}",
                    "subgraph": {
                        "nodes": [
                            {"id": "start", "type": "start", "config": {}},
                            {
                                "id": "write",
                                "type": "tool",
                                "config": {
                                    "server_id": "srv",
                                    "tool_name": "write",
                                },
                            },
                            {"id": "end", "type": "end", "config": {}},
                        ],
                        "edges": [
                            {"id": "one", "source": "start", "target": "write"},
                            {"id": "two", "source": "write", "target": "end"},
                        ],
                    },
                },
            },
            "iteration child.*tool calls",
        ),
    ],
)
def test_plan_blocks_reachable_side_effect_nodes(node: dict[str, Any], reason: str) -> None:
    events = [
        _event(
            1,
            "node_started",
            "replay",
            {"input": {"inputs": {}, "upstream_outputs": {"start": {}}}},
        ),
        _event(2, "node_failed", "replay"),
    ]
    with pytest.raises(ExecutionRerunError, match=reason):
        build_execution_rerun_plan(_linear(node), events)


def test_plan_blocks_side_effect_worker_reached_only_by_supervisor_command() -> None:
    workflow = _dsl(
        [
            {"id": "start", "type": "start", "config": {}},
            {
                "id": "supervisor",
                "type": "agent",
                "config": {"agent_mode": "supervisor", "workers": ["writer"]},
            },
            {"id": "writer", "type": "tool", "config": {}},
            {"id": "end", "type": "end", "config": {}},
        ],
        [
            ("start", "supervisor"),
            ("supervisor", "end"),
            ("start", "writer"),
            ("writer", "end"),
        ],
    )
    events = [
        _event(
            1,
            "node_started",
            "supervisor",
            {"input": {"inputs": {}, "upstream_outputs": {"start": {}}}},
        ),
        _event(2, "node_failed", "supervisor"),
    ]

    with pytest.raises(ExecutionRerunError, match="writer.*tool calls"):
        build_execution_rerun_plan(workflow, events)


def test_plan_rejects_missing_non_replayed_join_snapshot() -> None:
    workflow = _dsl(
        [
            {"id": "start", "type": "start", "config": {}},
            {"id": "left", "type": "agent", "config": {}},
            {"id": "right", "type": "agent", "config": {}},
            {"id": "merge", "type": "agent", "config": {}},
            {"id": "end", "type": "end", "config": {}},
        ],
        [
            ("start", "left"),
            ("start", "right"),
            ("left", "merge"),
            ("right", "merge"),
            ("merge", "end"),
        ],
    )
    events = [
        _event(
            1,
            "node_started",
            "left",
            {"input": {"inputs": {}, "upstream_outputs": {"start": {}}}},
        ),
        _event(2, "node_failed", "left"),
        _event(3, "node_finished", "right", {"output": {"output": "too late"}}),
    ]
    with pytest.raises(ExecutionRerunError, match="right"):
        build_execution_rerun_plan(workflow, events)


async def test_compiler_reruns_parallel_branch_with_reused_sibling_snapshot(
    fake_emitter: FakeEmitter,
) -> None:
    workflow = _dsl(
        [
            {"id": "start", "type": "start", "config": {}},
            {"id": "left", "type": "agent", "config": {"user_prompt": "left"}},
            {"id": "right", "type": "agent", "config": {"user_prompt": "right"}},
            {
                "id": "merge",
                "type": "agent",
                "config": {"user_prompt": "{{nodes.left.output}} + {{nodes.right.output}}"},
            },
            {"id": "end", "type": "end", "config": {}},
        ],
        [
            ("start", "left"),
            ("start", "right"),
            ("left", "merge"),
            ("right", "merge"),
            ("merge", "end"),
        ],
    )
    provider = FakeChatProvider(["left replayed", "merged"])
    graph = WorkflowCompiler().compile(
        workflow,
        make_ctx(fake_emitter, provider),
        start_node_id="left",
    )
    state: dict[str, Any] = {
        "inputs": {},
        "node_outputs": {"right": {"output": "right reused"}},
        "loop_counts": {},
        "messages": [],
        "route": None,
        "error": None,
        "final_output": None,
    }

    result = await graph.ainvoke(state, config={"recursion_limit": 50})

    started = [event["node_id"] for event in fake_emitter.of_type(EventType.NODE_STARTED)]
    assert started == ["left", "merge", "end"]
    assert result["node_outputs"]["right"]["output"] == "right reused"
    assert "left replayed" in provider.calls[-1][-1].content
    assert "right reused" in provider.calls[-1][-1].content


def _wait_for_terminal(client: TestClient, execution_id: str, headers: dict[str, str]) -> dict:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(f"/api/executions/{execution_id}", headers=headers)
        assert response.status_code == 200, response.text
        execution = response.json()
        if execution["status"] in {"succeeded", "failed", "cancelled"}:
            return execution
        time.sleep(0.05)
    raise AssertionError(f"execution {execution_id} did not finish")


def test_rerun_http_preserves_version_provenance_and_starts_at_failed_node(tmp_path) -> None:
    editor = bearer(EDITOR_TOKEN)
    viewer = bearer(VIEWER_TOKEN)
    body = {
        "name": "Rerun probe",
        "dsl": {
            "version": "1.0",
            "name": "Rerun probe",
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {"id": "rag", "type": "rag", "config": {"kb_id": ""}},
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "start-rag", "source": "start", "target": "rag"},
                {"id": "rag-end", "source": "rag", "target": "end"},
            ],
        },
    }
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        workflow = client.post("/api/workflows", headers=editor, json=body)
        assert workflow.status_code == 201, workflow.text
        started = client.post(
            f"/api/workflows/{workflow.json()['id']}/run",
            headers=editor,
            json={"inputs": {"query": "rerun me"}},
        )
        assert started.status_code == 201, started.text
        source = _wait_for_terminal(client, started.json()["id"], viewer)
        assert source["status"] == "failed"

        assert (
            client.post(
                f"/api/executions/{source['id']}/rerun",
                headers=viewer,
                json={},
            ).status_code
            == 403
        )
        rerun_headers = {**editor, "Idempotency-Key": "rerun-probe"}
        rerun = client.post(
            f"/api/executions/{source['id']}/rerun",
            headers=rerun_headers,
            json={},
        )
        assert rerun.status_code == 201, rerun.text
        duplicate = client.post(
            f"/api/executions/{source['id']}/rerun",
            headers=rerun_headers,
            json={},
        )
        assert duplicate.status_code == 201, duplicate.text
        assert duplicate.json()["id"] == rerun.json()["id"]

        finished = _wait_for_terminal(client, rerun.json()["id"], viewer)
        assert finished["workflow_version_id"] == source["workflow_version_id"]
        assert finished["parent_execution_id"] == source["id"]
        assert finished["rerun_from_node_id"] == "rag"
        inspection = client.get(
            f"/api/executions/{finished['id']}/inspection", headers=viewer
        )
        assert inspection.status_code == 200, inspection.text
        assert inspection.json()["parent_execution_id"] == source["id"]
        assert inspection.json()["rerun_from_node_id"] == "rag"
        assert [attempt["node_id"] for attempt in inspection.json()["attempts"]] == ["rag"]
