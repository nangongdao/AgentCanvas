"""Public HTTP contracts for request size, concurrency, and timeout policies."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from app.core.config import Settings, validate_runtime_settings
from app.core.operation_policies import request_policy_rules
from app.core.request_policy import (
    RequestPolicyConfig,
    RequestPolicyMiddleware,
    RequestPolicyRule,
)
from app.main import create_app


def _policy_app() -> tuple[FastAPI, asyncio.Event, asyncio.Event]:
    app = FastAPI()
    started = asyncio.Event()
    release = asyncio.Event()
    app.add_middleware(
        RequestPolicyMiddleware,
        default_config=RequestPolicyConfig(max_body_bytes=4),
        rules=(
            RequestPolicyRule.regex(
                "slow",
                r"^/slow$",
                RequestPolicyConfig(max_body_bytes=8, max_concurrent=1, timeout_seconds=1),
                methods={"POST"},
            ),
            RequestPolicyRule.regex(
                "timeout",
                r"^/timeout$",
                RequestPolicyConfig(max_body_bytes=8, timeout_seconds=0.01),
                methods={"POST"},
            ),
        ),
    )

    @app.post("/echo")
    async def echo(request: Request) -> PlainTextResponse:
        await request.body()
        return PlainTextResponse("ok")

    @app.post("/slow")
    async def slow() -> PlainTextResponse:
        started.set()
        await release.wait()
        return PlainTextResponse("ok")

    @app.post("/timeout")
    async def timeout() -> PlainTextResponse:
        await asyncio.sleep(0.05)
        return PlainTextResponse("ok")

    return app, started, release


@pytest.mark.asyncio
async def test_request_policy_rejects_oversized_body_and_times_out() -> None:
    app, _, _ = _policy_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        too_large = await client.post("/echo", content=b"12345")
        assert too_large.status_code == 413
        assert too_large.json()["detail"] == "request body exceeds configured limit"

        async def chunks():
            yield b"123"
            yield b"45"

        streamed = await client.post("/echo", content=chunks())
        assert streamed.status_code == 413
        assert streamed.json()["max_bytes"] == 4

        timed_out = await client.post("/timeout", content=b"ok")
        assert timed_out.status_code == 504
        assert timed_out.json()["detail"] == "operation timed out"


@pytest.mark.asyncio
async def test_request_policy_rejects_a_saturated_operation() -> None:
    app, started, release = _policy_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first_task = asyncio.create_task(client.post("/slow", content=b"ok"))
        await asyncio.wait_for(started.wait(), timeout=1)
        second = await client.post("/slow", content=b"ok")
        release.set()
        first = await first_task

        assert first.status_code == 200
        assert second.status_code == 429
        assert second.json()["detail"] == "operation concurrency limit reached"


def test_runtime_settings_reject_invalid_operation_limits(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="REQUEST_BODY_MAX_BYTES"):
        validate_runtime_settings(Settings(data_dir=tmp_path, request_body_max_bytes=0))
    with pytest.raises(RuntimeError, match="MODEL_TIMEOUT_SECONDS"):
        validate_runtime_settings(Settings(data_dir=tmp_path, model_timeout_seconds=0))
    with pytest.raises(RuntimeError, match="UPLOAD_REQUEST_MAX_BYTES"):
        validate_runtime_settings(
            Settings(
                data_dir=tmp_path,
                rag_max_upload_bytes=100,
                upload_request_max_bytes=100,
            )
        )


def test_application_request_policy_errors_keep_cors_headers(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        request_body_max_bytes=4,
        cors_origins=("http://client.test",),
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/auth/login",
            content=b"12345",
            headers={"Origin": "http://client.test"},
        )

    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == "http://client.test"


def test_mcp_health_uses_the_bounded_probe_policy(tmp_path) -> None:
    rules = request_policy_rules(Settings(data_dir=tmp_path, mcp_timeout_seconds=12))
    mcp_rule = next(rule for rule in rules if rule.name == "mcp_probe")

    assert mcp_rule.matches(
        {"type": "http", "method": "GET", "path": "/api/mcp/servers/demo/health"}
    )
    assert mcp_rule.config.timeout_seconds == 12
