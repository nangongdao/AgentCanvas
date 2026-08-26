"""Versioned DSL normalization and portable import/export tests."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.engine.validation import validate_dsl
from app.main import create_app
from app.schemas.dsl import WorkflowDSL
from app.services.dsl_migrations import normalize_workflow_dsl
from tests.test_auth import EDITOR_TOKEN, VIEWER_TOKEN, auth_settings, bearer

LEGACY_FIXTURE = Path(__file__).parent / "fixtures" / "workflow_dsl_0_9.json"


def _legacy_dsl() -> dict:
    return json.loads(LEGACY_FIXTURE.read_text(encoding="utf-8"))


def test_legacy_dsl_migrates_without_semantic_drift() -> None:
    normalized = normalize_workflow_dsl(_legacy_dsl())
    dsl = WorkflowDSL.model_validate(normalized)
    validate_dsl(dsl)

    assert dsl.version == "1.0"
    assert dsl.name == "Legacy handoff"
    assert dsl.settings.timeout_seconds == 45
    assert dsl.settings.max_loop_iterations == 20
    assert [(node.id, node.type, node.position.x, node.position.y) for node in dsl.nodes] == [
        ("start", "start", 10.0, 20.0),
        ("end", "end", 310.0, 20.0),
    ]
    assert dsl.edges[0].source_handle is None
    assert dsl.edges[0].target_handle is None
    assert dsl.nodes[1].config["output_template"] == {"answer": "{{input.query}}"}


def test_import_export_round_trip_and_unknown_version_rejection(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        imported = client.post(
            "/api/workflows/import",
            headers=bearer(EDITOR_TOKEN),
            json={
                "format": "agentcanvas-workflow",
                "format_version": 1,
                "name": "Imported legacy flow",
                "description": "Migration fixture",
                "dsl": _legacy_dsl(),
            },
        )
        assert imported.status_code == 201, imported.text
        workflow = imported.json()
        assert workflow["name"] == "Imported legacy flow"
        assert workflow["dsl"]["name"] == "Imported legacy flow"
        assert workflow["dsl"]["version"] == "1.0"

        versions = client.get(
            f"/api/workflows/{workflow['id']}/versions",
            headers=bearer(VIEWER_TOKEN),
        ).json()
        exported = client.get(
            f"/api/workflows/{workflow['id']}/versions/{versions[0]['id']}/export",
            headers=bearer(VIEWER_TOKEN),
        )
        assert exported.status_code == 200, exported.text
        bundle = exported.json()
        assert bundle["format"] == "agentcanvas-workflow"
        assert bundle["format_version"] == 1
        assert bundle["source_version_number"] == 1
        assert bundle["dsl"] == workflow["dsl"]

        round_trip = client.post(
            "/api/workflows/import",
            headers=bearer(EDITOR_TOKEN),
            json={**bundle, "name": "Round trip copy"},
        )
        assert round_trip.status_code == 201, round_trip.text
        assert round_trip.json()["dsl"] == {
            **workflow["dsl"],
            "name": "Round trip copy",
        }

        invalid = _legacy_dsl()
        invalid["version"] = "99.0"
        rejected = client.post(
            "/api/workflows/import",
            headers=bearer(EDITOR_TOKEN),
            json={"dsl": invalid},
        )
        assert rejected.status_code == 400
        assert "unsupported workflow DSL version" in rejected.text
