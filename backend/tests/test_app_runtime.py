"""Public application runtime resolve/session/send coverage (C3-2)."""

from __future__ import annotations

import json
import sqlite3
import time

from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_apps import (
    _admin,
    _published_version,
    _settings,
)


def _runtime_dsl(name: str = "Runtime Workflow") -> dict:
    """DSL that declares ``user_query`` and echoes it back as ``answer``."""
    return {
        "version": "1.0",
        "name": name,
        "variables": [
            {"name": "user_query", "type": "string", "required": True},
        ],
        "settings": {"max_loop_iterations": 20, "timeout_seconds": 30, "recursion_limit": 50},
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "position": {"x": 0, "y": 0},
                "config": {
                    "input_schema": [{"name": "user_query", "type": "string", "required": True}]
                },
            },
            {
                "id": "end",
                "type": "end",
                "position": {"x": 200, "y": 0},
                # Echo the user's query straight through so the assistant reply
                # is a concrete string the runtime can persist.
                "config": {"output_template": {"answer": "{{nodes.start.output.user_query}}"}},
            },
        ],
        "edges": [{"id": "edge", "source": "start", "target": "end"}],
    }


def _rag_runtime_dsl(kb_id: str) -> dict:
    """Runtime workflow that returns real retrieval text and citations."""
    return {
        "version": "1.0",
        "name": "Runtime RAG Workflow",
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
                "position": {"x": 0, "y": 0},
                "config": {
                    "input_schema": [{"name": "user_query", "type": "string", "required": True}]
                },
            },
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
        ],
        "edges": [
            {"id": "to-rag", "source": "start", "target": "retrieve"},
            {"id": "to-end", "source": "retrieve", "target": "end"},
        ],
    }


def _setup(client: TestClient) -> tuple[str, str, str]:
    """Create org/project + a published workflow that declares user_query."""
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "runtime-admin@example.com",
            "password": "runtime-password",
            "display_name": "Runtime Admin",
            "role": "admin",
        },
    )
    assert registered.status_code == 201, registered.text
    org = client.post("/api/organizations", json={"name": "Runtime Org"})
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]
    project = client.post(f"/api/organizations/{org_id}/projects", json={"name": "Runtime Project"})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    workflow = client.post(
        "/api/workflows",
        headers=_admin(),
        json={"name": "Runtime Workflow", "project_id": project_id, "dsl": _runtime_dsl()},
    )
    assert workflow.status_code == 201, workflow.text
    workflow_id = workflow.json()["id"]
    assert client.post(f"/api/workflows/{workflow_id}/publish", headers=_admin()).status_code == 200
    return org_id, project_id, workflow_id


def _bind_version(client: TestClient, project_id: str, workflow_id: str) -> str:
    """Create a public app and switch it to the published version."""
    created = client.post(
        "/api/apps",
        headers=_admin(),
        json={
            "project_id": project_id,
            "workflow_id": workflow_id,
            "name": "Runtime App",
            "type": "chatbot",
            "welcome_message": "Hello there",
            "suggested_questions": ["What is X?"],
            "visibility": "public",
        },
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["app"]["id"]
    version_id = _published_version(client, workflow_id)
    assert (
        client.post(
            f"/api/apps/{app_id}/version", headers=_admin(), json={"version_id": version_id}
        ).status_code
        == 200
    )
    return app_id


def _set_message_citations(tmp_path, message_id: str, citations: list[dict]) -> None:
    with sqlite3.connect(tmp_path / "app.db") as database:
        database.execute(
            "UPDATE chat_messages SET citations_json = ? WHERE id = ?",
            (json.dumps(citations), message_id),
        )
        database.commit()


def test_resolve_public_app_returns_metadata_without_token(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        _bind_version(client, project_id, workflow_id)

        resolved = client.get("/api/apps/p/runtime-app")
        assert resolved.status_code == 200, resolved.text
        body = resolved.json()
        assert body["slug"] == "runtime-app"
        assert body["name"] == "Runtime App"
        assert body["welcome_message"] == "Hello there"
        assert body["suggested_questions"] == ["What is X?"]
        assert body["requires_token"] is False
        assert {field["name"] for field in body["input_form"]} == {"user_query"}
        # No token or internal ids leaked.
        assert "token_prefix" not in body
        assert "public_token_hash" not in body
        # C3-3: embed fields present; no origins configured means embedding disabled.
        assert body["theme_color"] is None
        assert body["embed_enabled"] is False


def test_runtime_response_sets_frame_ancestors_none_by_default(tmp_path) -> None:
    """Apps without embed_allowed_origins must emit frame-ancestors 'none'."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        _bind_version(client, project_id, workflow_id)

        resolved = client.get("/api/apps/p/runtime-app")
        assert resolved.status_code == 200
        csp = resolved.headers.get("content-security-policy", "")
        assert "frame-ancestors 'none'" in csp


def test_runtime_response_sets_frame_ancestors_when_origins_configured(tmp_path) -> None:
    """Apps with embed_allowed_origins must list them in frame-ancestors."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        app_id = _bind_version(client, project_id, workflow_id)

        updated = client.put(
            f"/api/apps/{app_id}",
            headers=_admin(),
            json={
                "theme_color": "#3B82F6",
                "embed_allowed_origins": [
                    "https://example.com",
                    "https://app.example.com:8443",
                ],
            },
        )
        assert updated.status_code == 200, updated.text
        app_body = updated.json()["app"]
        assert app_body["theme_color"] == "#3b82f6"
        assert app_body["embed_allowed_origins"] == [
            "https://example.com",
            "https://app.example.com:8443",
        ]

        resolved = client.get("/api/apps/p/runtime-app")
        assert resolved.status_code == 200
        csp = resolved.headers.get("content-security-policy", "")
        assert "frame-ancestors" in csp
        assert "https://example.com" in csp
        assert "https://app.example.com:8443" in csp
        assert "'none'" not in csp

        # The runtime descriptor should also reflect the embed state.
        body = resolved.json()
        assert body["theme_color"] == "#3b82f6"
        assert body["embed_enabled"] is True


def test_runtime_csp_applied_to_session_and_messages_endpoints(tmp_path) -> None:
    """CSP frame-ancestors must be set on all runtime endpoints, not just resolve."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        app_id = _bind_version(client, project_id, workflow_id)
        client.put(
            f"/api/apps/{app_id}",
            headers=_admin(),
            json={"embed_allowed_origins": ["https://embed.test"]},
        )

        session = client.post(
            "/api/apps/p/runtime-app/sessions",
            json={"inputs": {"user_query": "hi"}},
        )
        assert session.status_code == 201
        csp = session.headers.get("content-security-policy", "")
        assert "https://embed.test" in csp

        messages = client.get(
            f"/api/apps/p/runtime-app/sessions/{session.json()['session_id']}/messages"
        )
        assert messages.status_code == 200
        csp = messages.headers.get("content-security-policy", "")
        assert "https://embed.test" in csp


def test_update_app_rejects_invalid_theme_color(tmp_path) -> None:
    """Malformed theme colors must be rejected with 422."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        app_id = _bind_version(client, project_id, workflow_id)
        bad = client.put(
            f"/api/apps/{app_id}",
            headers=_admin(),
            json={"theme_color": "not-a-color"},
        )
        assert bad.status_code == 422


def test_update_app_rejects_invalid_embed_origin(tmp_path) -> None:
    """Origins without a scheme or with a path must be rejected."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        app_id = _bind_version(client, project_id, workflow_id)
        # Missing scheme.
        bad = client.put(
            f"/api/apps/{app_id}",
            headers=_admin(),
            json={"embed_allowed_origins": ["example.com"]},
        )
        assert bad.status_code == 422
        # Has a path.
        bad = client.put(
            f"/api/apps/{app_id}",
            headers=_admin(),
            json={"embed_allowed_origins": ["https://example.com/path"]},
        )
        assert bad.status_code == 422
        # Non-http scheme.
        bad = client.put(
            f"/api/apps/{app_id}",
            headers=_admin(),
            json={"embed_allowed_origins": ["ftp://example.com"]},
        )
        assert bad.status_code == 422


def test_update_app_empty_origins_disables_embedding(tmp_path) -> None:
    """Setting an empty list explicitly disables embedding (frame-ancestors 'none')."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        app_id = _bind_version(client, project_id, workflow_id)
        # First enable embedding.
        client.put(
            f"/api/apps/{app_id}",
            headers=_admin(),
            json={"embed_allowed_origins": ["https://example.com"]},
        )
        # Then disable it with an empty list.
        disabled = client.put(
            f"/api/apps/{app_id}",
            headers=_admin(),
            json={"embed_allowed_origins": []},
        )
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["app"]["embed_allowed_origins"] == []

        resolved = client.get("/api/apps/p/runtime-app")
        assert resolved.status_code == 200
        csp = resolved.headers.get("content-security-policy", "")
        assert "frame-ancestors 'none'" in csp
        assert resolved.json()["embed_enabled"] is False


def test_create_app_with_embed_config(tmp_path) -> None:
    """Creating an app with embed config persists theme_color and origins."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={
                "project_id": project_id,
                "workflow_id": workflow_id,
                "name": "Embed App",
                "visibility": "public",
                "theme_color": "#FF5733",
                "embed_allowed_origins": ["https://my-site.com"],
            },
        )
        assert created.status_code == 201, created.text
        app = created.json()["app"]
        assert app["theme_color"] == "#ff5733"
        assert app["embed_allowed_origins"] == ["https://my-site.com"]


def test_embed_script_served_when_embedding_enabled(tmp_path) -> None:
    """The floating-bubble bootstrap is served as JavaScript when enabled."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        app_id = _bind_version(client, project_id, workflow_id)
        enabled = client.put(
            f"/api/apps/{app_id}",
            headers=_admin(),
            json={"embed_allowed_origins": ["https://example.com"]},
        )
        assert enabled.status_code == 200, enabled.text

        script = client.get("/api/apps/p/runtime-app/embed.js")
        assert script.status_code == 200, script.text
        assert script.headers["content-type"].startswith("text/javascript")
        # Defensive headers: no referrer leakage, no sniffing, no stale config.
        assert script.headers.get("referrer-policy") == "no-referrer"
        assert script.headers.get("x-content-type-options") == "nosniff"
        assert script.headers.get("cache-control") == "no-store"
        # The bootstrap is loaded by external host pages; CORP must be
        # cross-origin or the C8-3 middleware's same-origin default makes
        # browsers block the request with ERR_BLOCKED_BY_RESPONSE.
        assert script.headers.get("cross-origin-resource-policy") == "cross-origin"
        body = script.text
        # The static bootstrap resolves slug/origin from its own src URL and
        # reads embedder-supplied data attributes; it must not interpolate any
        # server-side app data (name, tokens, ids).
        assert "[a-z0-9-]{1,64}" in body
        assert '"/apps/p/" + slug' in body
        assert "?embed=1" in body
        assert 'getAttribute("data-token")' in body
        assert 'getAttribute("data-color")' in body
        assert 'getAttribute("data-position")' in body
        assert "Runtime App" not in body


def test_embed_script_rejected_when_embedding_disabled(tmp_path) -> None:
    """Apps without embed_allowed_origins must not serve the bubble script."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        app_id = _bind_version(client, project_id, workflow_id)
        client.put(
            f"/api/apps/{app_id}",
            headers=_admin(),
            json={"embed_allowed_origins": []},
        )
        assert client.get("/api/apps/p/runtime-app/embed.js").status_code == 403


def test_embed_script_unknown_or_project_app_returns_404(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert client.get("/api/apps/p/no-such-app/embed.js").status_code == 404
        _org_id, project_id, workflow_id = _setup(client)
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={
                "project_id": project_id,
                "workflow_id": workflow_id,
                "name": "Runtime App",
                "visibility": "project",
                "embed_allowed_origins": ["https://example.com"],
            },
        )
        assert created.status_code == 201, created.text
        app_id = created.json()["app"]["id"]
        version_id = _published_version(client, workflow_id)
        assert (
            client.post(
                f"/api/apps/{app_id}/version", headers=_admin(), json={"version_id": version_id}
            ).status_code
            == 200
        )
        # Project apps are unreachable from the public runtime entirely.
        assert client.get("/api/apps/p/runtime-app/embed.js").status_code == 404


def test_embed_script_link_app_served_without_token_and_leaks_no_secret(tmp_path) -> None:
    """The bootstrap is public even for link apps; the token travels only in
    the embedder's data-token attribute and never appears in the script."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={
                "project_id": project_id,
                "workflow_id": workflow_id,
                "name": "Runtime App",
                "visibility": "link",
                "embed_allowed_origins": ["https://example.com"],
            },
        )
        assert created.status_code == 201, created.text
        raw_token = created.json()["token"]
        app_id = created.json()["app"]["id"]
        version_id = _published_version(client, workflow_id)
        assert (
            client.post(
                f"/api/apps/{app_id}/version", headers=_admin(), json={"version_id": version_id}
            ).status_code
            == 200
        )

        script = client.get("/api/apps/p/runtime-app/embed.js")
        assert script.status_code == 200, script.text
        assert raw_token not in script.text
        # The iframe URL is built client-side from the script src origin, so a
        # deployment behind any public origin works without configuration.
        assert "src.origin" in script.text


def test_resolve_link_app_requires_valid_token(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        # Create a link app (token-protected) instead of public.
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={
                "project_id": project_id,
                "workflow_id": workflow_id,
                "name": "Runtime App",
                "visibility": "link",
            },
        )
        assert created.status_code == 201, created.text
        token = created.json()["token"]
        app_id = created.json()["app"]["id"]
        version_id = _published_version(client, workflow_id)
        assert (
            client.post(
                f"/api/apps/{app_id}/version", headers=_admin(), json={"version_id": version_id}
            ).status_code
            == 200
        )

        # No token -> 401.
        assert client.get("/api/apps/p/runtime-app").status_code == 401
        # Wrong token -> 401.
        assert client.get("/api/apps/p/runtime-app", params={"t": "wrong"}).status_code == 401
        # Correct token -> 200 and requires_token is True.
        resolved = client.get("/api/apps/p/runtime-app", params={"t": token})
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["requires_token"] is True


def test_resolve_link_app_rejects_rotated_token(tmp_path) -> None:
    """After a token rotation the previous token must no longer grant access."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={
                "project_id": project_id,
                "workflow_id": workflow_id,
                "name": "Runtime App",
                "visibility": "link",
            },
        )
        assert created.status_code == 201
        old_token = created.json()["token"]
        app_id = created.json()["app"]["id"]
        version_id = _published_version(client, workflow_id)
        assert (
            client.post(
                f"/api/apps/{app_id}/version", headers=_admin(), json={"version_id": version_id}
            ).status_code
            == 200
        )

        # Old token works before rotation.
        assert client.get("/api/apps/p/runtime-app", params={"t": old_token}).status_code == 200

        rotated = client.post(f"/api/apps/{app_id}/token/rotate", headers=_admin())
        assert rotated.status_code == 200, rotated.text
        new_token = rotated.json()["token"]
        assert new_token and new_token != old_token

        # Old token is now invalid (401), new token works (200).
        assert client.get("/api/apps/p/runtime-app", params={"t": old_token}).status_code == 401
        assert client.get("/api/apps/p/runtime-app", params={"t": new_token}).status_code == 200


def test_resolve_project_app_is_not_reachable(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={
                "project_id": project_id,
                "workflow_id": workflow_id,
                "name": "Runtime App",
                "visibility": "project",
            },
        )
        assert created.status_code == 201
        app_id = created.json()["app"]["id"]
        version_id = _published_version(client, workflow_id)
        assert (
            client.post(
                f"/api/apps/{app_id}/version", headers=_admin(), json={"version_id": version_id}
            ).status_code
            == 200
        )
        # Project apps are intentionally unreachable from the public runtime.
        assert client.get("/api/apps/p/runtime-app").status_code == 404


def test_resolve_unknown_slug_returns_404(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert client.get("/api/apps/p/no-such-app").status_code == 404


def test_runtime_session_persists_app_binding_and_welcome(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        app_id = _bind_version(client, project_id, workflow_id)

        session = client.post(
            "/api/apps/p/runtime-app/sessions",
            json={"inputs": {"user_query": "hi"}, "title": "Test"},
        )
        assert session.status_code == 201, session.text
        body = session.json()
        assert body["role"] == "system"
        assert body["content"] == "Hello there"
        session_id = body["session_id"]

        with sqlite3.connect(tmp_path / "app.db") as database:
            row = database.execute(
                "SELECT app_id, workflow_id, title FROM chat_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        assert row[0] == app_id
        assert row[1] == workflow_id
        assert row[2] == "Test"


def test_runtime_session_send_streams_assistant_reply(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        _bind_version(client, project_id, workflow_id)

        session = client.post(
            "/api/apps/p/runtime-app/sessions",
            json={"inputs": {"user_query": "hi"}},
        )
        session_id = session.json()["session_id"]

        send = client.post(
            f"/api/apps/p/runtime-app/sessions/{session_id}/send",
            json={
                "message": "What is X?",
                "inputs": {"user_query": "What is X?"},
            },
        )
        assert send.status_code == 200, send.text
        assert send.headers["content-type"].startswith("text/event-stream")
        # The streamed payload must terminate with a workflow terminal event.
        assert "workflow_finished" in send.text or "workflow_failed" in send.text

        # The assistant reply is persisted as a message.
        messages = client.get(f"/api/apps/p/runtime-app/sessions/{session_id}/messages")
        assert messages.status_code == 200, messages.text
        roles = [m["role"] for m in messages.json()]
        assert "user" in roles
        assert "assistant" in roles


def test_runtime_streams_and_persists_rag_citations(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)

        kb = client.post(
            "/api/knowledge-bases",
            headers=_admin(),
            json={
                "project_id": project_id,
                "name": "Runtime sources",
                "chunk_size": 128,
                "chunk_overlap": 24,
            },
        )
        assert kb.status_code == 201, kb.text
        kb_id = kb.json()["id"]
        uploaded = client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            headers=_admin(),
            files={
                "file": (
                    "runtime-handbook.md",
                    b"AgentCanvas runtime citations remain available after reconnect.",
                    "text/markdown",
                )
            },
        )
        assert uploaded.status_code == 201, uploaded.text
        document_id = uploaded.json()["id"]
        started = client.post(
            f"/api/knowledge-bases/{kb_id}/documents/{document_id}/ingest",
            headers=_admin(),
        )
        assert started.status_code == 202, started.text
        deadline = time.monotonic() + 10
        while True:
            state = client.get(
                f"/api/knowledge-bases/{kb_id}/documents/{document_id}/ingest",
                headers=_admin(),
            )
            assert state.status_code == 200, state.text
            if state.json()["job_status"] in {"succeeded", "failed", "cancelled"}:
                break
            assert time.monotonic() < deadline, state.text
            time.sleep(0.02)
        assert state.json()["job_status"] == "succeeded", state.text

        current = client.get(f"/api/workflows/{workflow_id}", headers=_admin())
        assert current.status_code == 200, current.text
        updated = client.put(
            f"/api/workflows/{workflow_id}",
            headers=_admin(),
            json={
                "dsl": _rag_runtime_dsl(kb_id),
                "version": current.json()["version"],
            },
        )
        assert updated.status_code == 200, updated.text
        published = client.post(f"/api/workflows/{workflow_id}/publish", headers=_admin())
        assert published.status_code == 200, published.text
        app_id = _bind_version(client, project_id, workflow_id)

        runtime_session = client.post(
            "/api/apps/p/runtime-app/sessions",
            json={"inputs": {"user_query": "reconnect citations"}},
        )
        assert runtime_session.status_code == 201, runtime_session.text
        session_id = runtime_session.json()["session_id"]
        sent = client.post(
            f"/api/apps/p/runtime-app/sessions/{session_id}/send",
            json={
                "message": "reconnect citations",
                "inputs": {"user_query": "reconnect citations"},
            },
        )
        assert sent.status_code == 200, sent.text
        assert '"kind": "retrieval"' in sent.text
        assert '"filename": "runtime-handbook.md"' in sent.text

        replay = client.get(f"/api/apps/p/runtime-app/sessions/{session_id}/messages")
        assert replay.status_code == 200, replay.text
        assistant = next(message for message in replay.json() if message["role"] == "assistant")
        assert assistant["citations"]
        assert assistant["citations"][0]["filename"] == "runtime-handbook.md"
        citation = assistant["citations"][0]

        source = client.get(
            f"/api/apps/p/runtime-app/sessions/{session_id}/messages/"
            f"{assistant['id']}/citations/{citation['id']}/source"
        )
        assert source.status_code == 200, source.text
        assert source.content == (
            b"AgentCanvas runtime citations remain available after reconnect."
        )
        assert source.headers["content-type"].startswith("text/markdown")
        assert "inline" in source.headers["content-disposition"]
        assert source.headers["referrer-policy"] == "no-referrer"
        assert source.headers["x-content-type-options"] == "nosniff"
        assert source.headers["cache-control"] == "private, no-store"

        source_contract = client.get("/openapi.json").json()["paths"][
            "/api/apps/p/{slug}/sessions/{session_id}/messages/{message_id}/citations/"
            "{citation_id}/source"
        ]["get"]["responses"]["200"]["content"]
        assert set(source_contract) == {"text/plain", "text/markdown", "application/pdf"}
        assert source_contract["application/pdf"]["schema"]["format"] == "binary"

        missing = client.get(
            f"/api/apps/p/runtime-app/sessions/{session_id}/messages/"
            f"{assistant['id']}/citations/not-a-real-citation/source"
        )
        assert missing.status_code == 404
        user_message = next(message for message in replay.json() if message["role"] == "user")
        wrong_message = client.get(
            f"/api/apps/p/runtime-app/sessions/{session_id}/messages/"
            f"{user_message['id']}/citations/{citation['id']}/source"
        )
        assert wrong_message.status_code == 404

        other_document = client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            headers=_admin(),
            files={"file": ("other.md", b"unrelated source", "text/markdown")},
        )
        assert other_document.status_code == 201, other_document.text
        _set_message_citations(
            tmp_path,
            assistant["id"],
            [{**citation, "document_id": other_document.json()["id"]}],
        )
        assert client.get(source.url.path).status_code == 404

        other_kb = client.post(
            "/api/knowledge-bases",
            headers=_admin(),
            json={"project_id": project_id, "name": "Other runtime KB"},
        )
        assert other_kb.status_code == 201, other_kb.text
        _set_message_citations(
            tmp_path,
            assistant["id"],
            [{**citation, "kb_id": other_kb.json()["id"]}],
        )
        assert client.get(source.url.path).status_code == 404

        foreign_org = client.post("/api/organizations", json={"name": "Foreign Runtime Org"})
        assert foreign_org.status_code == 201, foreign_org.text
        foreign_project = client.post(
            f"/api/organizations/{foreign_org.json()['id']}/projects",
            json={"name": "Foreign Runtime Project"},
        )
        assert foreign_project.status_code == 201, foreign_project.text
        foreign_kb = client.post(
            "/api/knowledge-bases",
            headers=_admin(),
            json={"project_id": foreign_project.json()["id"], "name": "Foreign KB"},
        )
        assert foreign_kb.status_code == 201, foreign_kb.text
        foreign_document = client.post(
            f"/api/knowledge-bases/{foreign_kb.json()['id']}/documents",
            headers=_admin(),
            files={"file": ("foreign.md", b"foreign source", "text/markdown")},
        )
        assert foreign_document.status_code == 201, foreign_document.text
        _set_message_citations(
            tmp_path,
            assistant["id"],
            [
                {
                    **citation,
                    "kb_id": foreign_kb.json()["id"],
                    "document_id": foreign_document.json()["id"],
                    "filename": "foreign.md",
                }
            ],
        )
        assert client.get(source.url.path).status_code == 404

        _set_message_citations(tmp_path, assistant["id"], [citation])
        second_session = client.post(
            "/api/apps/p/runtime-app/sessions",
            json={"inputs": {"user_query": "other session"}},
        )
        assert second_session.status_code == 201, second_session.text
        wrong_session_source = source.url.path.replace(
            session_id,
            second_session.json()["session_id"],
        )
        assert client.get(wrong_session_source).status_code == 404

        foreign_app = client.post(
            "/api/apps",
            headers=_admin(),
            json={
                "project_id": project_id,
                "workflow_id": workflow_id,
                "name": "Foreign Runtime App",
                "type": "chatbot",
                "visibility": "public",
            },
        )
        assert foreign_app.status_code == 201, foreign_app.text
        foreign_app_body = foreign_app.json()["app"]
        version_id = _published_version(client, workflow_id)
        assert (
            client.post(
                f"/api/apps/{foreign_app_body['id']}/version",
                headers=_admin(),
                json={"version_id": version_id},
            ).status_code
            == 200
        )
        wrong_app_source = source.url.path.replace("runtime-app", foreign_app_body["slug"])
        assert client.get(wrong_app_source).status_code == 404

        linked = client.put(
            f"/api/apps/{app_id}",
            headers=_admin(),
            json={"visibility": "link"},
        )
        assert linked.status_code == 200, linked.text
        rotated = client.post(f"/api/apps/{app_id}/token/rotate", headers=_admin())
        assert rotated.status_code == 200, rotated.text
        raw_token = rotated.json()["token"]
        assert raw_token
        assert client.get(source.url.path).status_code == 401
        protected_source = client.get(source.url.path, params={"t": raw_token})
        assert protected_source.status_code == 200, protected_source.text
        assert protected_source.content == source.content

        usage = client.get(f"/api/apps/{app_id}/usage", headers=_admin())
        assert usage.status_code == 200, usage.text
        assert usage.json()["available_citations"] == 1
        assert usage.json()["referenced_citations"] == 1
        assert usage.json()["citation_coverage"] == 1.0


def test_runtime_session_isolation_rejects_foreign_session(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        _bind_version(client, project_id, workflow_id)

        # A platform chat session not bound to this app.
        foreign = client.post(
            "/api/chat/sessions",
            headers=_admin(),
            json={"workflow_id": workflow_id, "title": "Platform"},
        )
        assert foreign.status_code == 201
        foreign_id = foreign.json()["id"]

        # The runtime must reject a session not owned by the app.
        send = client.post(
            f"/api/apps/p/runtime-app/sessions/{foreign_id}/send",
            json={"message": "intrusion"},
        )
        assert send.status_code == 404
        messages = client.get(f"/api/apps/p/runtime-app/sessions/{foreign_id}/messages")
        assert messages.status_code == 404


def _runtime_turn(client: TestClient) -> tuple[str, str]:
    """Create a public app, send one message, return (session_id, assistant_id)."""
    _org_id, project_id, workflow_id = _setup(client)
    _bind_version(client, project_id, workflow_id)
    runtime_session = client.post(
        "/api/apps/p/runtime-app/sessions",
        json={"inputs": {"user_query": "feedback flow"}},
    )
    assert runtime_session.status_code == 201, runtime_session.text
    session_id = runtime_session.json()["session_id"]
    sent = client.post(
        f"/api/apps/p/runtime-app/sessions/{session_id}/send",
        json={"message": "feedback flow", "inputs": {"user_query": "feedback flow"}},
    )
    assert sent.status_code == 200, sent.text
    replay = client.get(f"/api/apps/p/runtime-app/sessions/{session_id}/messages")
    assert replay.status_code == 200
    assistant = next(m for m in replay.json() if m["role"] == "assistant")
    return session_id, assistant["id"]


def test_runtime_feedback_upsert_and_get(tmp_path) -> None:
    """End users can 👎 a runtime reply; it lands in the durable feedback store."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        session_id, assistant_id = _runtime_turn(client)

        upsert = client.post(
            f"/api/apps/p/runtime-app/sessions/{session_id}/messages/{assistant_id}/feedback",
            json={"rating": "negative", "comment": "wrong citation"},
        )
        assert upsert.status_code == 200, upsert.text
        body = upsert.json()
        assert body["rating"] == "negative"
        assert body["comment"] == "wrong citation"
        assert body["session_id"] == session_id

        fetched = client.get(
            f"/api/apps/p/runtime-app/sessions/{session_id}/messages/{assistant_id}/feedback"
        )
        assert fetched.status_code == 200
        assert fetched.json()["id"] == body["id"]

        # The platform-side feedback read observes the same row (promote path).
        platform = client.get(
            f"/api/chat/sessions/{session_id}/messages/{assistant_id}/feedback",
            headers=_admin(),
        )
        assert platform.status_code == 200
        assert platform.json()["rating"] == "negative"


def test_runtime_feedback_rejects_user_message_and_unknown_ids(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        session_id, _assistant_id = _runtime_turn(client)
        replay = client.get(f"/api/apps/p/runtime-app/sessions/{session_id}/messages")
        user_id = next(m["id"] for m in replay.json() if m["role"] == "user")

        rejected = client.post(
            f"/api/apps/p/runtime-app/sessions/{session_id}/messages/{user_id}/feedback",
            json={"rating": "positive"},
        )
        assert rejected.status_code == 422

        unknown = client.post(
            f"/api/apps/p/runtime-app/sessions/{session_id}/messages/ghost/feedback",
            json={"rating": "positive"},
        )
        assert unknown.status_code == 404

        foreign_session = client.post(
            f"/api/apps/p/runtime-app/sessions/not-a-session/messages/{user_id}/feedback",
            json={"rating": "positive"},
        )
        assert foreign_session.status_code == 404


def test_runtime_feedback_requires_token_for_link_app(tmp_path) -> None:
    """Link apps keep the token gate on the feedback surface too."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _setup(client)
        app_id = _bind_version(client, project_id, workflow_id)
        switched = client.put(
            f"/api/apps/{app_id}",
            headers=_admin(),
            json={"visibility": "link"},
        )
        assert switched.status_code == 200, switched.text
        # Switching public → link issues a fresh token; use that one.
        token = switched.json()["token"]

        session = client.post(
            "/api/apps/p/runtime-app/sessions",
            params={"t": token},
            json={"inputs": {"user_query": "link feedback"}},
        )
        assert session.status_code == 201, session.text
        session_id = session.json()["session_id"]
        sent = client.post(
            f"/api/apps/p/runtime-app/sessions/{session_id}/send",
            params={"t": token},
            json={"message": "link feedback", "inputs": {"user_query": "link feedback"}},
        )
        assert sent.status_code == 200, sent.text
        replay = client.get(
            f"/api/apps/p/runtime-app/sessions/{session_id}/messages", params={"t": token}
        )
        assistant_id = next(m["id"] for m in replay.json() if m["role"] == "assistant")

        missing = client.post(
            f"/api/apps/p/runtime-app/sessions/{session_id}/messages/{assistant_id}/feedback",
            json={"rating": "positive"},
        )
        assert missing.status_code == 401

        with_token = client.post(
            f"/api/apps/p/runtime-app/sessions/{session_id}/messages/{assistant_id}/feedback",
            params={"t": token},
            json={"rating": "positive"},
        )
        assert with_token.status_code == 200, with_token.text
