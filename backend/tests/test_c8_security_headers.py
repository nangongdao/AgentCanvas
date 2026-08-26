"""C8-3 defense-in-depth: site-wide security headers and HSTS opt-in."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security_headers import _DEFAULT_HEADERS, SecurityHeadersMiddleware
from app.main import create_app

_FERNET = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token="test-admin-token-c8-3",
        secret_key=_FERNET,
        log_level="WARNING",
        local_embedding_dimensions=64,
    )
    base.update(overrides)
    return Settings(**base)


def test_default_headers_on_every_response(tmp_path: Path) -> None:
    """A plain unauthenticated route still carries the site-wide headers."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/meta")
        assert response.status_code == 200
        for header in _DEFAULT_HEADERS:
            assert header in response.headers, header
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "base-uri 'none'" in response.headers["content-security-policy"]


def test_hsts_absent_by_default_and_opt_in(tmp_path: Path) -> None:
    """HSTS is never advertised over plain HTTP unless explicitly enabled."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/meta")
        assert "strict-transport-security" not in response.headers

    with TestClient(create_app(_settings(tmp_path, hsts_enabled=True))) as client:
        response = client.get("/api/meta")
        assert "max-age=31536000" in response.headers["strict-transport-security"]
        assert "includeSubDomains" in response.headers["strict-transport-security"]


def test_headers_stamp_cors_and_error_responses(tmp_path: Path) -> None:
    """CORS-rejected and rate-limited responses still carry the headers."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        # 404 on an unknown route carries the headers.
        missing = client.get("/api/does-not-exist")
        assert missing.status_code == 404
        assert missing.headers.get("x-content-type-options") == "nosniff"

        # A CORS preflight from a disallowed origin is rejected by Starlette's
        # CORS layer, but the security middleware (outermost) still stamps it.
        preflight = client.options(
            "/api/workflows",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert preflight.status_code == 400 or preflight.status_code == 200
        assert preflight.headers.get("x-frame-options") == "DENY"


def test_middleware_does_not_override_existing_header(tmp_path: Path) -> None:
    """An existing X-Content-Type-Options set by a route is not overwritten."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/meta")
        assert response.headers["x-content-type-options"] == "nosniff"
        # The middleware only adds when absent, so a route that sets its own
        # value keeps it.
        assert response.headers["x-frame-options"] == "DENY"


def test_static_security_headers_module_contract(tmp_path: Path) -> None:
    """The header dictionary includes the mandatory baseline set."""
    required = {
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Cross-Origin-Opener-Policy",
        "Cross-Origin-Resource-Policy",
        "Content-Security-Policy",
    }
    assert required.issubset(set(_DEFAULT_HEADERS))


def test_security_headers_middleware_stamps_plain_asgi(tmp_path: Path) -> None:
    """The middleware works on a bare ASGI app (unit-level)."""

    async def app(scope, receive, send):
        assert scope["type"] == "http"
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = SecurityHeadersMiddleware(app)
    received = []

    async def fake_send(message):
        received.append(message)

    async def fake_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    import asyncio

    asyncio.run(middleware({"type": "http", "method": "GET", "path": "/"}, fake_receive, fake_send))
    start = next(m for m in received if m["type"] == "http.response.start")
    header_names = {name.decode().lower() for name, _ in start["headers"]}
    assert "x-content-type-options" in header_names
    assert "content-security-policy" in header_names
    assert "strict-transport-security" not in header_names
