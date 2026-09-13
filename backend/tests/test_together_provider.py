"""Tests for Together AI provider stream mappings."""

from __future__ import annotations

import json as _json
from typing import Any

import httpx
import pytest

from app.providers.base import ChatMessage, ToolSchema
from app.providers.together_provider import TogetherProvider


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


async def test_together_provider_streams_text_and_usage(monkeypatch) -> None:
    events = [
        "data: " + _json.dumps({"choices": [{"delta": {"content": "Hel"}}]}),
        "data: " + _json.dumps({"choices": [{"delta": {"content": "lo"}}]}),
        "data: " + _json.dumps({
            "choices": [{"delta": {}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        }),
        "data: [DONE]",
    ]
    provider = TogetherProvider(model="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo", api_key="test-key")

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        return _FakeStreamCM(events)

    monkeypatch.setattr(provider._client, "stream", fake_stream)

    chunks = [c async for c in provider.stream_chat([ChatMessage(role="user", content="hi")])]
    text = "".join(c.text for c in chunks if c.type == "text")
    assert text == "Hello"
    usage_chunks = [c for c in chunks if c.type == "usage"]
    assert usage_chunks, "expected at least one usage chunk"
    assert usage_chunks[-1].usage is not None
    assert usage_chunks[-1].usage.prompt_tokens == 10
    assert usage_chunks[-1].usage.completion_tokens == 5
    assert usage_chunks[-1].usage.total_tokens == 15


async def test_together_provider_sends_messages_and_tools(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        captured["body"] = json
        captured["headers"] = headers
        captured["url"] = url
        return _FakeStreamCM(["data: [DONE]"])

    provider = TogetherProvider(model="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo", api_key="test-key")
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
            [
                ChatMessage(role="system", content="be helpful"),
                ChatMessage(role="user", content="hello"),
            ],
            tools=tools,
        )
    ]
    assert captured["body"]["model"] == "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"
    assert captured["body"]["messages"][0]["role"] == "system"
    assert captured["body"]["messages"][0]["content"] == "be helpful"
    assert captured["body"]["messages"][1]["role"] == "user"
    assert captured["body"]["messages"][1]["content"] == "hello"
    assert captured["body"]["tools"][0]["function"]["name"] == "search"
    assert captured["body"]["tool_choice"] == "auto"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["url"] == "https://api.together.xyz/v1/chat/completions"


async def test_together_provider_surfaces_tool_calls(monkeypatch) -> None:
    events = [
        "data: " + _json.dumps({
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_abc",
                        "function": {"name": "search", "arguments": '{"q":'}
                    }]
                }
            }]
        }),
        "data: " + _json.dumps({
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "function": {"arguments": '"test"}'}
                    }]
                }
            }]
        }),
        "data: [DONE]",
    ]
    provider = TogetherProvider(model="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo", api_key="test-key")

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        return _FakeStreamCM(events)

    monkeypatch.setattr(provider._client, "stream", fake_stream)

    result = await provider.chat([ChatMessage(role="user", content="find x")])
    assert result.tool_calls
    assert result.tool_calls[0].name == "search"
    assert _json.loads(result.tool_calls[0].arguments) == {"q": "test"}


def test_together_provider_registered() -> None:
    from app.providers import PROVIDERS

    assert "together" in PROVIDERS


async def test_together_provider_json_mode(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        captured["body"] = json
        return _FakeStreamCM(["data: [DONE]"])

    provider = TogetherProvider(model="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo", api_key="test-key")
    monkeypatch.setattr(provider._client, "stream", fake_stream)

    _ = [
        c
        async for c in provider.stream_chat(
            [ChatMessage(role="user", content="output json")],
            response_format={"type": "json_object"},
        )
    ]
    assert captured["body"]["response_format"] == {"type": "json_object"}


async def test_together_provider_custom_base_url(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        captured["url"] = url
        return _FakeStreamCM(["data: [DONE]"])

    provider = TogetherProvider(
        model="test-model",
        api_key="test-key",
        base_url="https://custom.together.example.com",
    )
    monkeypatch.setattr(provider._client, "stream", fake_stream)

    _ = [c async for c in provider.stream_chat([ChatMessage(role="user", content="hi")])]
    assert captured["url"] == "https://custom.together.example.com/v1/chat/completions"
