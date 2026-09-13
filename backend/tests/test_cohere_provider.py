"""Tests for Cohere provider stream mappings."""

from __future__ import annotations

import json as _json
from typing import Any

import httpx
import pytest

from app.providers.base import ChatMessage, ToolCall, ToolSchema
from app.providers.cohere_provider import CohereProvider


class _FakeStreamCM:
    """Async context manager that yields a scripted httpx.Response."""

    def __init__(self, lines: list[str]) -> None:
        encoded = ("\n".join(lines) + "\n").encode()

        class _ByteStream(httpx.AsyncByteStream):
            async def __aiter__(self):  # type: ignore[override]
                for byte_line in encoded.splitlines(keepends=True):
                    yield byte_line

            async def aclose(self) -> None:  # type: ignore[override]
                return None

        self._response = httpx.Response(200, stream=_ByteStream())

    async def __aenter__(self) -> httpx.Response:
        return self._response

    async def __aexit__(self, *_exc: object) -> None:
        return None


async def test_cohere_provider_streams_text_and_usage(monkeypatch) -> None:
    events = [
        _json.dumps({"event_type": "text-generation", "text": "Hel"}),
        _json.dumps({"event_type": "text-generation", "text": "lo"}),
        _json.dumps({
            "event_type": "stream-end",
            "response": {
                "meta": {
                    "tokens": {
                        "input_tokens": 5,
                        "output_tokens": 2,
                    }
                }
            }
        }),
    ]
    provider = CohereProvider(model="command-r-plus", api_key="test-key")

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        return _FakeStreamCM(events)

    monkeypatch.setattr(provider._client, "stream", fake_stream)

    chunks = [c async for c in provider.stream_chat([ChatMessage(role="user", content="hi")])]
    text = "".join(c.text for c in chunks if c.type == "text")
    assert text == "Hello"
    usage_chunks = [c for c in chunks if c.type == "usage"]
    assert usage_chunks, "expected at least one usage chunk"
    assert usage_chunks[-1].usage is not None
    assert usage_chunks[-1].usage.prompt_tokens == 5
    assert usage_chunks[-1].usage.completion_tokens == 2
    assert usage_chunks[-1].usage.total_tokens == 7


async def test_cohere_provider_sends_system_message_as_preamble(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        captured["body"] = json
        captured["headers"] = headers
        captured["url"] = url
        return _FakeStreamCM([_json.dumps({"event_type": "stream-end", "response": {"meta": {}}})])

    provider = CohereProvider(model="command-r-plus", api_key="test-key")
    monkeypatch.setattr(provider._client, "stream", fake_stream)

    _ = [
        c
        async for c in provider.stream_chat(
            [
                ChatMessage(role="system", content="be brief"),
                ChatMessage(role="user", content="hello"),
            ]
        )
    ]
    assert captured["body"]["preamble"] == "be brief"
    assert captured["body"]["message"] == "hello"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["url"] == "https://api.cohere.com/v1/chat"


async def test_cohere_provider_sends_tools(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        captured["body"] = json
        return _FakeStreamCM([_json.dumps({"event_type": "stream-end", "response": {"meta": {}}})])

    provider = CohereProvider(model="command-r-plus", api_key="test-key")
    monkeypatch.setattr(provider._client, "stream", fake_stream)

    tools = [
        ToolSchema(
            name="search",
            description="Search tool",
            parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        )
    ]
    _ = [
        c
        async for c in provider.stream_chat(
            [ChatMessage(role="user", content="find x")],
            tools=tools,
        )
    ]
    assert captured["body"]["tools"][0]["name"] == "search"
    assert captured["body"]["tools"][0]["description"] == "Search tool"
    assert "q" in captured["body"]["tools"][0]["parameter_definitions"]


async def test_cohere_provider_surfaces_tool_calls(monkeypatch) -> None:
    events = [
        _json.dumps({
            "event_type": "tool-calls-generation",
            "tool_calls": [
                {
                    "id": "call_123",
                    "name": "search",
                    "parameters": {"q": "x"},
                }
            ]
        }),
        _json.dumps({"event_type": "stream-end", "response": {"meta": {}}}),
    ]
    provider = CohereProvider(model="command-r-plus", api_key="test-key")

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        return _FakeStreamCM(events)

    monkeypatch.setattr(provider._client, "stream", fake_stream)

    result = await provider.chat([ChatMessage(role="user", content="find x")])
    assert result.tool_calls
    assert result.tool_calls[0].name == "search"
    assert _json.loads(result.tool_calls[0].arguments) == {"q": "x"}


async def test_cohere_provider_maps_chat_history(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        captured["body"] = json
        return _FakeStreamCM([_json.dumps({"event_type": "stream-end", "response": {"meta": {}}})])

    provider = CohereProvider(model="command-r-plus", api_key="test-key")
    monkeypatch.setattr(provider._client, "stream", fake_stream)

    _ = [
        c
        async for c in provider.stream_chat(
            [
                ChatMessage(role="user", content="first question"),
                ChatMessage(role="assistant", content="first answer"),
                ChatMessage(role="user", content="second question"),
            ]
        )
    ]
    # Last user message becomes the main message
    assert captured["body"]["message"] == "second question"
    # Previous messages go to chat_history
    assert len(captured["body"]["chat_history"]) == 2
    assert captured["body"]["chat_history"][0]["role"] == "user"
    assert captured["body"]["chat_history"][0]["message"] == "first question"
    assert captured["body"]["chat_history"][1]["role"] == "chatbot"
    assert captured["body"]["chat_history"][1]["message"] == "first answer"


def test_cohere_provider_registered() -> None:
    from app.providers import PROVIDERS

    assert "cohere" in PROVIDERS


async def test_cohere_provider_custom_base_url(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        captured["url"] = url
        return _FakeStreamCM([_json.dumps({"event_type": "stream-end", "response": {"meta": {}}})])

    provider = CohereProvider(
        model="command-r-plus",
        api_key="test-key",
        base_url="https://custom.api.example.com",
    )
    monkeypatch.setattr(provider._client, "stream", fake_stream)

    _ = [c async for c in provider.stream_chat([ChatMessage(role="user", content="hi")])]
    assert captured["url"] == "https://custom.api.example.com/v1/chat"
