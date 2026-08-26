"""D2 node-attempt inspection and redaction tests."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.execution_inspection import (
    MAX_NODE_SNAPSHOT_BYTES,
    bounded_json_snapshot,
    build_execution_inspection,
)
from tests.test_auth import (
    EDITOR_TOKEN,
    VIEWER_TOKEN,
    auth_settings,
    bearer,
    workflow_body,
)


def _event(
    seq: int,
    event_type: str,
    *,
    node_id: str | None = "agent",
    payload: dict | None = None,
    offset_ms: int = 0,
):
    return SimpleNamespace(
        seq=seq,
        event_type=event_type,
        node_id=node_id,
        payload_json=payload or {},
        ts=datetime(2026, 8, 5, tzinfo=UTC) + timedelta(milliseconds=offset_ms),
    )


def test_event_log_rebuilds_redacted_attempts_and_usage() -> None:
    events = [
        _event(
            1,
            "node_started",
            payload={"input": {"inputs": {"api_key": "sk-secret", "query": "hello"}}},
        ),
        _event(
            2,
            "node_finished",
            payload={
                "elapsed_ms": 42,
                "output": {
                    "text": "done",
                    "access_token": "private",
                    "meta": {"usage": {"prompt": 12, "completion": 5, "total": 17}},
                },
            },
            offset_ms=42,
        ),
        _event(3, "node_started", payload={"input": {"inputs": {"query": "retry"}}}),
        _event(
            4,
            "node_failed",
            payload={"elapsed_ms": 7, "error": "Bearer abc.def.ghi was rejected"},
            offset_ms=49,
        ),
    ]

    result = build_execution_inspection(
        events,
        node_metadata={"agent": {"type": "agent", "label": "Answer"}},
    )

    first, second = result["attempts"]
    assert (first["attempt"], first["status"], first["duration_ms"]) == (1, "succeeded", 42)
    assert first["input"]["inputs"]["api_key"] == "[REDACTED]"
    assert first["output"]["access_token"] == "[REDACTED]"
    assert (first["prompt_tokens"], first["completion_tokens"], first["total_tokens"]) == (
        12,
        5,
        17,
    )
    assert (second["attempt"], second["status"], second["duration_ms"]) == (2, "failed", 7)
    assert second["error"] == "Bearer [REDACTED] was rejected"
    assert result["total_duration_ms"] == 49
    assert result["total_tokens"] == 17


def test_iteration_node_path_resolves_nested_inspection_metadata() -> None:
    events = [
        _event(
            1,
            "node_started",
            node_id="each[2].agent[0]",
            payload={"node_path": "each.agent[0]", "input": {}},
        ),
        _event(
            2,
            "node_finished",
            node_id="each[2].agent[0]",
            payload={"node_path": "each.agent[0]", "output": {"text": "done"}},
        ),
    ]

    result = build_execution_inspection(
        events,
        node_metadata={
            "each.agent[0]": {"type": "agent", "label": "Nested answer"}
        },
    )

    attempt = result["attempts"][0]
    assert attempt["node_id"] == "each[2].agent[0]"
    assert attempt["node_type"] == "agent"
    assert attempt["node_label"] == "Nested answer"


def test_oversized_snapshot_keeps_only_digest() -> None:
    value = {"payload": "x" * (MAX_NODE_SNAPSHOT_BYTES + 1)}
    snapshot = bounded_json_snapshot(value)
    assert snapshot["_truncated"] is True
    assert snapshot["bytes"] > MAX_NODE_SNAPSHOT_BYTES
    assert len(snapshot["sha256"]) == 64
    assert "payload" not in snapshot


def test_execution_inspection_over_http_is_versioned_and_redacted(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        editor = bearer(EDITOR_TOKEN)
        viewer = bearer(VIEWER_TOKEN)
        workflow = client.post("/api/workflows", headers=editor, json=workflow_body()).json()
        started = client.post(
            f"/api/workflows/{workflow['id']}/run",
            headers=editor,
            json={"inputs": {"api_key": "secret-value", "query": "inspect me"}},
        )
        assert started.status_code == 201, started.text

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            current = client.get(f"/api/executions/{started.json()['id']}", headers=viewer)
            if current.json()["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("execution did not finish")

        response = client.get(
            f"/api/executions/{started.json()['id']}/inspection",
            headers=viewer,
        )
        assert response.status_code == 200, response.text
        inspection = response.json()
        assert inspection["redacted"] is True
        assert inspection["workflow_version_id"] == started.json()["workflow_version_id"]
        assert inspection["workflow_version_number"] == 1
        assert [(item["node_id"], item["status"]) for item in inspection["attempts"]] == [
            ("start", "succeeded"),
            ("end", "succeeded"),
        ]
        serialized = json.dumps(inspection)
        assert "secret-value" not in serialized
        assert serialized.count("[REDACTED]") >= 2
        assert client.get(f"/api/executions/{started.json()['id']}/inspection").status_code == 401
