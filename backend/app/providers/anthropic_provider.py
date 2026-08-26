"""Anthropic chat provider - native Messages API streaming.

Streams via the Anthropic Messages API (``/v1/messages``) using SSE deltas.
Maps the unified ``ChatMessage``/``ToolSchema`` shapes onto Anthropic's
``messages``/``tools`` request format and converts streamed events into the
shared ``StreamChunk`` protocol consumed by node executors.

Notes
-----
* The system prompt is sent as a top-level ``system`` parameter (Anthropic
  requirement), not as a chat message.
* Tool-call deltas arrive incrementally as ``input_json_delta`` fragments;
  they are accumulated into ``ToolCallDelta`` chunks so the base
  ``chat()`` accumulator can reconstruct full tool calls.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from app.core.provider_capabilities import ProviderCapabilities
from app.providers.base import (
    BaseChatProvider,
    ChatMessage,
    StreamChunk,
    ToolCallDelta,
    ToolSchema,
    Usage,
    register_provider,
)

logger = logging.getLogger(__name__)


def _to_anthropic_message(msg: ChatMessage) -> dict[str, Any] | None:
    """Convert a unified message to the Anthropic messages payload.

    Returns ``None`` for system messages, which are hoisted to the top level.
    """
    if msg.role == "system":
        return None
    if msg.role == "tool":
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id or "",
                    "content": msg.content or "",
                }
            ],
        }
    content: list[dict[str, Any]] | str = msg.content or ""
    if msg.tool_calls:
        blocks: list[dict[str, Any]] = []
        if msg.content:
            blocks.append({"type": "text", "text": msg.content})
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.arguments) if tc.arguments else {}
            except json.JSONDecodeError:
                args = {}
            blocks.append(
                {"type": "tool_use", "id": tc.id, "name": tc.name, "input": args}
            )
        content = blocks
    return {"role": msg.role, "content": content}


def _to_anthropic_tools(tools: Sequence[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.parameters or {"type": "object", "properties": {}},
        }
        for t in tools
    ]


@register_provider("anthropic")
class AnthropicProvider(BaseChatProvider):
    """Native Anthropic Messages API streaming client."""

    default_capabilities = ProviderCapabilities(
        stream=True,
        tools=True,
        usage=True,
        cost=True,
    )

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        base_url: str | None = None,
        default_params: dict[str, Any] | None = None,
        timeout: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=(base_url or "https://api.anthropic.com").rstrip("/"),
            default_params=default_params,
        )
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] = (),
        **params: Any,
    ) -> AsyncIterator[StreamChunk]:
        system_parts = [m.content for m in messages if m.role == "system" and m.content]
        chat_messages = [
            converted
            for converted in (_to_anthropic_message(m) for m in messages)
            if converted is not None
        ]

        body: dict[str, Any] = {
            "model": self.model,
            "messages": chat_messages,
            "stream": True,
            "max_tokens": int(params.pop("max_tokens", self.default_params.get("max_tokens", 1024))),
            **{
                k: v
                for k, v in {**self.default_params, **params}.items()
                if k != "max_tokens"
            },
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if tools:
            body["tools"] = _to_anthropic_tools(tools)

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/v1/messages"

        try:
            async with self._client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    err_text = (await resp.aread()).decode(errors="replace")
                    raise RuntimeError(f"Anthropic HTTP {resp.status_code}: {err_text[:500]}")
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        logger.debug("skip non-json sse line: %s", data[:80])
                        continue
                    for chunk in self._events_from_payload(payload):
                        yield chunk
                yield StreamChunk(type="done")
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Anthropic request failed: {exc}") from exc

    def _events_from_payload(self, payload: dict[str, Any]) -> list[StreamChunk]:
        """Convert one Anthropic SSE event into zero or more StreamChunks."""
        chunks: list[StreamChunk] = []
        event_type = payload.get("type", "")
        if event_type == "message_start":
            usage = (payload.get("message") or {}).get("usage") or {}
            if usage:
                chunks.append(
                    StreamChunk(
                        type="usage",
                        usage=Usage(
                            prompt_tokens=int(usage.get("input_tokens") or 0),
                            completion_tokens=int(usage.get("output_tokens") or 0),
                            total_tokens=int(usage.get("input_tokens") or 0)
                            + int(usage.get("output_tokens") or 0),
                        ),
                    )
                )
        elif event_type == "content_block_delta":
            delta = payload.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                chunks.append(StreamChunk(type="text", text=delta["text"]))
            elif delta.get("type") == "input_json_delta":
                chunks.append(
                    StreamChunk(
                        type="tool_call_delta",
                        tool_call=ToolCallDelta(
                            index=int(payload.get("index", 0)),
                            arguments_delta=delta.get("partial_json", ""),
                        ),
                    )
                )
        elif event_type == "content_block_start":
            block = payload.get("content_block") or {}
            if block.get("type") == "tool_use":
                chunks.append(
                    StreamChunk(
                        type="tool_call_delta",
                        tool_call=ToolCallDelta(
                            index=int(payload.get("index", 0)),
                            id=block.get("id"),
                            name=block.get("name"),
                        ),
                    )
                )
        elif event_type == "message_delta":
            usage = payload.get("usage") or {}
            if usage.get("output_tokens") is not None:
                chunks.append(
                    StreamChunk(
                        type="usage",
                        usage=Usage(
                            prompt_tokens=0,
                            completion_tokens=int(usage.get("output_tokens") or 0),
                            total_tokens=int(usage.get("output_tokens") or 0),
                        ),
                    )
                )
        return chunks


__all__ = ["AnthropicProvider"]
