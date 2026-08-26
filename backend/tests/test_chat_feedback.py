"""Tests for C3-4 chat deepening: feedback, dataset promotion, variables, export."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

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
                    "config": {
                        "input_schema": [{"name": "user_query", "type": "string", "required": True}]
                    },
                    "position": {"x": 0, "y": 0},
                },
                # Echo the user query so send() persists a concrete assistant
                # reply (same pattern as the app runtime tests).
                {
                    "id": "end",
                    "type": "end",
                    "position": {"x": 200, "y": 0},
                    "config": {"output_template": {"answer": "{{nodes.start.output.user_query}}"}},
                },
            ],
            "edges": [{"id": "edge", "source": "start", "target": "end"}],
        },
    }


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as c:
        yield c


def _make_session(client: TestClient) -> tuple[str, str]:
    wf = client.post("/api/workflows", json=_workflow_body()).json()
    session = client.post(
        "/api/chat/sessions",
        json={"title": "C3-4", "workflow_id": wf["id"]},
    ).json()
    return session["id"], wf["id"]


def _persist_assistant_message(client: TestClient, session_id: str) -> str:
    """Append a user + assistant pair directly via the messages API is not
    possible (messages are created by send), so we use the DB session through
    the send endpoint with a workflow that finishes quickly.

    The demo workflow above finishes with an empty output, so we patch the
    assistant message directly using the send endpoint and then fetch the
    created messages. Since the workflow has no provider, the engine uses the
    mock provider and returns a deterministic reply.
    """
    # Send a user message; the workflow runs with mock provider and persists
    # an assistant message.
    resp = client.post(
        f"/api/chat/sessions/{session_id}/send",
        json={"message": "what is 2+2?"},
    )
    assert resp.status_code == 200
    messages = client.get(f"/api/chat/sessions/{session_id}/messages").json()["items"]
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]
    assert assistant_msgs, "expected an assistant message after send"
    return assistant_msgs[-1]["id"]


def test_feedback_upsert_get_and_delete(client: TestClient) -> None:
    session_id, _ = _make_session(client)
    assistant_id = _persist_assistant_message(client, session_id)

    upsert = client.post(
        f"/api/chat/sessions/{session_id}/messages/{assistant_id}/feedback",
        json={"rating": "negative", "comment": "wrong answer"},
    )
    assert upsert.status_code == 200, upsert.text
    feedback = upsert.json()
    assert feedback["rating"] == "negative"
    assert feedback["comment"] == "wrong answer"
    assert feedback["promoted_dataset_version_id"] is None

    fetched = client.get(f"/api/chat/sessions/{session_id}/messages/{assistant_id}/feedback")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == feedback["id"]

    # Update to positive
    updated = client.post(
        f"/api/chat/sessions/{session_id}/messages/{assistant_id}/feedback",
        json={"rating": "positive"},
    )
    assert updated.json()["rating"] == "positive"

    deleted = client.delete(f"/api/chat/sessions/{session_id}/messages/{assistant_id}/feedback")
    assert deleted.status_code == 204
    assert (
        client.get(f"/api/chat/sessions/{session_id}/messages/{assistant_id}/feedback").status_code
        == 404
    )


def test_feedback_rejects_user_message(client: TestClient) -> None:
    session_id, _ = _make_session(client)
    client.post(f"/api/chat/sessions/{session_id}/send", json={"message": "hello"})
    messages = client.get(f"/api/chat/sessions/{session_id}/messages").json()["items"]
    user_msg = next(m for m in messages if m["role"] == "user")
    resp = client.post(
        f"/api/chat/sessions/{session_id}/messages/{user_msg['id']}/feedback",
        json={"rating": "positive"},
    )
    assert resp.status_code == 422


def test_feedback_rejects_invalid_rating(client: TestClient) -> None:
    session_id, _ = _make_session(client)
    assistant_id = _persist_assistant_message(client, session_id)
    resp = client.post(
        f"/api/chat/sessions/{session_id}/messages/{assistant_id}/feedback",
        json={"rating": "meh"},
    )
    assert resp.status_code == 422


def test_feedback_cross_session_returns_404(client: TestClient) -> None:
    session_a, _ = _make_session(client)
    session_b, _ = _make_session(client)
    assistant_id = _persist_assistant_message(client, session_a)
    resp = client.post(
        f"/api/chat/sessions/{session_b}/messages/{assistant_id}/feedback",
        json={"rating": "positive"},
    )
    assert resp.status_code == 404


def test_promote_creates_new_dataset(client: TestClient) -> None:
    session_id, _ = _make_session(client)
    assistant_id = _persist_assistant_message(client, session_id)

    resp = client.post(
        f"/api/chat/sessions/{session_id}/messages/{assistant_id}/promote",
        json={
            "dataset_name": "Promoted feedback",
            "expected": {"answer": "4"},
            "change_summary": "from chat",
        },
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["dataset_id"]
    assert out["dataset_version_id"]
    assert out["case_id"].startswith("chat-")

    # Dataset is visible in evaluation list
    dataset = client.get(f"/api/evaluation-datasets/{out['dataset_id']}").json()
    assert dataset["name"] == "Promoted feedback"
    cases = dataset["versions"][0]["cases"]  # newest version first
    assert len(cases) == 1
    assert cases[0]["expected"] == {"answer": "4"}
    assert cases[0]["inputs"]["user_query"] == "what is 2+2?"

    # Feedback is marked as promoted
    feedback = client.get(f"/api/chat/sessions/{session_id}/messages/{assistant_id}/feedback")
    # No feedback was left, so promote should not require one. But marking is
    # best-effort: if no feedback exists, feedback_id is "".
    assert feedback.status_code == 404


def test_promote_appends_version_to_existing_dataset(client: TestClient) -> None:
    session_id, _ = _make_session(client)
    assistant_id = _persist_assistant_message(client, session_id)

    # Create an initial dataset
    dataset = client.post(
        "/api/evaluation-datasets",
        json={
            "name": "Existing",
            "description": "",
            "cases": [
                {
                    "id": "seed",
                    "name": "Seed",
                    "inputs": {"user_query": "seed"},
                    "expected": {"answer": "seed"},
                }
            ],
            "change_summary": "initial",
        },
    ).json()

    resp = client.post(
        f"/api/chat/sessions/{session_id}/messages/{assistant_id}/promote",
        json={"dataset_id": dataset["id"]},
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["dataset_id"] == dataset["id"]

    updated = client.get(f"/api/evaluation-datasets/{dataset['id']}").json()
    versions = updated["versions"]  # newest version first
    assert len(versions) == 2
    cases = versions[0]["cases"]
    assert len(cases) == 2
    assert any(c["id"].startswith("chat-") for c in cases)


def test_promote_marks_feedback_as_promoted(client: TestClient) -> None:
    session_id, _ = _make_session(client)
    assistant_id = _persist_assistant_message(client, session_id)

    # Leave negative feedback first
    client.post(
        f"/api/chat/sessions/{session_id}/messages/{assistant_id}/feedback",
        json={"rating": "negative", "comment": "bad"},
    )

    resp = client.post(
        f"/api/chat/sessions/{session_id}/messages/{assistant_id}/promote",
        json={"dataset_name": "Marked"},
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["feedback_id"]

    feedback = client.get(
        f"/api/chat/sessions/{session_id}/messages/{assistant_id}/feedback"
    ).json()
    assert feedback["promoted_dataset_version_id"] == out["dataset_version_id"]
    assert feedback["promoted_at"] is not None


def test_promote_rejects_user_message(client: TestClient) -> None:
    session_id, _ = _make_session(client)
    client.post(f"/api/chat/sessions/{session_id}/send", json={"message": "hi"})
    messages = client.get(f"/api/chat/sessions/{session_id}/messages").json()["items"]
    user_msg = next(m for m in messages if m["role"] == "user")
    resp = client.post(
        f"/api/chat/sessions/{session_id}/messages/{user_msg['id']}/promote",
        json={},
    )
    assert resp.status_code == 422


def test_session_variables_crud(client: TestClient) -> None:
    session_id, _ = _make_session(client)

    # Initially empty
    snapshot = client.get(f"/api/chat/sessions/{session_id}/variables").json()
    assert snapshot["variables"] == {}

    # Upsert
    put = client.put(
        f"/api/chat/sessions/{session_id}/variables/summary",
        json={"name": "summary", "value": "aggregated text"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["name"] == "summary"
    assert put.json()["value"] == "aggregated text"

    snapshot = client.get(f"/api/chat/sessions/{session_id}/variables").json()
    assert snapshot["variables"] == {"summary": "aggregated text"}

    # Update existing
    client.put(
        f"/api/chat/sessions/{session_id}/variables/summary",
        json={"name": "summary", "value": {"nested": 42}},
    )
    snapshot = client.get(f"/api/chat/sessions/{session_id}/variables").json()
    assert snapshot["variables"]["summary"] == {"nested": 42}

    # Delete
    deleted = client.delete(f"/api/chat/sessions/{session_id}/variables/summary")
    assert deleted.status_code == 204
    snapshot = client.get(f"/api/chat/sessions/{session_id}/variables").json()
    assert snapshot["variables"] == {}

    assert client.delete(f"/api/chat/sessions/{session_id}/variables/missing").status_code == 404


def test_session_variable_name_path_mismatch(client: TestClient) -> None:
    session_id, _ = _make_session(client)
    resp = client.put(
        f"/api/chat/sessions/{session_id}/variables/foo",
        json={"name": "bar", "value": 1},
    )
    assert resp.status_code == 422


def test_session_variable_invalid_name(client: TestClient) -> None:
    session_id, _ = _make_session(client)
    resp = client.put(
        f"/api/chat/sessions/{session_id}/variables/1bad",
        json={"name": "1bad", "value": 1},
    )
    assert resp.status_code == 422


def test_export_json_and_markdown(client: TestClient) -> None:
    session_id, _ = _make_session(client)
    _persist_assistant_message(client, session_id)
    client.put(
        f"/api/chat/sessions/{session_id}/variables/topic",
        json={"name": "topic", "value": "math"},
    )

    json_resp = client.get(f"/api/chat/sessions/{session_id}/export", params={"format": "json"})
    assert json_resp.status_code == 200
    assert "application/json" in json_resp.headers["content-type"]
    payload = json.loads(json_resp.content)
    assert payload["session"]["title"] == "C3-4"
    assert payload["session"]["variables"] == {"topic": "math"}
    assert len(payload["messages"]) >= 2
    assert "attachment" in json_resp.headers["content-disposition"]

    md_resp = client.get(f"/api/chat/sessions/{session_id}/export", params={"format": "markdown"})
    assert md_resp.status_code == 200
    assert "text/markdown" in md_resp.headers["content-type"]
    text = md_resp.text
    assert "# C3-4" in text
    assert "## Variables" in text
    assert "topic" in text
    assert "## Transcript" in text


def test_export_unknown_session_404(client: TestClient) -> None:
    assert client.get("/api/chat/sessions/ghost/export").status_code == 404


def test_export_invalid_format_422(client: TestClient) -> None:
    session_id, _ = _make_session(client)
    resp = client.get(f"/api/chat/sessions/{session_id}/export", params={"format": "csv"})
    assert resp.status_code == 422
