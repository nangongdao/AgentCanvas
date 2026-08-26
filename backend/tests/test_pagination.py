"""HTTP contract tests for bounded cursor-paginated list APIs."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _workflow_body(name: str, description: str = "") -> dict:
    return {
        "name": name,
        "description": description,
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
                {"id": "end", "type": "end", "position": {"x": 200, "y": 0}},
            ],
            "edges": [{"id": "edge", "source": "start", "target": "end"}],
        },
    }


def test_workflow_pages_search_and_keep_equal_sort_values(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, environment="test", auth_mode="disabled")
    with TestClient(create_app(settings)) as client:
        created_ids = {
            client.post(
                "/api/workflows",
                json=_workflow_body("Same name", f"target {index}"),
            ).json()["id"]
            for index in range(3)
        }
        client.post(
            "/api/workflows",
            json=_workflow_body("Different", "must not match"),
        )

        first = client.get(
            "/api/workflows",
            params={
                "limit": 2,
                "sort": "name",
                "order": "asc",
                "search": "target",
            },
        )
        assert first.status_code == 200, first.text
        first_page = first.json()
        assert set(first_page) == {"items", "next_cursor", "has_more"}
        assert len(first_page["items"]) == 2
        assert first_page["has_more"] is True
        assert first_page["next_cursor"]

        second = client.get(
            "/api/workflows",
            params={
                "limit": 2,
                "sort": "name",
                "order": "asc",
                "search": "target",
                "cursor": first_page["next_cursor"],
            },
        )
        assert second.status_code == 200, second.text
        second_page = second.json()
        assert len(second_page["items"]) == 1
        assert second_page["has_more"] is False
        assert second_page["next_cursor"] is None

        returned_ids = {row["id"] for row in first_page["items"] + second_page["items"]}
        assert returned_ids == created_ids

        tampered = client.get(
            "/api/workflows",
            params={
                "sort": "name",
                "order": "desc",
                "cursor": first_page["next_cursor"],
            },
        )
        assert tampered.status_code == 422

        malformed = client.get(
            "/api/workflows",
            params={"cursor": "not-a-valid-cursor!"},
        )
        assert malformed.status_code == 422
        invalid_base64 = client.get(
            "/api/workflows",
            params={"cursor": "A"},
        )
        assert invalid_base64.status_code == 422

        too_large = client.get("/api/workflows", params={"limit": 201})
        assert too_large.status_code == 422


def test_workflow_pagination_remains_bounded_at_100k_rows(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, environment="test", auth_mode="disabled")
    with TestClient(create_app(settings)) as client:
        with sqlite3.connect(tmp_path / "app.db") as connection:
            connection.execute(
                """
                WITH RECURSIVE sequence(value) AS (
                    VALUES(1)
                    UNION ALL
                    SELECT value + 1 FROM sequence WHERE value < 100000
                )
                INSERT INTO workflows (
                    id, name, description, dsl_json, version, is_archived,
                    created_at, updated_at
                )
                SELECT
                    printf('bulk-%027d', value),
                    'Bulk workflow',
                    '',
                    '{"version":"1.0","name":"Bulk","nodes":[],"edges":[]}',
                    1,
                    0,
                    '2026-01-01 00:00:00',
                    '2026-01-01 00:00:00'
                FROM sequence
                """
            )
            connection.commit()

        first = client.get(
            "/api/workflows",
            params={"limit": 200, "sort": "id", "order": "asc"},
        )
        assert first.status_code == 200, first.text
        first_page = first.json()
        assert len(first_page["items"]) == 200
        assert first_page["has_more"] is True

        second = client.get(
            "/api/workflows",
            params={
                "limit": 200,
                "sort": "id",
                "order": "asc",
                "cursor": first_page["next_cursor"],
            },
        )
        assert second.status_code == 200, second.text
        second_page = second.json()
        assert len(second_page["items"]) == 200
        assert second_page["has_more"] is True
        assert {item["id"] for item in first_page["items"]}.isdisjoint(
            item["id"] for item in second_page["items"]
        )


def test_execution_pages_keep_equal_status_values(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, environment="test", auth_mode="disabled")
    with TestClient(create_app(settings)) as client:
        workflow = client.post("/api/workflows", json=_workflow_body("Execution paging")).json()
        created_ids = {
            client.post(f"/api/workflows/{workflow['id']}/run", json={"inputs": {}}).json()["id"]
            for _ in range(3)
        }

        first = client.get(
            f"/api/workflows/{workflow['id']}/executions",
            params={"limit": 2, "sort": "status", "order": "asc"},
        )
        assert first.status_code == 200, first.text
        first_page = first.json()
        assert set(first_page) == {"items", "next_cursor", "has_more"}
        assert len(first_page["items"]) == 2
        assert first_page["has_more"] is True

        second = client.get(
            f"/api/workflows/{workflow['id']}/executions",
            params={
                "limit": 2,
                "sort": "status",
                "order": "asc",
                "cursor": first_page["next_cursor"],
            },
        )
        assert second.status_code == 200, second.text
        second_page = second.json()
        assert second_page["has_more"] is False
        assert {row["id"] for row in first_page["items"] + second_page["items"]} == created_ids


def test_knowledge_base_pages_support_search_and_equal_names(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, environment="test", auth_mode="disabled")
    with TestClient(create_app(settings)) as client:
        created_ids = {
            client.post(
                "/api/knowledge-bases",
                json={"name": "Same knowledge", "description": f"target {index}"},
            ).json()["id"]
            for index in range(3)
        }
        client.post(
            "/api/knowledge-bases",
            json={"name": "Other knowledge", "description": "not a match"},
        )

        first = client.get(
            "/api/knowledge-bases",
            params={
                "limit": 2,
                "sort": "name",
                "order": "asc",
                "search": "target",
            },
        )
        assert first.status_code == 200, first.text
        first_page = first.json()
        assert len(first_page["items"]) == 2
        assert first_page["has_more"] is True

        second = client.get(
            "/api/knowledge-bases",
            params={
                "limit": 2,
                "sort": "name",
                "order": "asc",
                "search": "target",
                "cursor": first_page["next_cursor"],
            },
        )
        assert second.status_code == 200, second.text
        second_page = second.json()
        assert {row["id"] for row in first_page["items"] + second_page["items"]} == created_ids
        assert second_page["has_more"] is False


def test_document_pages_support_search_and_equal_status_values(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, environment="test", auth_mode="disabled")
    with TestClient(create_app(settings)) as client:
        knowledge_base = client.post(
            "/api/knowledge-bases", json={"name": "Document paging"}
        ).json()
        base_path = f"/api/knowledge-bases/{knowledge_base['id']}/documents"
        created_ids = {
            client.post(
                base_path,
                files={"file": (f"target-{index}.txt", b"content", "text/plain")},
            ).json()["id"]
            for index in range(3)
        }
        client.post(
            base_path,
            files={"file": ("other.txt", b"content", "text/plain")},
        )

        first = client.get(
            base_path,
            params={
                "limit": 2,
                "sort": "status",
                "order": "asc",
                "search": "target",
            },
        )
        assert first.status_code == 200, first.text
        first_page = first.json()
        assert len(first_page["items"]) == 2
        assert first_page["has_more"] is True

        second = client.get(
            base_path,
            params={
                "limit": 2,
                "sort": "status",
                "order": "asc",
                "search": "target",
                "cursor": first_page["next_cursor"],
            },
        )
        assert second.status_code == 200, second.text
        second_page = second.json()
        assert {row["id"] for row in first_page["items"] + second_page["items"]} == created_ids
        assert second_page["has_more"] is False


def test_chat_session_pages_support_search_and_equal_titles(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, environment="test", auth_mode="disabled")
    with TestClient(create_app(settings)) as client:
        created_ids = {
            client.post("/api/chat/sessions", json={"title": "Target chat"}).json()["id"]
            for _ in range(3)
        }
        client.post("/api/chat/sessions", json={"title": "Other chat"})

        first = client.get(
            "/api/chat/sessions",
            params={
                "limit": 2,
                "sort": "title",
                "order": "asc",
                "search": "target",
            },
        )
        assert first.status_code == 200, first.text
        first_page = first.json()
        assert len(first_page["items"]) == 2
        assert first_page["has_more"] is True

        second = client.get(
            "/api/chat/sessions",
            params={
                "limit": 2,
                "sort": "title",
                "order": "asc",
                "search": "target",
                "cursor": first_page["next_cursor"],
            },
        )
        assert second.status_code == 200, second.text
        second_page = second.json()
        assert {row["id"] for row in first_page["items"] + second_page["items"]} == created_ids
        assert second_page["has_more"] is False


def test_chat_session_cursor_is_bound_to_workflow_filter(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, environment="test", auth_mode="disabled")
    with TestClient(create_app(settings)) as client:
        workflow_a = client.post("/api/workflows", json=_workflow_body("Workflow A")).json()
        workflow_b = client.post("/api/workflows", json=_workflow_body("Workflow B")).json()
        client.post(
            "/api/chat/sessions",
            json={"title": "A session", "workflow_id": workflow_a["id"]},
        )
        client.post(
            "/api/chat/sessions",
            json={"title": "A second session", "workflow_id": workflow_a["id"]},
        )
        client.post(
            "/api/chat/sessions",
            json={"title": "B session", "workflow_id": workflow_b["id"]},
        )

        first = client.get(
            "/api/chat/sessions",
            params={"workflow_id": workflow_a["id"], "limit": 1},
        )
        assert first.status_code == 200, first.text
        cursor = first.json()["next_cursor"]
        assert cursor

        mismatched = client.get(
            "/api/chat/sessions",
            params={"workflow_id": workflow_b["id"], "limit": 1, "cursor": cursor},
        )
        assert mismatched.status_code == 422, mismatched.text


def test_chat_messages_use_the_uniform_page_envelope(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, environment="test", auth_mode="disabled")
    with TestClient(create_app(settings)) as client:
        session = client.post("/api/chat/sessions", json={"title": "Messages"}).json()
        response = client.get(
            f"/api/chat/sessions/{session['id']}/messages",
            params={"limit": 1, "sort": "created_at", "order": "asc", "search": "hello"},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"items": [], "next_cursor": None, "has_more": False}
