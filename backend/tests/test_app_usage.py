"""Per-application usage aggregation and end-user rate limiting (C3-5)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.test_apps import _admin, _published_version, _settings

FERNET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="
RATE_ADMIN_TOKEN = "test-admin-token-usage"


def _rate_limited_settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token=RATE_ADMIN_TOKEN,
        secret_key=FERNET_KEY,
        rate_limit_app_runtime_requests=2,
        rate_limit_window_seconds=60,
        rate_limit_default_requests=1000,
    )


def _agent_dsl(name: str = "Usage Agent Workflow") -> dict:
    return {
        "version": "1.0",
        "name": name,
        "variables": [{"name": "user_query", "type": "string", "required": True}],
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
                "id": "agent",
                "type": "agent",
                "position": {"x": 200, "y": 0},
                "config": {"model_config_id": "default", "user_prompt": "{{input.user_query}}"},
            },
            {
                "id": "end",
                "type": "end",
                "position": {"x": 400, "y": 0},
                "config": {"output_template": {"answer": "{{nodes.agent.output}}"}},
            },
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "agent"},
            {"id": "e2", "source": "agent", "target": "end"},
        ],
    }


def _echo_dsl(name: str = "Usage Echo Workflow") -> dict:
    return {
        "version": "1.0",
        "name": name,
        "variables": [{"name": "user_query", "type": "string", "required": True}],
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
                "config": {"output_template": {"answer": "{{nodes.start.output.user_query}}"}},
            },
        ],
        "edges": [{"id": "e1", "source": "start", "target": "end"}],
    }


def _setup_app(
    client: TestClient, *, dsl: dict | None = None, name: str = "Usage App"
) -> tuple[str, str, str, str, str]:
    """Create org/project/published workflow + public bound app; return ids."""
    suffix = name.lower().replace(" ", "-")
    registered = client.post(
        "/api/auth/register",
        json={
            "email": f"{suffix}@example.test",
            "password": "usage-password",
            "display_name": name,
            "role": "admin",
        },
    )
    assert registered.status_code == 201, registered.text
    org = client.post("/api/organizations", json={"name": f"{name} Org"})
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]
    project = client.post(
        f"/api/organizations/{org_id}/projects", json={"name": f"{name} Project"}
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    workflow = client.post(
        "/api/workflows",
        headers=_admin(),
        json={
            "name": f"{name} Workflow",
            "project_id": project_id,
            "dsl": dsl or _echo_dsl(),
        },
    )
    assert workflow.status_code == 201, workflow.text
    workflow_id = workflow.json()["id"]
    assert (
        client.post(f"/api/workflows/{workflow_id}/publish", headers=_admin()).status_code == 200
    )
    version_id = _published_version(client, workflow_id)
    created = client.post(
        "/api/apps",
        headers=_admin(),
        json={
            "project_id": project_id,
            "workflow_id": workflow_id,
            "name": name,
            "visibility": "public",
        },
    )
    assert created.status_code == 201, created.text
    app_row = created.json()["app"]
    bound = client.post(
        f"/api/apps/{app_row['id']}/version",
        headers=_admin(),
        json={"version_id": version_id},
    )
    assert bound.status_code == 200, bound.text
    return org_id, project_id, workflow_id, app_row["id"], app_row["slug"]


def _new_runtime_session(client: TestClient, slug: str) -> str:
    created = client.post(f"/api/apps/p/{slug}/sessions", json={"inputs": {}})
    assert created.status_code == 201, created.text
    return created.json()["session_id"]


def _send(client: TestClient, slug: str, session_id: str, message: str):
    return client.post(f"/api/apps/p/{slug}/sessions/{session_id}/send", json={"message": message})


def test_usage_aggregates_chat_activity_and_feedback(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, _project_id, _workflow_id, app_id, slug = _setup_app(client)

        session_a = _new_runtime_session(client, slug)
        session_b = _new_runtime_session(client, slug)
        for sid, message in ((session_a, "first"), (session_b, "second")):
            sent = _send(client, slug, sid, message)
            assert sent.status_code == 200, sent.text

        messages_a = client.get(f"/api/apps/p/{slug}/sessions/{session_a}/messages").json()
        messages_b = client.get(f"/api/apps/p/{slug}/sessions/{session_b}/messages").json()
        assistant_a = next(m["id"] for m in messages_a if m["role"] == "assistant")
        assistant_b = next(m["id"] for m in messages_b if m["role"] == "assistant")
        assert (
            client.post(
                f"/api/apps/p/{slug}/sessions/{session_a}/messages/{assistant_a}/feedback",
                json={"rating": "positive"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/apps/p/{slug}/sessions/{session_b}/messages/{assistant_b}/feedback",
                json={"rating": "negative"},
            ).status_code
            == 200
        )

        usage = client.get(f"/api/apps/{app_id}/usage", headers=_admin())
        assert usage.status_code == 200, usage.text
        body = usage.json()
        assert body["app_id"] == app_id
        assert body["days"] == 30
        assert body["sessions"] == 2
        assert body["user_messages"] == 2
        assert body["assistant_messages"] == 2
        assert body["executions"] == 2
        assert body["positive_feedback"] == 1
        assert body["negative_feedback"] == 1
        assert body["feedback_rate"] == 0.5
        assert body["available_citations"] == 0
        assert body["referenced_citations"] == 0
        assert body["citation_coverage"] is None
        # The echo workflow has no agent attempts: zero tokens, known zero cost.
        assert body["total_tokens"] == 0
        assert body["cost_known"] is True
        assert body["estimated_cost_usd"] == "0.000000000000"
        # A single daily bucket covering today's activity.
        assert len(body["daily"]) == 1
        day = body["daily"][0]
        assert day["sessions"] == 2
        assert day["messages"] == 4
        assert day["executions"] == 2


def test_usage_reports_unknown_cost_without_pricing(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, _project_id, _workflow_id, app_id, slug = _setup_app(
            client, dsl=_agent_dsl(), name="Unpriced"
        )

        sid = _new_runtime_session(client, slug)
        sent = _send(client, slug, sid, "price me")
        assert sent.status_code == 200, sent.text

        usage = client.get(f"/api/apps/{app_id}/usage", headers=_admin()).json()
        # The seeded default model reports usage but has no rates configured,
        # so the app total must be unknown rather than a silent zero.
        assert usage["total_tokens"] > 0
        assert usage["cost_known"] is False
        assert usage["estimated_cost_usd"] is None


def test_usage_prices_tokens_after_model_pricing(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, _project_id, _workflow_id, app_id, slug = _setup_app(
            client, dsl=_agent_dsl(), name="Priced"
        )
        priced = client.put(
            "/api/models/default",
            headers=_admin(),
            json={
                "prompt_price_per_million_usd": "1.5",
                "completion_price_per_million_usd": "2.5",
            },
        )
        assert priced.status_code == 200, priced.text

        sid = _new_runtime_session(client, slug)
        sent = _send(client, slug, sid, "price me")
        assert sent.status_code == 200, sent.text

        usage = client.get(f"/api/apps/{app_id}/usage", headers=_admin()).json()
        assert usage["total_tokens"] > 0
        assert usage["cost_known"] is True
        assert usage["estimated_cost_usd"] is not None
        expected = (
            usage["prompt_tokens"] * 1.5 + usage["completion_tokens"] * 2.5
        ) / 1_000_000
        actual = float(usage["estimated_cost_usd"])
        assert abs(actual - expected) < 1e-9


def test_usage_requires_authentication(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, _project_id, _workflow_id, app_id, _slug = _setup_app(client)
        # Drop the bootstrap admin's session cookie to probe unauthenticated.
        client.cookies.clear()
        assert client.get(f"/api/apps/{app_id}/usage").status_code == 401


def test_usage_days_bounds(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, _project_id, _workflow_id, app_id, _slug = _setup_app(client)
        assert client.get(f"/api/apps/{app_id}/usage?days=0", headers=_admin()).status_code == 422
        assert client.get(f"/api/apps/{app_id}/usage?days=91", headers=_admin()).status_code == 422
        bounded = client.get(f"/api/apps/{app_id}/usage?days=7", headers=_admin())
        assert bounded.status_code == 200
        assert bounded.json()["days"] == 7
        assert bounded.json()["since"]


def test_runtime_send_rate_limited_per_session(tmp_path) -> None:
    with TestClient(create_app(_rate_limited_settings(tmp_path))) as client:
        admin = {"Authorization": f"Bearer {RATE_ADMIN_TOKEN}"}
        registered = client.post(
            "/api/auth/register",
            json={
                "email": "rate-admin@example.test",
                "password": "rate-password",
                "display_name": "Rate Admin",
                "role": "admin",
            },
        )
        assert registered.status_code == 201, registered.text
        org = client.post("/api/organizations", json={"name": "Usage Org"})
        assert org.status_code == 201, org.text
        org_id = org.json()["id"]
        project = client.post(
            f"/api/organizations/{org_id}/projects",
            json={"name": "Usage Project"},
            headers=admin,
        )
        assert project.status_code == 201, project.text
        project_id = project.json()["id"]
        workflow = client.post(
            "/api/workflows",
            headers=admin,
            json={
                "name": "Rate Limit WF",
                "project_id": project_id,
                "dsl": _echo_dsl("Rate Limit WF"),
            },
        )
        assert workflow.status_code == 201, workflow.text
        workflow_id = workflow.json()["id"]
        assert (
            client.post(f"/api/workflows/{workflow_id}/publish", headers=admin).status_code == 200
        )
        versions = client.get(f"/api/workflows/{workflow_id}/versions", headers=admin).json()
        version_id = next(v["id"] for v in versions if v["status"] == "published")
        created = client.post(
            "/api/apps",
            headers=admin,
            json={
                "project_id": project_id,
                "workflow_id": workflow_id,
                "name": "Rate Limit App",
                "visibility": "public",
            },
        )
        assert created.status_code == 201, created.text
        app_row = created.json()["app"]
        assert (
            client.post(
                f"/api/apps/{app_row['id']}/version",
                headers=admin,
                json={"version_id": version_id},
            ).status_code
            == 200
        )
        slug = app_row["slug"]

        sid = _new_runtime_session(client, slug)
        assert _send(client, slug, sid, "one").status_code == 200
        assert _send(client, slug, sid, "two").status_code == 200

        third = _send(client, slug, sid, "three")
        assert third.status_code == 429, third.text
        assert third.headers.get("retry-after") == "60"

        # The budget is per conversation: a fresh session is unaffected.
        fresh_sid = _new_runtime_session(client, slug)
        assert _send(client, slug, fresh_sid, "ok").status_code == 200
