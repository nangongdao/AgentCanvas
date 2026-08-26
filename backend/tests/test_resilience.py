"""Provider/MCP outbound resilience state-machine and API contract tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from fnmatch import fnmatchcase
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, validate_runtime_settings
from app.core.container import _resilience_redis_url
from app.core.resilience import (
    CircuitOpenError,
    InjectedResilienceFault,
    ResilienceConfig,
    ResilienceRegistry,
    RetryBudgetExceeded,
    is_transient_error,
)
from app.engine.nodes.base import CompileContext
from app.main import create_app
from app.providers.base import BaseChatProvider, ChatMessage, StreamChunk
from app.schemas.events import EventType


def _config(**overrides: Any) -> ResilienceConfig:
    return ResilienceConfig(
        backoff_seconds=0,
        max_backoff_seconds=0,
        **overrides,
    )


class _Emitter:
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


class _ScriptedProvider(BaseChatProvider):
    name = "scripted"

    def __init__(
        self,
        model: str,
        chunks: Sequence[StreamChunk | Exception],
    ) -> None:
        super().__init__(model=model)
        self.chunks = tuple(chunks)
        self.calls = 0

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        **_params: Any,
    ) -> AsyncIterator[StreamChunk]:
        self.calls += 1
        for item in self.chunks:
            if isinstance(item, Exception):
                raise item
            yield item


class _SnapshotRedis:
    def __init__(self, states: dict[str, dict[str, str]]) -> None:
        self.states = states

    async def scan_iter(self, *, match: str) -> AsyncIterator[str]:
        for key in self.states:
            if fnmatchcase(key, match):
                yield key

    async def time(self) -> tuple[int, int]:
        return (100, 0)

    async def hgetall(self, key: str) -> dict[str, str]:
        return self.states.get(key, {})

    async def zremrangebyscore(self, *_args: object) -> int:
        return 0

    async def zcard(self, _key: str) -> int:
        return 0


class _UnavailableSnapshotRedis:
    async def time(self) -> tuple[int, int]:
        raise ConnectionError("redis unavailable")

    async def scan_iter(self, *, match: str) -> AsyncIterator[str]:
        del match
        raise ConnectionError("redis unavailable")
        yield ""  # pragma: no cover - keeps this an async iterator


@pytest.mark.asyncio
async def test_retry_success_records_attempts_and_budget() -> None:
    registry = ResilienceRegistry()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary")
        return "ok"

    assert (
        await registry.execute(
            "provider:mock:test",
            operation,
            config=_config(),
            retryable=is_transient_error,
        )
        == "ok"
    )
    assert calls == 2
    snapshot = registry.snapshot("provider:mock:test")
    assert snapshot is not None
    assert snapshot["state"] == "closed"
    assert snapshot["total_failures"] == 1
    assert snapshot["total_successes"] == 1
    assert snapshot["retry_budget_used"] == 1


@pytest.mark.asyncio
async def test_circuit_opens_and_allows_one_half_open_probe() -> None:
    now = [0.0]
    registry = ResilienceRegistry(clock=lambda: now[0])
    config = _config(max_attempts=1, failure_threshold=2, reset_timeout_seconds=5)

    async def fail() -> None:
        raise TimeoutError("down")

    for _ in range(2):
        with pytest.raises(TimeoutError):
            await registry.execute("mcp:server:list_tools", fail, config=config, retryable=is_transient_error)
    with pytest.raises(CircuitOpenError):
        await registry.execute("mcp:server:list_tools", fail, config=config, retryable=is_transient_error)

    now[0] = 6.0

    async def recover() -> str:
        return "healthy"

    assert (
        await registry.execute(
            "mcp:server:list_tools", recover, config=config, retryable=is_transient_error
        )
        == "healthy"
    )
    assert registry.snapshot("mcp:server:list_tools")["state"] == "closed"  # type: ignore[index]


@pytest.mark.asyncio
async def test_retry_budget_stops_unbounded_retries() -> None:
    registry = ResilienceRegistry()
    config = _config(max_attempts=4, failure_threshold=20, retry_budget=1)
    calls = 0

    async def fail() -> None:
        nonlocal calls
        calls += 1
        raise TimeoutError("temporary")

    with pytest.raises(RetryBudgetExceeded):
        await registry.execute("provider:mock:budget", fail, config=config, retryable=is_transient_error)
    assert calls == 2


@pytest.mark.asyncio
async def test_fault_injection_is_transient_and_does_not_call_operation() -> None:
    registry = ResilienceRegistry()
    config = _config()
    registry.inject_failures("provider:mock:fault", 1)
    calls = 0

    async def recover() -> str:
        nonlocal calls
        calls += 1
        return "recovered"

    assert (
        await registry.execute(
            "provider:mock:fault",
            recover,
            config=config,
            retryable=is_transient_error,
        )
        == "recovered"
    )
    assert calls == 1
    assert registry.snapshot("provider:mock:fault")["injected_failures_remaining"] == 0  # type: ignore[index]
    assert is_transient_error(InjectedResilienceFault("test"))


@pytest.mark.asyncio
async def test_stream_retries_only_before_first_chunk() -> None:
    registry = ResilienceRegistry()
    config = _config()
    attempts = 0

    def before_first_chunk() -> AsyncIterator[str]:
        async def run() -> AsyncIterator[str]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("not connected")
            yield "complete"

        return run()

    assert [
        item
        async for item in registry.stream(
            "provider:mock:stream", before_first_chunk, config=config, retryable=is_transient_error
        )
    ] == ["complete"]
    assert attempts == 2

    attempts = 0

    def after_first_chunk() -> AsyncIterator[str]:
        async def run() -> AsyncIterator[str]:
            nonlocal attempts
            attempts += 1
            yield "partial"
            raise TimeoutError("connection lost")

        return run()

    with pytest.raises(TimeoutError):
        _ = [
            item
            async for item in registry.stream(
                "provider:mock:partial", after_first_chunk, config=config, retryable=is_transient_error
            )
        ]
    assert attempts == 1


@pytest.mark.asyncio
async def test_shared_snapshots_preserve_prefix_filter_semantics() -> None:
    state_prefix = "agentcanvas:resilience:state:"
    redis = _SnapshotRedis(
        {
            f"{state_prefix}provider:mock:chat": {
                "state": "closed",
                "retry_budget_limit": "32",
                "retry_window_ms": "60000",
            },
            f"{state_prefix}mcp:server:list_tools": {
                "state": "open",
                "opened_at": "1",
                "reset_timeout_ms": "30000",
                "retry_budget_limit": "32",
                "retry_window_ms": "60000",
            },
        }
    )
    registry = ResilienceRegistry(redis_client=redis)

    resources = await registry.snapshots_async(prefix="provider:")

    assert [resource["key"] for resource in resources] == ["provider:mock:chat"]


def test_only_horizontal_roles_enable_shared_resilience(tmp_path) -> None:
    local = Settings(data_dir=tmp_path, redis_url="redis://127.0.0.1:6379/0")
    horizontal = Settings(
        data_dir=tmp_path,
        process_role="api",
        database_url="postgresql+asyncpg://agentcanvas:test@postgres/agentcanvas",
        redis_url="redis://redis:6379/0",
        startup_migrations=False,
    )

    assert _resilience_redis_url(local) == ""  # noqa: SLF001
    assert _resilience_redis_url(horizontal) == horizontal.redis_url  # noqa: SLF001


def test_half_open_probe_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError, match="half_open_probe_timeout_seconds"):
        ResilienceConfig(half_open_probe_timeout_seconds=0)


@pytest.mark.asyncio
async def test_compile_context_falls_back_and_emits_bounded_event() -> None:
    emitter = _Emitter()
    primary = _ScriptedProvider("primary", [TimeoutError("secret upstream details")])
    fallback = _ScriptedProvider(
        "fallback",
        [StreamChunk(type="text", text="recovered"), StreamChunk(type="done")],
    )
    ctx = CompileContext(
        execution_id="resilience-test",
        emitter=emitter,  # type: ignore[arg-type]
        get_provider=lambda _model_id: primary,
        resilience=ResilienceRegistry(),
        resilience_config=_config(max_attempts=1),
    )

    result = await ctx.model_chat_chain(
        [primary, fallback],
        [ChatMessage(role="user", content="hello")],
        required_capabilities={"stream"},
        node_id="agent",
    )
    assert result.content == "recovered"
    assert primary.calls == fallback.calls == 1
    fallback_events = [
        event for event in emitter.events if event["payload"].get("kind") == "provider_fallback"
    ]
    assert fallback_events == [
        {
            "type": EventType.NODE_STREAMING,
            "node_id": "agent",
            "payload": {
                "kind": "provider_fallback",
                "from_provider": "scripted",
                "from_model": "primary",
                "to_provider": "scripted",
                "to_model": "fallback",
                "reason": "TimeoutError",
            },
        }
    ]
    assert "secret upstream details" not in str(fallback_events)


@pytest.mark.asyncio
async def test_compile_context_never_falls_back_after_stream_output() -> None:
    emitter = _Emitter()
    primary = _ScriptedProvider(
        "primary",
        [StreamChunk(type="text", text="partial"), TimeoutError("lost")],
    )
    fallback = _ScriptedProvider(
        "fallback",
        [StreamChunk(type="text", text="duplicate"), StreamChunk(type="done")],
    )
    ctx = CompileContext(
        execution_id="resilience-test",
        emitter=emitter,  # type: ignore[arg-type]
        get_provider=lambda _model_id: primary,
        resilience=ResilienceRegistry(),
        resilience_config=_config(max_attempts=1),
    )

    received: list[str] = []
    with pytest.raises(TimeoutError, match="lost"):
        async for chunk in ctx.model_stream_chat_chain(
            [primary, fallback],
            [ChatMessage(role="user", content="hello")],
            required_capabilities={"stream"},
            node_id="agent",
        ):
            received.append(chunk.text)
    assert received == ["partial"]
    assert primary.calls == 1
    assert fallback.calls == 0
    assert not any(event["payload"].get("kind") == "provider_fallback" for event in emitter.events)


def test_resilience_endpoint_is_read_only_and_secret_free(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs=",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/resilience")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["summary"] == {"total": 0, "open": 0, "half_open": 0}
        assert "api_key" not in response.text


def test_resilience_endpoint_fails_closed_when_shared_backend_is_unavailable(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs=",
    )
    with TestClient(create_app(settings)) as client:
        client.app.state.container.resilience = ResilienceRegistry(  # type: ignore[attr-defined]
            redis_client=_UnavailableSnapshotRedis()
        )

        response = client.get("/api/resilience")

        assert response.status_code == 503
        assert response.json() == {"detail": "shared resilience backend unavailable"}


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("model_resilience_max_attempts", 0, "MODEL_RESILIENCE_MAX_ATTEMPTS"),
        ("mcp_resilience_failure_threshold", 0, "MCP_RESILIENCE_FAILURE_THRESHOLD"),
        ("model_resilience_retry_budget", -1, "MODEL_RESILIENCE_RETRY_BUDGET"),
        ("mcp_resilience_reset_seconds", 0, "MCP_RESILIENCE_RESET_SECONDS"),
        ("resilience_retry_window_seconds", 0, "RESILIENCE_RETRY_WINDOW_SECONDS"),
    ],
)
def test_runtime_settings_reject_invalid_resilience_limits(
    tmp_path,
    field: str,
    value: int,
    message: str,
) -> None:
    settings = replace(Settings(data_dir=tmp_path), **cast(Any, {field: value}))
    with pytest.raises(RuntimeError, match=message):
        validate_runtime_settings(settings)
