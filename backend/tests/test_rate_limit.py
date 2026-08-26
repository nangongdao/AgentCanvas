"""HTTP behavior tests for the single-instance request limiter."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, validate_runtime_settings
from app.core.ratelimit import RateLimitConfig, RateLimitMiddleware, RateLimitRule
from app.main import create_app


class _FakeRedisRateLimiter:
    def __init__(self, result: list[int]) -> None:
        self.result = result
        self.calls = 0

    async def eval(self, *_args: object) -> list[int]:
        self.calls += 1
        return self.result


def test_route_family_shares_a_bucket_and_health_is_exempt() -> None:
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        configs={
            "default": RateLimitConfig(10, 60),
            "/api/items": RateLimitConfig(2, 60),
        },
    )

    @app.get("/api/items/{item_id}")
    async def get_item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    with TestClient(app) as client:
        first = client.get("/api/items/one")
        second = client.get("/api/items/two")
        blocked = client.get("/api/items/three")

        assert first.status_code == 200
        assert first.headers["X-RateLimit-Limit"] == "2"
        assert first.headers["X-RateLimit-Remaining"] == "1"
        assert second.status_code == 200
        assert second.headers["X-RateLimit-Remaining"] == "0"
        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"]
        assert blocked.json()["detail"] == "Too many requests"

        assert all(client.get("/healthz").status_code == 200 for _ in range(3))


def test_method_specific_dynamic_routes_use_a_named_bucket() -> None:
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        default_config=RateLimitConfig(10, 60),
        rules=(
            RateLimitRule.regex(
                "execution_start",
                r"^/api/workflows/[^/]+/run$",
                RateLimitConfig(1, 60),
                methods={"POST"},
            ),
        ),
    )

    @app.api_route("/api/workflows/{workflow_id}/run", methods=["GET", "POST"])
    async def run_workflow(workflow_id: str) -> dict[str, str]:
        return {"id": workflow_id}

    with TestClient(app) as client:
        assert client.get("/api/workflows/one/run").status_code == 200
        assert client.get("/api/workflows/two/run").status_code == 200
        assert client.post("/api/workflows/one/run").status_code == 200
        blocked = client.post("/api/workflows/two/run")
        assert blocked.status_code == 429
        assert blocked.headers["X-RateLimit-Limit"] == "1"


def test_configured_redis_rate_limit_counter_is_used() -> None:
    redis = _FakeRedisRateLimiter([1, 0, 0])
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        default_config=RateLimitConfig(10, 60),
        redis_client=redis,
    )

    @app.get("/api/items/{item_id}")
    async def get_item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    with TestClient(app) as client:
        assert client.get("/api/items/one").status_code == 200
    assert redis.calls == 1


def test_configured_redis_rate_limit_fails_closed() -> None:
    class BrokenRedis:
        async def eval(self, *_args: object) -> list[int]:
            raise ConnectionError("redis unavailable")

    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        default_config=RateLimitConfig(10, 60),
        redis_client=BrokenRedis(),
    )

    @app.get("/api/items/{item_id}")
    async def get_item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    with TestClient(app) as client:
        assert client.get("/api/items/one").status_code == 503


def test_application_applies_the_execution_start_policy(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, environment="test", auth_mode="disabled")
    with TestClient(create_app(settings)) as client:
        for index in range(10):
            response = client.post(
                f"/api/workflows/{index:032d}/run",
                json={"inputs": {}},
            )
            assert response.status_code == 404

        blocked = client.post(
            "/api/workflows/99999999999999999999999999999999/run",
            json={"inputs": {}},
        )
        assert blocked.status_code == 429
        assert blocked.headers["X-RateLimit-Limit"] == "10"


def test_application_login_policy_allows_normal_role_switching(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token="admin-token-with-more-than-16-characters",
        editor_api_token="editor-token",
        viewer_api_token="viewer-token",
    )
    with TestClient(create_app(settings)) as client:
        headers = {"Origin": "http://localhost:5173"}
        for _ in range(10):
            response = client.post(
                "/api/auth/login",
                json={"token": "incorrect-token"},
                headers=headers,
            )
            assert response.status_code == 401

        blocked = client.post(
            "/api/auth/login",
            json={"token": "incorrect-token"},
            headers=headers,
        )
        assert blocked.status_code == 429
        assert blocked.headers["X-RateLimit-Limit"] == "10"
        assert blocked.headers["Access-Control-Allow-Origin"] == headers["Origin"]


def test_application_rate_limits_are_configurable(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token="admin-token-with-more-than-16-characters",
        editor_api_token="editor-token",
        viewer_api_token="viewer-token",
        rate_limit_login_requests=2,
    )
    with TestClient(create_app(settings)) as client:
        for _ in range(2):
            response = client.post(
                "/api/auth/login",
                json={"token": "incorrect-token"},
            )
            assert response.status_code == 401
        blocked = client.post(
            "/api/auth/login",
            json={"token": "incorrect-token"},
        )
        assert blocked.status_code == 429
        assert blocked.headers["X-RateLimit-Limit"] == "2"


def test_refresh_and_interactive_login_use_separate_buckets(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token="admin-token-with-more-than-16-characters",
        rate_limit_login_requests=2,
    )
    with TestClient(create_app(settings)) as client:
        for _ in range(2):
            assert client.post("/api/auth/refresh").status_code == 401
        assert client.post("/api/auth/refresh").status_code == 429

        for _ in range(2):
            response = client.post(
                "/api/auth/login",
                json={"token": "incorrect-token"},
            )
            assert response.status_code == 401
        assert (
            client.post(
                "/api/auth/login",
                json={"token": "incorrect-token"},
            ).status_code
            == 429
        )


def test_registration_and_login_share_the_interactive_bucket(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token="admin-token-with-more-than-16-characters",
        rate_limit_login_requests=2,
    )
    with TestClient(create_app(settings)) as client:
        for _ in range(2):
            assert client.post("/api/auth/register", json={}).status_code == 422
        blocked = client.post(
            "/api/auth/login",
            json={"token": "incorrect-token"},
        )
        assert blocked.status_code == 429
        assert blocked.headers["X-RateLimit-Limit"] == "2"


def test_invalid_rate_limit_settings_fail_fast(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="RATE_LIMIT_MAX_BUCKETS"):
        validate_runtime_settings(Settings(data_dir=tmp_path, rate_limit_max_buckets=0))
