"""Agent 节点模型链的显式负载均衡策略测试。

覆盖 ``AgentConfig.load_balance`` 的两种策略:

* ``failover``(默认)——始终从链首开始,仅故障转移;
* ``round_robin``——进程内按调用轮转起点,把请求分散到整条链,
  失败仍在剩余序里按序转移,首 chunk 之后不换流。
"""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from pydantic import ValidationError

from app.engine.nodes import base as base_module
from app.engine.nodes.base import CompileContext
from app.providers.base import BaseChatProvider, ChatMessage, StreamChunk
from app.schemas.dsl import AgentConfig
from app.schemas.events import EventType


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


class _RoutedProvider(BaseChatProvider):
    """Streaming provider that records its own invocation into a shared log."""

    def __init__(
        self,
        provider_id: str,
        chunks: Sequence[StreamChunk | Exception],
        call_log: list[str],
    ) -> None:
        super().__init__(model=provider_id)
        self.provider_id = provider_id
        self.chunks = tuple(chunks)
        self.call_log = call_log
        self.calls = 0

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        **_params: Any,
    ) -> AsyncIterator[StreamChunk]:
        self.calls += 1
        self.call_log.append(self.provider_id)
        for item in self.chunks:
            if isinstance(item, Exception):
                raise item
            yield item


@pytest.fixture(autouse=True)
def _fresh_round_robin_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base_module, "_ROUND_ROBIN_CURSOR", itertools.count())


def _chain(emitter: _Emitter, providers: list[_RoutedProvider]) -> CompileContext:
    return CompileContext(
        execution_id="load-balance-test",
        emitter=emitter,  # type: ignore[arg-type]
        get_provider=lambda _model_id: providers[0],
    )


def _text(provider_id: str) -> list[StreamChunk]:
    return [StreamChunk(type="text", text=provider_id), StreamChunk(type="done")]


@pytest.mark.asyncio
async def test_default_failover_always_starts_at_primary() -> None:
    emitter = _Emitter()
    call_log: list[str] = []
    primary = _RoutedProvider("primary", _text("primary"), call_log)
    backup = _RoutedProvider("backup", _text("backup"), call_log)
    ctx = _chain(emitter, [primary, backup])

    for _ in range(3):
        result = await ctx.model_chat_chain(
            [primary, backup],
            [ChatMessage(role="user", content="hello")],
            required_capabilities={"stream"},
        )
        assert result.content == "primary"

    assert call_log == ["primary", "primary", "primary"]
    assert backup.calls == 0
    assert not emitter.events


@pytest.mark.asyncio
async def test_round_robin_spreads_calls_across_the_chain() -> None:
    emitter = _Emitter()
    call_log: list[str] = []
    a = _RoutedProvider("a", _text("a"), call_log)
    b = _RoutedProvider("b", _text("b"), call_log)
    c = _RoutedProvider("c", _text("c"), call_log)
    ctx = _chain(emitter, [a, b, c])

    served: list[str] = []
    for _ in range(3):
        result = await ctx.model_chat_chain(
            [a, b, c],
            [ChatMessage(role="user", content="hello")],
            required_capabilities={"stream"},
            load_balance="round_robin",
        )
        served.append(result.content)

    assert served == ["a", "b", "c"]
    assert a.calls == b.calls == c.calls == 1
    assert not emitter.events


@pytest.mark.asyncio
async def test_round_robin_still_fails_over_within_the_rotation() -> None:
    emitter = _Emitter()
    call_log: list[str] = []
    a = _RoutedProvider("a", _text("a"), call_log)
    b = _RoutedProvider("b", [TimeoutError("transient")], call_log)
    c = _RoutedProvider("c", _text("c"), call_log)
    ctx = _chain(emitter, [a, b, c])

    first = await ctx.model_chat_chain(
        [a, b, c],
        [ChatMessage(role="user", content="hello")],
        required_capabilities={"stream"},
        load_balance="round_robin",
    )
    assert first.content == "a"

    second = await ctx.model_chat_chain(
        [a, b, c],
        [ChatMessage(role="user", content="hello")],
        required_capabilities={"stream"},
        load_balance="round_robin",
    )
    assert second.content == "c"

    assert call_log == ["a", "b", "c"]
    fallback_events = [
        event for event in emitter.events if event["payload"].get("kind") == "provider_fallback"
    ]
    assert len(fallback_events) == 1
    assert fallback_events[0]["payload"]["from_model"] == "b"
    assert fallback_events[0]["payload"]["to_model"] == "c"
    assert fallback_events[0]["payload"]["reason"] == "TimeoutError"
    assert "transient" not in str(fallback_events)


@pytest.mark.asyncio
async def test_round_robin_stream_rotates_and_keeps_started_streams() -> None:
    emitter = _Emitter()
    call_log: list[str] = []
    a = _RoutedProvider("a", _text("a"), call_log)
    b = _RoutedProvider(
        "b",
        [StreamChunk(type="text", text="b-partial"), TimeoutError("lost")],
        call_log,
    )
    c = _RoutedProvider("c", _text("c"), call_log)
    ctx = _chain(emitter, [a, b, c])

    received: list[str] = []
    async for chunk in ctx.model_stream_chat_chain(
        [a, b, c],
        [ChatMessage(role="user", content="hello")],
        required_capabilities={"stream"},
        load_balance="round_robin",
    ):
        if chunk.type == "text":
            received.append(chunk.text or "")
    assert received == ["a"]

    with pytest.raises(TimeoutError, match="lost"):
        async for chunk in ctx.model_stream_chat_chain(
            [a, b, c],
            [ChatMessage(role="user", content="hello")],
            required_capabilities={"stream"},
            load_balance="round_robin",
        ):
            if chunk.type == "text":
                received.append(chunk.text or "")

    # The second call rotated to ``b``, emitted its first chunk, then failed:
    # the stream is never switched and ``c`` stays untouched.
    assert received == ["a", "b-partial"]
    assert c.calls == 0
    assert not any(event["payload"].get("kind") == "provider_fallback" for event in emitter.events)


def test_agent_config_load_balance_validation() -> None:
    cfg = AgentConfig.model_validate({})
    assert cfg.load_balance == "failover"

    cfg = AgentConfig.model_validate({"load_balance": "round_robin"})
    assert cfg.load_balance == "round_robin"

    with pytest.raises(ValidationError):
        AgentConfig.model_validate({"load_balance": "weighted"})
