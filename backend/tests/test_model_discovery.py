"""Tests for provider endpoint discovery (model list probe + test connection).

The per-provider mapping tests monkeypatch ``request_public`` so they stay
hermetic: the fake records what would have been sent and returns a canned
response. One test deliberately does NOT monkeypatch, to prove the SSRF guard
still rejects a private destination (127.0.0.1 needs no DNS, so it is safe to
run offline).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services import model_discovery
from app.services.model_discovery import (
    ModelDiscoveryError,
    discover_models,
    supported_providers,
)

SECRET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="
API_KEY = "sk-live-super-secret-1234567890"


def _settings(tmp_path) -> Settings:
    # auth disabled -> local-development principal resolves to Admin
    return Settings(data_dir=tmp_path, environment="test", auth_mode="disabled", secret_key=SECRET_KEY)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as c:
        yield c


def _fake_public(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: httpx.Response | None = None,
    exc: Exception | None = None,
    sent: list[dict[str, Any]] | None = None,
) -> None:
    """Replace the SSRF-safe helper with a recorder so tests stay offline."""

    async def _call(
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | str | None = None,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> httpx.Response:
        if sent is not None:
            sent.append({"method": method, "url": url, "headers": dict(headers or {})})
        if exc is not None:
            raise exc
        assert response is not None
        return response

    monkeypatch.setattr(model_discovery, "request_public", _call)


# --------------------------------------------------------------------------
# Provider coverage
# --------------------------------------------------------------------------


def test_every_registered_provider_is_probeable_or_explicitly_excluded() -> None:
    supported = set(supported_providers())
    assert supported == {"anthropic", "gemini", "mock", "ollama", "openai_compat"}


def test_supported_providers_is_sorted() -> None:
    assert list(supported_providers()) == sorted(supported_providers())


# --------------------------------------------------------------------------
# Per-provider mapping
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_provider_answers_without_network() -> None:
    result = await discover_models("mock")
    assert result.provider == "mock"
    assert [model.id for model in result.models] == ["mock-chat", "mock-embed"]
    assert [model.kind for model in result.models] == ["chat", "embedding"]


@pytest.mark.asyncio
async def test_openai_compatible_endpoint_is_probed_with_bearer_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, Any]] = []
    response = httpx.Response(
        200,
        json={
            "object": "list",
            "data": [
                {"id": "gpt-4o-mini", "owned_by": "openai"},
                {"id": "text-embedding-3-small", "owned_by": "openai"},
            ],
        },
    )
    _fake_public(monkeypatch, response=response, sent=sent)

    result = await discover_models(
        "openai_compat", base_url="https://gateway.example.com/v1", api_key=API_KEY
    )

    assert sent[0]["method"] == "GET"
    assert sent[0]["url"] == "https://gateway.example.com/v1/models"
    assert sent[0]["headers"]["authorization"] == f"Bearer {API_KEY}"
    assert [(model.id, model.kind) for model in result.models] == [
        ("gpt-4o-mini", "chat"),
        ("text-embedding-3-small", "embedding"),
    ]
    assert result.models[0].owned_by == "openai"


@pytest.mark.asyncio
async def test_openai_compatible_defaults_to_the_vendor_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, Any]] = []
    _fake_public(monkeypatch, response=httpx.Response(200, json={"data": []}), sent=sent)
    await discover_models("openai_compat", api_key=API_KEY)
    assert sent[0]["url"] == "https://api.openai.com/v1/models"


@pytest.mark.asyncio
async def test_anthropic_sends_the_api_key_and_version_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, Any]] = []
    response = httpx.Response(
        200,
        json={"data": [{"id": "claude-sonnet-5", "display_name": "Claude Sonnet 5"}]},
    )
    _fake_public(monkeypatch, response=response, sent=sent)

    result = await discover_models("anthropic", api_key=API_KEY)

    assert sent[0]["url"] == "https://api.anthropic.com/v1/models"
    assert sent[0]["headers"]["x-api-key"] == API_KEY
    assert sent[0]["headers"]["anthropic-version"] == "2023-06-01"
    assert "authorization" not in sent[0]["headers"]
    assert result.models[0].id == "claude-sonnet-5"
    assert result.models[0].owned_by == "Claude Sonnet 5"


@pytest.mark.asyncio
async def test_gemini_strips_the_models_prefix_and_reads_generation_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, Any]] = []
    response = httpx.Response(
        200,
        json={
            "models": [
                {
                    "name": "models/gemini-2.5-flash",
                    "displayName": "Gemini 2.5 Flash",
                    "supportedGenerationMethods": ["generateContent", "countTokens"],
                },
                {
                    "name": "models/gemini-embedding-001",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        },
    )
    _fake_public(monkeypatch, response=response, sent=sent)

    result = await discover_models("gemini", api_key=API_KEY)

    assert sent[0]["url"] == "https://generativelanguage.googleapis.com/v1beta/models"
    assert sent[0]["headers"]["x-goog-api-key"] == API_KEY
    assert [(model.id, model.kind) for model in result.models] == [
        ("gemini-2.5-flash", "chat"),
        ("gemini-embedding-001", "embedding"),
    ]


@pytest.mark.asyncio
async def test_ollama_lists_tags_without_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []
    response = httpx.Response(
        200,
        json={
            "models": [
                {"name": "llama3.2:latest", "details": {"family": "llama"}},
                {"name": "nomic-embed-text:latest", "details": {}},
            ]
        },
    )
    _fake_public(monkeypatch, response=response, sent=sent)

    result = await discover_models("ollama")

    assert sent[0]["url"] == "http://localhost:11434/api/tags"
    assert "authorization" not in sent[0]["headers"]
    assert [(model.id, model.kind) for model in result.models] == [
        ("llama3.2:latest", "chat"),
        ("nomic-embed-text:latest", "embedding"),
    ]
    assert result.models[0].owned_by == "llama"


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_status_is_surfaced_with_the_vendor_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_public(
        monkeypatch,
        response=httpx.Response(401, json={"error": {"message": "invalid api key"}}),
    )
    with pytest.raises(ModelDiscoveryError) as excinfo:
        await discover_models("openai_compat", api_key=API_KEY)
    assert "HTTP 401" in str(excinfo.value)
    assert "invalid api key" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_vendor_error_that_echoes_the_key_does_not_leak_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_public(
        monkeypatch,
        response=httpx.Response(
            401, json={"error": {"message": f"the key {API_KEY} is not valid"}}
        ),
    )
    with pytest.raises(ModelDiscoveryError) as excinfo:
        await discover_models("anthropic", api_key=API_KEY)
    assert API_KEY not in str(excinfo.value)
    assert "***" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_non_json_success_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_public(monkeypatch, response=httpx.Response(200, text="<html>gateway</html>"))
    with pytest.raises(ModelDiscoveryError, match="did not return JSON"):
        await discover_models("openai_compat", api_key=API_KEY)


@pytest.mark.asyncio
async def test_a_response_without_the_expected_list_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_public(monkeypatch, response=httpx.Response(200, json={"data": "nope"}))
    with pytest.raises(ModelDiscoveryError, match="no 'data' list"):
        await discover_models("openai_compat", api_key=API_KEY)


@pytest.mark.asyncio
async def test_timeout_is_reported_as_such(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_public(monkeypatch, exc=httpx.ConnectTimeout("too slow"))
    with pytest.raises(ModelDiscoveryError, match="timed out"):
        await discover_models("ollama", timeout_seconds=3.0)


@pytest.mark.asyncio
async def test_transport_failure_is_reported_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_public(monkeypatch, exc=httpx.ConnectError("refused"))
    with pytest.raises(ModelDiscoveryError, match="could not be reached"):
        await discover_models("ollama")


@pytest.mark.asyncio
async def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ModelDiscoveryError, match="does not support model discovery"):
        await discover_models("vertex")


@pytest.mark.asyncio
async def test_base_url_must_be_absolute() -> None:
    with pytest.raises(ModelDiscoveryError, match=r"absolute http\(s\) URL"):
        await discover_models("openai_compat", base_url="api.example.com/v1")


# --------------------------------------------------------------------------
# SSRF gate
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_private_destination_is_refused_without_an_explicit_opt_in() -> None:
    # No monkeypatch: the real guard runs. 127.0.0.1 needs no DNS.
    with pytest.raises(ModelDiscoveryError) as excinfo:
        await discover_models("ollama", base_url="http://127.0.0.1:11434")
    assert "public endpoint" in str(excinfo.value)
    assert "allow private network" in str(excinfo.value)


@pytest.mark.asyncio
async def test_private_destination_is_allowed_when_opted_in() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "llama3.2:latest"}]})

    result = await discover_models(
        "ollama",
        base_url="http://127.0.0.1:11434",
        allow_private_network=True,
        transport=httpx.MockTransport(handler),
    )
    assert [model.id for model in result.models] == ["llama3.2:latest"]


# --------------------------------------------------------------------------
# HTTP contract
# --------------------------------------------------------------------------


def test_endpoint_lists_the_mock_catalog(client: TestClient) -> None:
    resp = client.post("/api/models/discover", json={"provider": "mock"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "mock"
    assert [row["id"] for row in body["models"]] == ["mock-chat", "mock-embed"]
    assert body["models"][1]["kind"] == "embedding"
    assert isinstance(body["latency_ms"], int)


def test_endpoint_audits_the_probe(client: TestClient) -> None:
    assert client.post("/api/models/discover", json={"provider": "mock"}).status_code == 200
    logs = client.get("/api/audit-logs", params={"action": "model.discovered"})
    assert logs.status_code == 200, logs.text
    items = logs.json()["items"]
    assert any(row["action"] == "model.discovered" for row in items)


def test_endpoint_rejects_an_unknown_provider(client: TestClient) -> None:
    resp = client.post("/api/models/discover", json={"provider": "vertex"})
    assert resp.status_code == 422
    assert "unknown provider" in resp.json()["detail"]


def test_endpoint_rejects_a_private_destination_by_default(client: TestClient) -> None:
    resp = client.post(
        "/api/models/discover",
        json={"provider": "ollama", "base_url": "http://127.0.0.1:11434"},
    )
    assert resp.status_code == 400
    assert "public endpoint" in resp.json()["detail"]


def test_endpoint_reuses_a_saved_config_key_and_base_url(client: TestClient, tmp_path) -> None:
    created = client.post(
        "/api/models",
        json={
            "id": "discover-mock",
            "name": "Discover Mock",
            "provider": "mock",
            "model_name": "mock-chat",
            "api_key": API_KEY,
            "kind": "chat",
        },
    )
    assert created.status_code == 201, created.text

    resp = client.post(
        "/api/models/discover",
        json={"provider": "mock", "model_config_id": "discover-mock"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["provider"] == "mock"


def test_endpoint_rejects_an_unknown_config_reference(client: TestClient) -> None:
    resp = client.post(
        "/api/models/discover",
        json={"provider": "mock", "model_config_id": "does-not-exist"},
    )
    assert resp.status_code == 404


def test_endpoint_rejects_a_provider_that_mismatches_the_config(client: TestClient) -> None:
    created = client.post(
        "/api/models",
        json={
            "id": "discover-anthropic",
            "name": "Discover Anthropic",
            "provider": "anthropic",
            "model_name": "claude-sonnet-5",
            "kind": "chat",
        },
    )
    assert created.status_code == 201, created.text

    resp = client.post(
        "/api/models/discover",
        json={"provider": "mock", "model_config_id": "discover-anthropic"},
    )
    assert resp.status_code == 422
    assert "does not match" in resp.json()["detail"]


def test_endpoint_requires_authentication(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token="discover-admin-token-long-enough",
        editor_api_token="discover-editor-token-long-enough",
        viewer_api_token="discover-viewer-token-long-enough",
        secret_key=SECRET_KEY,
    )
    with TestClient(create_app(settings)) as c:
        anonymous = c.post("/api/models/discover", json={"provider": "mock"})
        # Discovery is admin-gated like the rest of model configuration.
        editor = c.post(
            "/api/models/discover",
            json={"provider": "mock"},
            headers={"Authorization": "Bearer discover-editor-token-long-enough"},
        )
        admin = c.post(
            "/api/models/discover",
            json={"provider": "mock"},
            headers={"Authorization": "Bearer discover-admin-token-long-enough"},
        )
    assert anonymous.status_code == 401
    assert editor.status_code == 403
    assert admin.status_code == 200
