"""Tests for the P5 chat session/message endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.test_rag import _wait_ingest

FERNET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key=FERNET_KEY,
    )


def _workflow_body(name: str = "Chat WF") -> dict:
    return {
        "name": name,
        "description": "",
        "dsl": {
            "version": "1.0",
            "name": name,
            "variables": [
                {"name": "user_query", "type": "string", "required": True},
            ],
            "settings": {
                "max_loop_iterations": 20,
                "timeout_seconds": 30,
                "recursion_limit": 50,
            },
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "config": {"input_schema": [{"name": "user_query", "type": "string", "required": True}]},
                    "position": {"x": 0, "y": 0},
                },
                {"id": "end", "type": "end", "position": {"x": 200, "y": 0}},
            ],
            "edges": [{"id": "edge", "source": "start", "target": "end"}],
        },
    }


def _rag_workflow_body(kb_id: str) -> dict:
    body = _workflow_body("Chat citation WF")
    body["dsl"]["nodes"] = [
        body["dsl"]["nodes"][0],
        {
            "id": "retrieve",
            "type": "rag",
            "position": {"x": 200, "y": 0},
            "config": {
                "kb_id": kb_id,
                "query": "{{input.user_query}}",
                "top_k": 3,
                "score_threshold": 0,
                "output_format": "merged_text",
            },
        },
        {
            "id": "end",
            "type": "end",
            "position": {"x": 400, "y": 0},
            "config": {"output_template": {"answer": "{{nodes.retrieve.text}}"}},
        },
    ]
    body["dsl"]["edges"] = [
        {"id": "to-rag", "source": "start", "target": "retrieve"},
        {"id": "to-end", "source": "retrieve", "target": "end"},
    ]
    return body


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as c:
        yield c


def test_create_list_delete_chat_session(client: TestClient) -> None:
    wf = client.post("/api/workflows", json=_workflow_body()).json()
    session = client.post(
        "/api/chat/sessions",
        json={"title": "My Chat", "workflow_id": wf["id"]},
    )
    assert session.status_code == 201, session.text
    sid = session.json()["id"]
    assert session.json()["workflow_id"] == wf["id"]

    listed = client.get("/api/chat/sessions").json()
    assert any(s["id"] == sid for s in listed["items"])

    fetched = client.get(f"/api/chat/sessions/{sid}").json()
    assert fetched["title"] == "My Chat"

    # Empty message list for a fresh session
    msgs = client.get(f"/api/chat/sessions/{sid}/messages").json()
    assert msgs == {"items": [], "next_cursor": None, "has_more": False}

    assert client.delete(f"/api/chat/sessions/{sid}").status_code == 204
    assert client.get(f"/api/chat/sessions/{sid}").status_code == 404


def test_create_session_rejects_unknown_workflow(client: TestClient) -> None:
    resp = client.post(
        "/api/chat/sessions",
        json={"title": "x", "workflow_id": "does-not-exist"},
    )
    assert resp.status_code == 404


def test_get_missing_session_returns_404(client: TestClient) -> None:
    assert client.get("/api/chat/sessions/ghost").status_code == 404
    assert client.get("/api/chat/sessions/ghost/messages").status_code == 404
    assert client.delete("/api/chat/sessions/ghost").status_code == 404


def test_send_message_requires_bound_workflow(client: TestClient) -> None:
    session = client.post("/api/chat/sessions", json={"title": "No WF"}).json()
    resp = client.post(
        f"/api/chat/sessions/{session['id']}/send",
        json={"message": "hi"},
    )
    assert resp.status_code == 400


def test_chat_replays_structured_rag_citations(client: TestClient) -> None:
    kb = client.post(
        "/api/knowledge-bases",
        json={"name": "Chat sources", "chunk_size": 128, "chunk_overlap": 16},
    )
    assert kb.status_code == 201, kb.text
    kb_id = kb.json()["id"]
    uploaded = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        files={
            "file": (
                "chat-handbook.md",
                b"Chat citations remain attached to durable assistant messages.",
                "text/markdown",
            )
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    _wait_ingest(client, kb_id, uploaded.json()["id"])

    workflow = client.post("/api/workflows", json=_rag_workflow_body(kb_id))
    assert workflow.status_code == 201, workflow.text
    chat = client.post(
        "/api/chat/sessions",
        json={"title": "Citation chat", "workflow_id": workflow.json()["id"]},
    )
    assert chat.status_code == 201, chat.text
    session_id = chat.json()["id"]
    sent = client.post(
        f"/api/chat/sessions/{session_id}/send",
        json={"message": "durable assistant citations"},
    )
    assert sent.status_code == 200, sent.text

    replay = client.get(f"/api/chat/sessions/{session_id}/messages")
    assert replay.status_code == 200, replay.text
    assistant = next(row for row in replay.json()["items"] if row["role"] == "assistant")
    assert assistant["citations"][0]["kb_id"] == kb_id
    assert assistant["citations"][0]["filename"] == "chat-handbook.md"
