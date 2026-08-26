"""ETag conditional GET for high-frequency list endpoints (C6-4)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs=",
        log_level="WARNING",
    )


def test_list_workflows_sends_etag_and_304(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        first = client.get("/api/workflows")
        assert first.status_code == 200
        etag = first.headers.get("etag")
        assert etag is not None and etag.startswith('W/"')
        assert first.headers.get("vary") == "Cookie"

        second = client.get("/api/workflows", headers={"If-None-Match": etag})
        assert second.status_code == 304
        assert second.content == b""
        assert second.headers.get("etag") == etag


def test_etag_changes_when_data_changes(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        before = client.get("/api/workflows")
        created = client.post(
            "/api/workflows",
            json={
                "name": "ETag probe",
                "project_id": None,
                "dsl": {
                    "version": "1.0",
                    "name": "ETag probe",
                    "variables": [],
                    "nodes": [
                        {
                            "id": "start",
                            "type": "start",
                            "config": {"input_schema": []},
                            "position": {"x": 0, "y": 0},
                        },
                        {
                            "id": "end",
                            "type": "end",
                            "config": {"output_template": {}},
                            "position": {"x": 200, "y": 0},
                        },
                    ],
                    "edges": [
                        {"id": "e1", "source": "start", "target": "end"}
                    ],
                },
            },
        )
        assert created.status_code == 201, created.text
        after = client.get("/api/workflows")
        assert after.headers["etag"] != before.headers["etag"]
        # The stale validator no longer matches.
        stale = client.get(
            "/api/workflows", headers={"If-None-Match": before.headers["etag"]}
        )
        assert stale.status_code == 200


def test_node_types_and_providers_are_etagged(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        for path in ("/api/node-types", "/api/models/providers"):
            first = client.get(path)
            assert first.status_code == 200, path
            assert first.headers.get("etag"), path
            cached = client.get(path, headers={"If-None-Match": first.headers["etag"]})
            assert cached.status_code == 304, path


def test_executions_list_is_etagged(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/workflows/demo-linear/executions")
        assert response.status_code == 200
        assert response.headers.get("etag")
        cached = client.get(
            "/api/workflows/demo-linear/executions",
            headers={"If-None-Match": response.headers["etag"]},
        )
        assert cached.status_code == 304
