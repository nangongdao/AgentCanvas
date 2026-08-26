"""HTTP Request node: outbound calls, retries, auth, and SSRF protection."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from app.core.outbound_http import (
    OutboundDestinationError,
    resolve_public_destination,
)
from app.core.secret_providers import (
    SecretResolver,
    build_secret_provider_chain,
)
from app.core.security import SecretBox
from app.engine.nodes.base import CompileContext
from app.engine.nodes.http_request import HttpRequestNodeExecutor
from app.engine.state import WorkflowState
from app.schemas.dsl import HttpRequestConfig, NodeSpec
from app.schemas.events import EventType

SECRET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="


@pytest.fixture(autouse=True)
def _stub_public_dns(monkeypatch):
    """Resolve .test hostnames to a stable public IP so the SSRF guard passes.

    Tests inject an httpx.MockTransport to control responses, but the SSRF
    guard still runs ``resolve_public_destination`` first. Without a DNS
    stub, ``.test`` hostnames fail to resolve. We map every .test host to a
    documentation-range public address so the guard accepts it while the
    mock transport handles the actual response.
    """

    def fake_getaddrinfo(host, *_args, **_kwargs):
        # Preserve real resolution for anything we don't own (e.g. loopback
        # addresses asserted by the SSRF rejection tests below).
        if isinstance(host, str) and host.endswith(".test"):
            # 93.184.216.34 is example.com's real address — a globally routable
            # IP so the SSRF guard accepts the synthetic .test hostname.
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            ]
        return real_getaddrinfo(host, *_args, **_kwargs)

    real_getaddrinfo = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


class FakeEmitter:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(
        self,
        event_type: EventType,
        *,
        node_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.events.append({"type": event_type, "node_id": node_id, "payload": payload or {}})

    def of_type(self, event_type: EventType) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == event_type]


def _resolver(tmp_path: Path) -> SecretResolver:
    box = SecretBox(SECRET_KEY)
    return SecretResolver(box, build_secret_provider_chain(tmp_path))


def _ctx(
    emitter: FakeEmitter,
    *,
    secret_resolver: Any | None = None,
    http_transport: httpx.MockTransport | None = None,
) -> CompileContext:
    return CompileContext(
        execution_id="http-test-exec",
        emitter=emitter,  # type: ignore[arg-type]
        get_provider=lambda _mid: None,
        max_loop_iterations=10,
        secret_resolver=secret_resolver,
        http_transport=http_transport,
    )


def _node(config: dict[str, Any]) -> NodeSpec:
    return NodeSpec(id="http", type="http", config=config)


async def _run(
    config: dict[str, Any],
    state: dict[str, Any],
    ctx: CompileContext,
) -> dict[str, Any]:
    fn = HttpRequestNodeExecutor().build(_node(config), ctx)
    result = await fn(cast(WorkflowState, state))
    assert isinstance(result, dict)
    return result


def _mock(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# --------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_with_json_extraction(tmp_path) -> None:
    received: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200, json={"data": {"items": [1, 2, 3]}})

    emitter = FakeEmitter()
    ctx = _ctx(emitter, http_transport=_mock(handler))
    result = await _run(
        {"method": "GET", "url": "https://api.example.test/v1/things"},
        {"inputs": {}, "node_outputs": {}},
        ctx,
    )
    out = result["node_outputs"]["http"]
    assert out["status_code"] == 200
    assert out["output"] == {"data": {"items": [1, 2, 3]}}
    assert out["json"] == {"data": {"items": [1, 2, 3]}}
    assert received[0].method == "GET"
    streaming = emitter.of_type(EventType.NODE_STREAMING)
    assert streaming[0]["payload"]["kind"] == "http_request"
    assert streaming[1]["payload"]["kind"] == "http_response"


@pytest.mark.asyncio
async def test_post_with_template_body_and_query(tmp_path) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode("utf-8")
        captured["content_type"] = request.headers.get("content-type")
        captured["accept"] = request.headers.get("accept")
        return httpx.Response(201, json={"created": True})

    emitter = FakeEmitter()
    ctx = _ctx(emitter, http_transport=_mock(handler))
    await _run(
        {
            "method": "POST",
            "url": "https://api.example.test/items",
            "query": {"page": "{{input.page}}"},
            "body": {"name": "{{input.name}}", "tags": ["a", "b"]},
            "expected_status": [201],
        },
        {"inputs": {"page": 3, "name": "widget"}, "node_outputs": {}},
        ctx,
    )
    assert "page=3" in captured["url"]
    body = json.loads(captured["body"])
    assert body == {"name": "widget", "tags": ["a", "b"]}
    assert captured["content_type"] == "application/json"


@pytest.mark.asyncio
async def test_json_path_extracts_nested_field(tmp_path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"result": {"value": 42, "extra": "drop"}}
        )

    ctx = _ctx(FakeEmitter(), http_transport=_mock(handler))
    result = await _run(
        {
            "method": "GET",
            "url": "https://api.example.test/v1",
            "response": {"extract_json": True, "json_path": "result.value"},
        },
        {"inputs": {}, "node_outputs": {}},
        ctx,
    )
    out = result["node_outputs"]["http"]
    assert out["output"] == 42
    assert out["json"] == {"result": {"value": 42, "extra": "drop"}}


@pytest.mark.asyncio
async def test_text_fallback_when_body_is_not_json(tmp_path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="plain text body")

    ctx = _ctx(FakeEmitter(), http_transport=_mock(handler))
    result = await _run(
        {"method": "GET", "url": "https://api.example.test/text"},
        {"inputs": {}, "node_outputs": {}},
        ctx,
    )
    out = result["node_outputs"]["http"]
    assert out["output"] == "plain text body"
    assert out["text"] == "plain text body"
    assert "json" not in out


# --------------------------------------------------------------------------
# Retry and failure
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retries_on_retryable_status(tmp_path, monkeypatch) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    # Avoid real sleeping in the retry backoff.
    async def _no_sleep(_secs: float) -> None:
        return None

    import app.engine.nodes.http_request as http_module

    monkeypatch.setattr(http_module.asyncio, "sleep", _no_sleep)
    ctx = _ctx(FakeEmitter(), http_transport=_mock(handler))
    result = await _run(
        {"method": "GET", "url": "https://api.example.test/flaky", "retry": {"max_attempts": 5}},
        {"inputs": {}, "node_outputs": {}},
        ctx,
    )
    assert result["node_outputs"]["http"]["status_code"] == 200
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_non_retryable_status_fails_node(tmp_path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    ctx = _ctx(FakeEmitter(), http_transport=_mock(handler))
    with pytest.raises(Exception, match="unexpected status 404"):
        await _run(
            {"method": "GET", "url": "https://api.example.test/missing"},
            {"inputs": {}, "node_outputs": {}},
            ctx,
        )


@pytest.mark.asyncio
async def test_connection_error_fails_after_attempts(tmp_path, monkeypatch) -> None:
    async def _no_sleep(_secs: float) -> None:
        return None

    import app.engine.nodes.http_request as http_module

    monkeypatch.setattr(http_module.asyncio, "sleep", _no_sleep)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    ctx = _ctx(FakeEmitter(), http_transport=_mock(handler))
    with pytest.raises(Exception, match="HTTP request failed"):
        await _run(
            {"method": "GET", "url": "https://api.example.test/down", "retry": {"max_attempts": 2}},
            {"inputs": {}, "node_outputs": {}},
            ctx,
        )


# --------------------------------------------------------------------------
# Auth via secret references
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bearer_auth_resolves_secret_reference(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HTTP_BEARER_TOKEN", "bearer-secret-123")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"ok": True})

    ctx = _ctx(FakeEmitter(), secret_resolver=_resolver(tmp_path), http_transport=_mock(handler))
    await _run(
        {
            "method": "GET",
            "url": "https://api.example.test/secured",
            "auth": {"type": "bearer", "token_ref": "env://HTTP_BEARER_TOKEN"},
        },
        {"inputs": {}, "node_outputs": {}},
        ctx,
    )
    assert captured["auth"] == "Bearer bearer-secret-123"


@pytest.mark.asyncio
async def test_basic_auth_resolves_username_and_password(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HTTP_BASIC_PASSWORD", "pw-secret")
    import base64

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"ok": True})

    ctx = _ctx(FakeEmitter(), secret_resolver=_resolver(tmp_path), http_transport=_mock(handler))
    await _run(
        {
            "method": "GET",
            "url": "https://api.example.test/basic",
            "auth": {
                "type": "basic",
                "username": "{{input.user}}",
                "password_ref": "env://HTTP_BASIC_PASSWORD",
            },
        },
        {"inputs": {"user": "alice"}, "node_outputs": {}},
        ctx,
    )
    expected = base64.b64encode(b"alice:pw-secret").decode("ascii")
    assert captured["auth"] == f"Basic {expected}"


@pytest.mark.asyncio
async def test_custom_header_auth_with_prefix(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HTTP_API_KEY", "key-secret-789")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["hdr"] = request.headers.get("x-api-key", "")
        return httpx.Response(200, json={"ok": True})

    ctx = _ctx(FakeEmitter(), secret_resolver=_resolver(tmp_path), http_transport=_mock(handler))
    await _run(
        {
            "method": "GET",
            "url": "https://api.example.test/header-auth",
            "auth": {
                "type": "header",
                "header_name": "X-Api-Key",
                "value_prefix": "",
                "token_ref": "env://HTTP_API_KEY",
            },
        },
        {"inputs": {}, "node_outputs": {}},
        ctx,
    )
    assert captured["hdr"] == "key-secret-789"


# --------------------------------------------------------------------------
# SSRF protection — the core security guarantee
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",  # loopback
        "http://localhost/",  # loopback hostname
        "http://10.0.0.1/",  # private class A
        "http://192.168.1.1/",  # private class C
        "http://169.254.169.254/",  # link-local / cloud metadata
        "http://[::1]/",  # IPv6 loopback
        "http://[fc00::1]/",  # IPv6 unique-local
        "http://[fe80::1]/",  # IPv6 link-local
        "ftp://example.com/",  # non-HTTP scheme
    ],
)
def test_resolve_public_destination_rejects_private_and_non_http(url: str) -> None:
    with pytest.raises(OutboundDestinationError):
        resolve_public_destination(url)


def test_resolve_public_destination_rejects_dns_rebinding_to_private(monkeypatch) -> None:
    def mixed_addresses(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", mixed_addresses)
    with pytest.raises(OutboundDestinationError, match="non-public"):
        resolve_public_destination("https://rebinding.example.test/")


@pytest.mark.asyncio
async def test_default_config_blocks_private_network(tmp_path) -> None:
    """The SSRF guard rejects link-local targets even with no transport injected.

    169.254.169.254 is the cloud metadata endpoint — a canonical SSRF target.
    The default config (allow_private_network=False) pins to public IPs only,
    so resolve_public_destination rejects this before any connection attempt.
    """
    ctx = _ctx(FakeEmitter())  # http_transport=None → real pinned client path
    with pytest.raises(Exception, match="non-public"):
        await _run(
            {"method": "GET", "url": "http://169.254.169.254/latest/meta-data/"},
            {"inputs": {}, "node_outputs": {}},
            ctx,
        )


@pytest.mark.asyncio
async def test_allow_private_network_opt_in(tmp_path) -> None:
    """allow_private_network=True lifts the public-IP pin for on-prem targets."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["host"] = str(request.url)
        return httpx.Response(200, json={"internal": True})

    ctx = _ctx(FakeEmitter(), http_transport=_mock(handler))
    result = await _run(
        {
            "method": "GET",
            "url": "http://10.0.0.5/internal",
            "allow_private_network": True,
        },
        {"inputs": {}, "node_outputs": {}},
        ctx,
    )
    assert result["node_outputs"]["http"]["status_code"] == 200
    assert "10.0.0.5/internal" in captured["host"]


# --------------------------------------------------------------------------
# Config validation
# --------------------------------------------------------------------------


def test_bearer_auth_requires_token_ref() -> None:
    with pytest.raises(Exception, match="token_ref"):
        HttpRequestConfig.model_validate(
            {"url": "https://example.test", "auth": {"type": "bearer"}}
        )


def test_basic_auth_requires_password_ref() -> None:
    with pytest.raises(Exception, match="password_ref"):
        HttpRequestConfig.model_validate(
            {
                "url": "https://example.test",
                "auth": {"type": "basic", "username": "u"},
            }
        )


def test_none_auth_allows_empty_refs() -> None:
    cfg = HttpRequestConfig.model_validate(
        {"url": "https://example.test", "auth": {"type": "none"}}
    )
    assert cfg.auth.type == "none"
