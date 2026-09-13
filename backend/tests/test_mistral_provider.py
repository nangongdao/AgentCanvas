"""Tests for Mistral AI provider stream mappings."""

from __future__ import annotations

import json as _json
from typing import Any

import httpx
import pytest

from app.providers.base import ChatMessage, ToolCall, ToolSchema
from app.providers.mistral_provider import MistralProvider


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


def _make_mistral_sse(events: list[dict[str, Any]]) -> list[str]:
    return [f"data: {_json.dumps(ev)}" for ev in events]


async def test_mistral_provider_streams_text_and_usage(monkeypatch) -> None:
    events: list[dict[str, Any]] = [
        {
            "choices": [{"delta": {"content": "Hel"}}],
        },
        {
            "choices": [{"delta": {"content": "lo"}}],
        },
        {
            "choices": [{"delta": {}}],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 2,
                "total_tokens": 7,
            },
        },
    ]
    lines = _make_mistral_sse(events) + ["data: [DONE]"]
    provider = MistralProvider(model="mistral-large-latest", api_key="test-key")

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        return _FakeStreamCM(lines)

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


async def test_mistral_provider_sends_tools(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        captured["body"] = json
        captured["headers"] = headers
        captured["url"] = url
        return _FakeStreamCM(_make_mistral_sse([{"choices": [{"delta": {}}]}]) + ["data: [DONE]"])

    provider = MistralProvider(model="mistral-large-latest", api_key="test-key")
    monkeypatch.setattr(provider._client, "stream", fake_stream)

    tools = [
        ToolSchema(name="search", description="Search tool", parameters={"type": "object", "properties": {}})
    ]
    _ = [
        c
        async for c in provider.stream_chat(
            [ChatMessage(role="user", content="find x")],
            tools=tools,
        )
    ]
    assert captured["body"]["tools"][0]["function"]["name"] == "search"
    assert captured["body"]["tools"][0]["function"]["description"] == "Search tool"
    assert captured["body"]["tool_choice"] == "auto"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["url"] == "https://api.mistral.ai/v1/chat/completions"


async def test_mistral_provider_surfaces_function_calls(monkeypatch) -> None:
    events: list[dict[str, Any]] = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_123",
                                "function": {"name": "search", "arguments": '{"q": "'},
                            }
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"arguments": 'x"}'},
                            }
                        ]
                    }
                }
            ]
        },
    ]
    provider = MistralProvider(model="mistral-large-latest", api_key="test-key")

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        return _FakeStreamCM(_make_mistral_sse(events) + ["data: [DONE]"])

    monkeypatch.setattr(provider._client, "stream", fake_stream)

    result = await provider.chat([ChatMessage(role="user", content="find x")])
    assert result.tool_calls
    assert result.tool_calls[0].name == "search"
    assert _json.loads(result.tool_calls[0].arguments) == {"q": "x"}


async def test_mistral_provider_maps_tool_result_message(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        captured["body"] = json
        return _FakeStreamCM(_make_mistral_sse([{"choices": [{"delta": {}}]}]) + ["data: [DONE]"])

    provider = MistralProvider(model="mistral-large-latest", api_key="test-key")
    monkeypatch.setattr(provider._client, "stream", fake_stream)

    _ = [
        c
        async for c in provider.stream_chat(
            [
                ChatMessage(role="user", content="find x"),
                ChatMessage(
                    role="assistant",
                    tool_calls=(ToolCall(id="c1", name="search", arguments='{"q": "x"}'),),
                ),
                ChatMessage(role="tool", name="search", content="42", tool_call_id="c1"),
            ]
        )
    ]
    messages = captured["body"]["messages"]
    # Assistant message with tool calls
    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"][0]["function"]["name"] == "search"
    assert messages[1]["tool_calls"][0]["id"] == "c1"
    # Tool result message
    assert messages[2]["role"] == "tool"
    assert messages[2]["content"] == "42"
    assert messages[2]["name"] == "search"
    assert messages[2]["tool_call_id"] == "c1"


async def test_mistral_provider_json_mode_parameter(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        captured["body"] = json
        return _FakeStreamCM(_make_mistral_sse([{"choices": [{"delta": {}}]}]) + ["data: [DONE]"])

    provider = MistralProvider(model="mistral-large-latest", api_key="test-key")
    monkeypatch.setattr(provider._client, "stream", fake_stream)

    params = provider.prepare_params(
        frozenset({"stream", "json_mode"}),
        {"temperature": 0.2},
    )
    _ = [
        c
        async for c in provider.stream_chat(
            [ChatMessage(role="user", content="respond with json")],
            **params,
        )
    ]
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["temperature"] == 0.2


def test_mistral_provider_registered() -> None:
    from app.providers import PROVIDERS

    assert "mistral" in PROVIDERS


async def test_mistral_provider_handles_sse_comments(monkeypatch) -> None:
    """Test that SSE comments are properly ignored."""
    lines = [
        ": keep-alive comment",
        "data: " + _json.dumps({"choices": [{"delta": {"content": "test"}}]}),
        ": another comment",
        "data: [DONE]",
    ]
    provider = MistralProvider(model="mistral-large-latest", api_key="test-key")

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        return _FakeStreamCM(lines)

    monkeypatch.setattr(provider._client, "stream", fake_stream)

    chunks = [c async for c in provider.stream_chat([ChatMessage(role="user", content="hi")])]
    text = "".join(c.text for c in chunks if c.type == "text")
    assert text == "test"


async def test_mistral_provider_custom_base_url(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        captured["url"] = url
        return _FakeStreamCM(_make_mistral_sse([{"choices": [{"delta": {}}]}]) + ["data: [DONE]"])

    provider = MistralProvider(
        model="mistral-large-latest",
        api_key="test-key",
        base_url="https://custom.api.example.com/v1",
    )
    monkeypatch.setattr(provider._client, "stream", fake_stream)

    _ = [c async for c in provider.stream_chat([ChatMessage(role="user", content="hi")])]
    assert captured["url"] == "https://custom.api.example.com/v1/chat/completions"
