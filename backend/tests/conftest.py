"""Shared test fixtures: fake providers, emitter, compile context."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.engine.nodes.base import CompileContext
from app.providers.base import ChatMessage, ChatResult, StreamChunk, Usage
from app.schemas.events import EventType


class FakeEmitter:
    """Collects emitted events in memory."""

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


class FakeChatProvider:
    """Returns scripted replies in order; loops the last one when exhausted."""

    def __init__(self, replies: list[str] | None = None) -> None:
        self.replies = replies or ["fake reply"]
        self.calls: list[list[ChatMessage]] = []
        self._i = 0

    def _next(self) -> str:
        reply = self.replies[min(self._i, len(self.replies) - 1)]
        self._i += 1
        return reply

    def prepare_params(self, _required, params):  # noqa: ANN001
        return dict(params or {})

    async def stream_chat(
        self, messages: list[ChatMessage], **params: Any
    ) -> AsyncIterator[StreamChunk]:
        self.calls.append(messages)
        reply = self._next()
        yield StreamChunk(type="text", text=reply)
        yield StreamChunk(type="usage", usage=Usage(1, 1, 2))
        yield StreamChunk(type="done")

    async def chat(self, messages: list[ChatMessage], **params: Any) -> ChatResult:
        self.calls.append(messages)
        return ChatResult(content=self._next(), usage=Usage(1, 1, 2))


@pytest.fixture
def fake_emitter() -> FakeEmitter:
    return FakeEmitter()


def make_ctx(
    emitter: FakeEmitter,
    provider: FakeChatProvider | None = None,
    *,
    max_loop_iterations: int = 10,
) -> CompileContext:
    prov = provider or FakeChatProvider()
    return CompileContext(
        execution_id="test-exec",
        emitter=emitter,  # type: ignore[arg-type]
        get_provider=lambda _mid: prov,
        max_loop_iterations=max_loop_iterations,
    )
