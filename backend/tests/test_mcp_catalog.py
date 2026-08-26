"""D4 Phase 3 approved MCP catalog and binding contract tests."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.core.config import BACKEND_DIR, Settings
from app.main import create_app

SECRET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key=SECRET_KEY,
    )
    return replace(settings, **cast(Any, overrides))


def _manifest(*, command: str | None = None) -> dict:
    return {
        "transport": "stdio",
        "permissions": {
            "network": [],
            "filesystem": [str(BACKEND_DIR / "mcp_servers")],
            "commands": [command or Path(sys.executable).name],
        },
    }


def _entry_body(entry_id: str, *, version: str = "1.0.0", command: str | None = None) -> dict:
    return {
        "id": entry_id,
        "name": "Catalog Test Server",
        "description": "A bounded catalog fixture",
        "source_url": "https://catalog.example.test/server",
        "version": version,
        "source_ref": f"https://catalog.example.test/server/{version}",
        "manifest": _manifest(command=command),
    }


def test_catalog_approval_upgrade_binding_and_runtime_revoke(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        initial = client.get("/api/mcp/catalog")
        assert initial.status_code == 200, initial.text
        builtin = {item["id"]: item for item in initial.json()}
        assert set(builtin) >= {"builtin-calculator", "builtin-filesystem", "builtin-websearch"}
        assert builtin["builtin-calculator"]["versions"][0]["status"] == "approved"

        created = client.post("/api/mcp/catalog", json=_entry_body("catalog-fixture"))
        assert created.status_code == 201, created.text
        first = created.json()["versions"][0]
        assert first["status"] == "draft"

        draft_binding = client.put(
            "/api/mcp/servers/demo-calculator/catalog",
            json={"version_id": first["id"]},
        )
        assert draft_binding.status_code == 409, draft_binding.text

        approved = client.post(
            f"/api/mcp/catalog/catalog-fixture/versions/{first['id']}/approve"
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved"

        version_two = client.post(
            "/api/mcp/catalog/catalog-fixture/versions",
            json={
                "version": "2.0.0",
                "source_ref": "https://catalog.example.test/server/2.0.0",
                "manifest": _manifest(),
            },
        )
        assert version_two.status_code == 201, version_two.text
        second = version_two.json()
        approved_two = client.post(
            f"/api/mcp/catalog/catalog-fixture/versions/{second['id']}/approve"
        )
        assert approved_two.status_code == 200, approved_two.text
        assert approved_two.json()["status"] == "approved"

        entry = client.get("/api/mcp/catalog/catalog-fixture").json()
        statuses = {version["version"]: version["status"] for version in entry["versions"]}
        assert statuses == {"1.0.0": "superseded", "2.0.0": "approved"}

        bound = client.put(
            "/api/mcp/servers/demo-calculator/catalog",
            json={"version_id": second["id"]},
        )
        assert bound.status_code == 200, bound.text
        assert bound.json()["catalog_entry_id"] == "catalog-fixture"
        assert bound.json()["catalog_version_id"] == second["id"]

        revoked = client.post(
            f"/api/mcp/catalog/catalog-fixture/versions/{second['id']}/revoke"
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["status"] == "revoked"

        health = client.get("/api/mcp/servers/demo-calculator/health")
        assert health.status_code == 200, health.text
        assert health.json()["state"] == "degraded"
        assert "not approved" in health.json()["error"]

        history = client.get("/api/mcp/catalog/catalog-fixture/history")
        assert history.status_code == 200, history.text
        assert {item["action"] for item in history.json()} == {
            "revoked",
            "upgraded",
            "created",
            "approved",
        }
        assert len(history.json()) == 5
        assert "secret" not in history.text.casefold()


def test_catalog_binding_rejects_undeclared_command(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        created = client.post(
            "/api/mcp/catalog",
            json=_entry_body("catalog-denied", command="node"),
        )
        assert created.status_code == 201, created.text
        version = created.json()["versions"][0]
        approved = client.post(
            f"/api/mcp/catalog/catalog-denied/versions/{version['id']}/approve"
        )
        assert approved.status_code == 200, approved.text
        denied = client.put(
            "/api/mcp/servers/demo-calculator/catalog",
            json={"version_id": version["id"]},
        )
        assert denied.status_code == 422, denied.text
        assert "command" in denied.json()["detail"]


def test_catalog_mutations_require_admin(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        auth_mode="token",
        admin_api_token="catalog-admin-token-20260808",
        editor_api_token="catalog-editor-token-20260808",
        viewer_api_token="catalog-viewer-token-20260808",
    )
    with TestClient(create_app(settings)) as client:
        body = _entry_body("catalog-rbac")
        editor = client.post(
            "/api/mcp/catalog",
            headers={"Authorization": "Bearer catalog-editor-token-20260808"},
            json=body,
        )
        assert editor.status_code == 403, editor.text
        admin = client.post(
            "/api/mcp/catalog",
            headers={"Authorization": "Bearer catalog-admin-token-20260808"},
            json=body,
        )
        assert admin.status_code == 201, admin.text
        version = admin.json()["versions"][0]
        approved = client.post(
            f"/api/mcp/catalog/catalog-rbac/versions/{version['id']}/approve",
            headers={"Authorization": "Bearer catalog-admin-token-20260808"},
        )
        assert approved.status_code == 200, approved.text
        for suffix in ("rollout/preview", "rollout"):
            denied = client.post(
                f"/api/mcp/catalog/catalog-rbac/{suffix}",
                headers={"Authorization": "Bearer catalog-editor-token-20260808"},
                json={"version_id": version["id"]},
            )
            assert denied.status_code == 403, denied.text
        viewer = client.get(
            "/api/mcp/catalog",
            headers={"Authorization": "Bearer catalog-viewer-token-20260808"},
        )
        assert viewer.status_code == 200, viewer.text


def test_catalog_diff_preview_and_controlled_rollout(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        created = client.post("/api/mcp/catalog", json=_entry_body("catalog-rollout"))
        assert created.status_code == 201, created.text
        first = created.json()["versions"][0]
        approved_first = client.post(
            f"/api/mcp/catalog/catalog-rollout/versions/{first['id']}/approve"
        )
        assert approved_first.status_code == 200, approved_first.text

        for server_id in ("demo-calculator", "demo-filesystem"):
            bound = client.put(
                f"/api/mcp/servers/{server_id}/catalog",
                json={"version_id": first["id"]},
            )
            assert bound.status_code == 200, bound.text

        second_body = _entry_body("catalog-rollout", version="2.0.0")
        second_body["manifest"]["permissions"]["network"] = ["api.example.test"]
        created_second = client.post(
            "/api/mcp/catalog/catalog-rollout/versions",
            json={
                "version": second_body["version"],
                "source_ref": second_body["source_ref"],
                "manifest": second_body["manifest"],
            },
        )
        assert created_second.status_code == 201, created_second.text
        second = created_second.json()
        approved_second = client.post(
            f"/api/mcp/catalog/catalog-rollout/versions/{second['id']}/approve"
        )
        assert approved_second.status_code == 200, approved_second.text

        diff = client.get(
            f"/api/mcp/catalog/catalog-rollout/versions/{second['id']}/diff",
            params={"from_version_id": first["id"]},
        )
        assert diff.status_code == 200, diff.text
        assert diff.json()["changed_fields"] == ["source_ref", "permissions"]
        assert diff.json()["permission_added"]["network"] == ["api.example.test"]
        assert diff.json()["permission_removed"]["network"] == []

        default_diff = client.get(
            f"/api/mcp/catalog/catalog-rollout/versions/{second['id']}/diff"
        )
        assert default_diff.status_code == 200, default_diff.text
        assert default_diff.json()["from_version_id"] == first["id"]
        assert default_diff.json()["changed_fields"] == diff.json()["changed_fields"]

        preview = client.post(
            "/api/mcp/catalog/catalog-rollout/rollout/preview",
            json={"version_id": second["id"]},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["compatible_count"] == 2
        assert preview.json()["incompatible_count"] == 0
        assert {row["server_id"] for row in preview.json()["servers"]} == {
            "demo-calculator",
            "demo-filesystem",
        }

        one_server_rollout = client.post(
            "/api/mcp/catalog/catalog-rollout/rollout",
            json={"version_id": second["id"], "server_ids": ["demo-calculator"]},
        )
        assert one_server_rollout.status_code == 200, one_server_rollout.text
        assert one_server_rollout.json()["updated_server_ids"] == ["demo-calculator"]
        assert one_server_rollout.json()["skipped"] == []

        all_rollout = client.post(
            "/api/mcp/catalog/catalog-rollout/rollout",
            json={"version_id": second["id"]},
        )
        assert all_rollout.status_code == 200, all_rollout.text
        assert all_rollout.json()["updated_server_ids"] == ["demo-filesystem"]
        assert all_rollout.json()["unchanged_server_ids"] == ["demo-calculator"]

        servers = {row["id"]: row for row in client.get("/api/mcp/servers").json()}
        assert servers["demo-calculator"]["catalog_version_id"] == second["id"]
        assert servers["demo-filesystem"]["catalog_version_id"] == second["id"]

        history = client.get("/api/mcp/catalog/catalog-rollout/history")
        assert history.status_code == 200, history.text
        assert {row["action"] for row in history.json()} >= {"rollout", "upgraded"}

        audit = client.get("/api/audit-logs", params={"limit": 100})
        assert audit.status_code == 200, audit.text
        actions = [row["action"] for row in audit.json()["items"]]
        assert actions.count("mcp_catalog.rollout") == 2
        assert actions.count("mcp_server.catalog_rollout") == 2
        assert "secret" not in audit.text.casefold()


def test_catalog_rollout_preview_skips_policy_incompatible_servers(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        created = client.post("/api/mcp/catalog", json=_entry_body("catalog-blocked"))
        assert created.status_code == 201, created.text
        first = created.json()["versions"][0]
        assert client.post(
            f"/api/mcp/catalog/catalog-blocked/versions/{first['id']}/approve"
        ).status_code == 200
        assert client.put(
            "/api/mcp/servers/demo-calculator/catalog",
            json={"version_id": first["id"]},
        ).status_code == 200

        blocked_body = _entry_body("catalog-blocked", version="2.0.0", command="node")
        created_second = client.post(
            "/api/mcp/catalog/catalog-blocked/versions",
            json={
                "version": blocked_body["version"],
                "source_ref": blocked_body["source_ref"],
                "manifest": blocked_body["manifest"],
            },
        )
        assert created_second.status_code == 201, created_second.text
        second = created_second.json()
        assert client.post(
            f"/api/mcp/catalog/catalog-blocked/versions/{second['id']}/approve"
        ).status_code == 200

        preview = client.post(
            "/api/mcp/catalog/catalog-blocked/rollout/preview",
            json={"version_id": second["id"]},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["compatible_count"] == 0
        assert preview.json()["incompatible_count"] == 1
        assert "command" in preview.json()["servers"][0]["reason"]

        rollout = client.post(
            "/api/mcp/catalog/catalog-blocked/rollout",
            json={"version_id": second["id"]},
        )
        assert rollout.status_code == 200, rollout.text
        assert rollout.json()["updated_server_ids"] == []
        assert rollout.json()["skipped"][0]["server_id"] == "demo-calculator"
