"""Tests for the anthropic and ollama provider stream mappings."""

from __future__ import annotations

import json as _json
from typing import Any

import httpx
import pytest

from app.core.provider_capabilities import ProviderCapabilities
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import ChatMessage, ToolSchema
from app.providers.mock_provider import MockChatProvider
from app.providers.ollama_provider import OllamaProvider
from app.providers.openai_provider import OpenAICompatProvider


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


def _make_anthropic_sse(events: list[dict[str, Any]]) -> list[str]:
    return [f"data: {_json.dumps(ev)}" for ev in events]


async def test_anthropic_provider_streams_text_and_usage(monkeypatch) -> None:
    events: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 5, "output_tokens": 0}},
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo"}},
        {"type": "message_delta", "usage": {"output_tokens": 2}},
    ]
    lines = _make_anthropic_sse(events)
    provider = AnthropicProvider(model="claude-test", api_key="sk-test")

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        return _FakeStreamCM(lines)

    monkeypatch.setattr(provider._client, "stream", fake_stream)

    chunks = [c async for c in provider.stream_chat([ChatMessage(role="user", content="hi")])]
    text = "".join(c.text for c in chunks if c.type == "text")
    assert text == "Hello"
    usage_chunks = [c for c in chunks if c.type == "usage"]
    assert usage_chunks, "expected at least one usage chunk"
    assert usage_chunks[-1].usage is not None
    assert usage_chunks[-1].usage.completion_tokens == 2


async def test_anthropic_provider_system_prompt_hoisted(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        captured["body"] = json
        return _FakeStreamCM(
            _make_anthropic_sse(
                [
                    {
                        "type": "message_start",
                        "message": {"usage": {"input_tokens": 1, "output_tokens": 0}},
                    },
                    {"type": "message_delta", "usage": {"output_tokens": 1}},
                ]
            )
        )

    provider = AnthropicProvider(model="claude-test", api_key="sk-test")
    monkeypatch.setattr(provider._client, "stream", fake_stream)

    _ = [
        c
        async for c in provider.stream_chat(
            [
                ChatMessage(role="system", content="be nice"),
                ChatMessage(role="user", content="hi"),
            ]
        )
    ]
    assert captured["body"]["system"] == "be nice"
    assert all(m["role"] != "system" for m in captured["body"]["messages"])


async def test_ollama_provider_streams_text_and_usage(monkeypatch) -> None:
    lines = [
        _json.dumps({"message": {"role": "assistant", "content": "Hi "}, "done": False}),
        _json.dumps({"message": {"role": "assistant", "content": "there"}, "done": False}),
        _json.dumps({"done": True, "prompt_eval_count": 3, "eval_count": 2}),
    ]
    provider = OllamaProvider(model="llama3", api_key="")

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        return _FakeStreamCM(lines)

    monkeypatch.setattr(provider._client, "stream", fake_stream)

    chunks = [c async for c in provider.stream_chat([ChatMessage(role="user", content="hi")])]
    text = "".join(c.text for c in chunks if c.type == "text")
    assert text == "Hi there"
    usage = [c for c in chunks if c.type == "usage"]
    assert usage and usage[-1].usage is not None
    assert usage[-1].usage.total_tokens == 5


async def test_ollama_provider_sends_system_and_tools(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        captured["body"] = json
        return _FakeStreamCM(
            [
                _json.dumps({"done": True, "prompt_eval_count": 1, "eval_count": 1}),
            ]
        )

    provider = OllamaProvider(model="llama3", api_key="")
    monkeypatch.setattr(provider._client, "stream", fake_stream)

    tools = [
        ToolSchema(name="search", description="d", parameters={"type": "object", "properties": {}})
    ]
    _ = [
        c
        async for c in provider.stream_chat(
            [
                ChatMessage(role="system", content="be brief"),
                ChatMessage(role="user", content="find x"),
            ],
            tools=tools,
        )
    ]
    assert captured["body"]["system"] == "be brief"
    assert captured["body"]["tools"][0]["function"]["name"] == "search"


@pytest.mark.parametrize("provider_name", ["anthropic", "ollama"])
def test_providers_registered(provider_name: str) -> None:
    from app.providers import PROVIDERS

    assert provider_name in PROVIDERS


async def test_provider_owned_json_mode_parameter_shaping() -> None:
    openai = OpenAICompatProvider(model="gpt-test", api_key="test")
    ollama = OllamaProvider(model="llama-test")
    demo = MockChatProvider(model="mock", delay=0)
    demo.capabilities = ProviderCapabilities(stream=True, json_mode=True, usage=True)
    try:
        assert openai.prepare_params(
            frozenset({"stream", "json_mode"}),
            {"temperature": 0.2, "json_mode": True},
        ) == {
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        assert ollama.prepare_params(frozenset({"stream", "json_mode"}), {"temperature": 0.2}) == {
            "temperature": 0.2,
            "format": "json",
        }
        params = demo.prepare_params(frozenset({"stream", "json_mode"}), {"json_mode": True})
        chunks = [
            chunk
            async for chunk in demo.stream_chat(
                [ChatMessage(role="user", content="route")], **params
            )
        ]
        assert "".join(chunk.text for chunk in chunks if chunk.type == "text") == (
            '{"next":"FINISH","reason":"mock response"}'
        )
    finally:
        await openai.aclose()
        await ollama.aclose()


def test_prepare_params_rejects_narrowed_required_capability() -> None:
    provider = MockChatProvider(model="mock", delay=0)
    with pytest.raises(RuntimeError, match="lacks required capabilities: tools"):
        provider.prepare_params(frozenset({"stream", "tools"}), {})
