"""Tests for AWS Bedrock provider stream mappings."""

from __future__ import annotations

import json as _json
from typing import Any

import httpx
import pytest

from app.providers.base import ChatMessage, ToolSchema
from app.providers.bedrock_provider import BedrockProvider


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


async def test_bedrock_provider_streams_text_and_usage(monkeypatch) -> None:
    events = [
        "data: " + _json.dumps({"contentBlockDelta": {"delta": {"text": "Hel"}}}),
        "data: " + _json.dumps({"contentBlockDelta": {"delta": {"text": "lo"}}}),
        "data: " + _json.dumps({
            "metadata": {
                "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}
            }
        }),
        "data: " + _json.dumps({"messageStop": {}}),
    ]
    provider = BedrockProvider(
        model="anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_key="fake-aws-creds",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
    )

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


async def test_bedrock_provider_sends_messages_and_tools(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        captured["body"] = json
        captured["headers"] = headers
        captured["url"] = url
        return _FakeStreamCM(["data: " + _json.dumps({"messageStop": {}})])

    provider = BedrockProvider(
        model="anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_key="fake-aws-creds",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
    )
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
    assert captured["body"]["modelId"] == "anthropic.claude-3-5-sonnet-20241022-v2:0"
    assert captured["body"]["system"][0]["text"] == "be helpful"
    assert captured["body"]["messages"][0]["role"] == "user"
    assert captured["body"]["messages"][0]["content"][0]["text"] == "hello"
    assert captured["body"]["toolConfig"]["tools"][0]["toolSpec"]["name"] == "search"
    assert "Bearer" not in captured["headers"].get("Authorization", "")  # No Bearer token


async def test_bedrock_provider_surfaces_tool_calls(monkeypatch) -> None:
    events = [
        "data: " + _json.dumps({
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {
                    "toolUse": {
                        "toolUseId": "tooluse_abc",
                        "name": "search",
                    }
                }
            }
        }),
        "data: " + _json.dumps({
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {
                    "toolUse": {
                        "input": '{"q": "test"}'
                    }
                }
            }
        }),
        "data: " + _json.dumps({"messageStop": {}}),
    ]
    provider = BedrockProvider(
        model="anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_key="fake-aws-creds",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
    )

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        return _FakeStreamCM(events)

    monkeypatch.setattr(provider._client, "stream", fake_stream)

    result = await provider.chat([ChatMessage(role="user", content="find x")])
    assert result.tool_calls
    assert result.tool_calls[0].name == "search"
    assert _json.loads(result.tool_calls[0].arguments) == {"q": "test"}


def test_bedrock_provider_registered() -> None:
    from app.providers import PROVIDERS

    assert "bedrock" in PROVIDERS


async def test_bedrock_provider_requires_base_url(monkeypatch) -> None:
    provider = BedrockProvider(
        model="anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_key="fake-aws-creds",
    )

    with pytest.raises(RuntimeError, match="requires base_url"):
        _ = [c async for c in provider.stream_chat([ChatMessage(role="user", content="hi")])]


async def test_bedrock_provider_custom_base_url(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_stream(method, url, *, headers=None, json=None):  # noqa: ANN001
        captured["url"] = url
        return _FakeStreamCM(["data: " + _json.dumps({"messageStop": {}})])

    provider = BedrockProvider(
        model="test-model",
        api_key="fake-aws-creds",
        base_url="https://bedrock-runtime.eu-west-1.amazonaws.com",
    )
    monkeypatch.setattr(provider._client, "stream", fake_stream)

    _ = [c async for c in provider.stream_chat([ChatMessage(role="user", content="hi")])]
    assert captured["url"] == "https://bedrock-runtime.eu-west-1.amazonaws.com/model/test-model/converse-stream"
