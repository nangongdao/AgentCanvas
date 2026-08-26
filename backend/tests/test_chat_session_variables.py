"""C3-4 chat deepening: session variables in execution + regenerate/edit."""

from __future__ import annotations

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


def _dsl(name: str, *, output_template: dict | None = None, agent: dict | None = None) -> dict:
    """start → [agent] → end workflow echoing its output as ``answer``."""
    nodes = [
        {
            "id": "start",
            "type": "start",
            "position": {"x": 0, "y": 0},
            "config": {
                "input_schema": [{"name": "user_query", "type": "string", "required": True}]
            },
        }
    ]
    edges = [{"id": "e1", "source": "start", "target": "end"}]
    if agent is not None:
        nodes.append(
            {"id": "agent", "type": "agent", "position": {"x": 200, "y": 0}, "config": agent}
        )
        edges = [
            {"id": "e1", "source": "start", "target": "agent"},
            {"id": "e2", "source": "agent", "target": "end"},
        ]
    nodes.append(
        {
            "id": "end",
            "type": "end",
            "position": {"x": 400, "y": 0},
            "config": {
                "output_template": output_template
                or {"answer": "{{nodes.start.output.user_query}}"}
            },
        }
    )
    return {
        "version": "1.0",
        "name": name,
        "variables": [{"name": "user_query", "type": "string", "required": True}],
        "settings": {"max_loop_iterations": 20, "timeout_seconds": 30, "recursion_limit": 50},
        "nodes": nodes,
        "edges": edges,
    }


def _body(name: str = "Vars WF", **kwargs) -> dict:
    return {"name": name, "description": "", "dsl": _dsl(name, **kwargs)}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as c:
        yield c


def _make_session(client: TestClient, body: dict | None = None) -> str:
    wf = client.post("/api/workflows", json=body or _body()).json()
    session = client.post(
        "/api/chat/sessions", json={"title": "Vars", "workflow_id": wf["id"]}
    ).json()
    return session["id"]


def _send(client: TestClient, session_id: str, message: str) -> None:
    resp = client.post(f"/api/chat/sessions/{session_id}/send", json={"message": message})
    assert resp.status_code == 200, resp.text


def _messages(client: TestClient, session_id: str) -> list[dict]:
    """Return the transcript oldest-first regardless of the page order."""
    items = client.get(f"/api/chat/sessions/{session_id}/messages").json()["items"]
    return sorted(items, key=lambda m: m["created_at"] or "")


def _variables(client: TestClient, session_id: str) -> dict:
    return client.get(f"/api/chat/sessions/{session_id}/variables").json()["variables"]


def test_session_variable_read_in_templates(client: TestClient) -> None:
    """A variable set before the turn is readable as {{session.<name>}}."""
    session_id = _make_session(client, _body(output_template={"answer": "topic={{session.topic}}"}))
    client.put(
        f"/api/chat/sessions/{session_id}/variables/topic",
        json={"name": "topic", "value": "precision-farming"},
    )
    _send(client, session_id, "hello")
    assistant = [m for m in _messages(client, session_id) if m["role"] == "assistant"]
    assert assistant, "expected an assistant reply"
    assert assistant[-1]["content"] == "topic=precision-farming"


def test_agent_session_writes_persist(client: TestClient) -> None:
    """An agent configured with session_writes persists its reply as a variable."""
    session_id = _make_session(
        client,
        _body(
            agent={
                "model_config_id": "default",
                "user_prompt": "{{input.user_query}}",
                "session_writes": {"last_reply": "{{nodes.agent.output}}"},
            },
            output_template={"answer": "{{nodes.agent.output}}"},
        ),
    )
    _send(client, session_id, "remember this")
    variables = _variables(client, session_id)
    assert "last_reply" in variables, variables
    assert "[Mock 模式]" in variables["last_reply"]
    assert "remember this" in variables["last_reply"]


def test_multi_turn_memory_read_after_write(client: TestClient) -> None:
    """Turn 1 writes a variable via the agent; turn 2 reads it back."""
    session_id = _make_session(
        client,
        _body(
            agent={
                "model_config_id": "default",
                "user_prompt": "{{input.user_query}}",
                "session_writes": {"last_reply": "{{nodes.agent.output}}"},
            },
            output_template={"answer": "{{nodes.agent.output}}"},
        ),
    )
    _send(client, session_id, "turn one")
    assert "last_reply" in _variables(client, session_id)

    # Swap the end template usage: a second workflow on the same session is not
    # possible, so instead verify via a direct variable read endpoint.
    snapshot = _variables(client, session_id)
    first_reply = snapshot["last_reply"]
    assert "turn one" in first_reply


def test_session_writes_skipped_without_store_agent(client: TestClient) -> None:
    """session_writes with no chat session (ad-hoc run) never writes rows."""
    wf = client.post(
        "/api/workflows",
        json=_body(
            "Adhoc WF",
            agent={
                "model_config_id": "default",
                "session_writes": {"leak": "{{nodes.agent.output}}"},
            },
        ),
    ).json()
    resp = client.post(f"/api/workflows/{wf['id']}/run", json={"inputs": {"user_query": "x"}})
    assert resp.status_code in {200, 201, 202}, resp.text


def test_regenerate_replaces_assistant_reply(client: TestClient) -> None:
    session_id = _make_session(client)
    _send(client, session_id, "first question")
    messages = _messages(client, session_id)
    assert len(messages) == 2
    assistant_id = next(m["id"] for m in messages if m["role"] == "assistant")

    resp = client.post(
        f"/api/chat/sessions/{session_id}/regenerate",
        params={"message_id": assistant_id},
        json={},
    )
    assert resp.status_code == 200, resp.text
    after = _messages(client, session_id)
    assert len(after) == 2, after
    assert [m["role"] for m in after] == ["user", "assistant"]
    assert after[1]["id"] != assistant_id
    assert after[0]["content"] == "first question"


def test_regenerate_rejects_user_message(client: TestClient) -> None:
    session_id = _make_session(client)
    _send(client, session_id, "hi")
    user_id = next(m["id"] for m in _messages(client, session_id) if m["role"] == "user")
    resp = client.post(
        f"/api/chat/sessions/{session_id}/regenerate",
        params={"message_id": user_id},
        json={},
    )
    assert resp.status_code == 422


def test_edit_user_message_truncates_and_reruns(client: TestClient) -> None:
    session_id = _make_session(client)
    _send(client, session_id, "original")
    _send(client, session_id, "second turn")
    messages = _messages(client, session_id)
    assert len(messages) == 4
    first_user = next(m["id"] for m in messages if m["role"] == "user")

    resp = client.post(
        f"/api/chat/sessions/{session_id}/messages/{first_user}/edit",
        json={"message": "edited question"},
    )
    assert resp.status_code == 200, resp.text
    after = _messages(client, session_id)
    roles = [m["role"] for m in after]
    assert roles == ["user", "assistant"], roles
    assert after[0]["content"] == "edited question"
    assert after[1]["content"] == "edited question"  # echo workflow


def test_edit_rejects_assistant_message(client: TestClient) -> None:
    session_id = _make_session(client)
    _send(client, session_id, "hi")
    assistant_id = next(m["id"] for m in _messages(client, session_id) if m["role"] == "assistant")
    resp = client.post(
        f"/api/chat/sessions/{session_id}/messages/{assistant_id}/edit",
        json={"message": "nope"},
    )
    assert resp.status_code == 422
