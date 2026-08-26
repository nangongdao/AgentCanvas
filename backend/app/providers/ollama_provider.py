"""Ollama chat provider - native /api/chat streaming.

Ollama exposes a native ``/api/chat`` endpoint that streams newline-delimited
JSON objects (one per generated token chunk) and reports token usage in the
final message. This provider maps the unified ``ChatMessage``/``ToolSchema``
shapes onto Ollama's request format and converts each NDJSON line into a
``StreamChunk``.

The provider is registered as ``ollama`` so it can be selected from the model
management UI. Tool calls are surfaced through Ollama's ``tools`` field when
the host Ollama version supports it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Collection, Mapping, Sequence
from typing import Any

import httpx

from app.core.provider_capabilities import CapabilityName, ProviderCapabilities
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


def _to_ollama_message(msg: ChatMessage) -> dict[str, Any] | None:
    if msg.role == "system":
        return None
    return {
        "role": msg.role,
        "content": msg.content or "",
    }


def _to_ollama_tools(tools: Sequence[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


@register_provider("ollama")
class OllamaProvider(BaseChatProvider):
    """Native Ollama /api/chat streaming client."""

    default_capabilities = ProviderCapabilities(
        stream=True,
        tools=True,
        json_mode=True,
        usage=True,
        cost=True,
    )

    def prepare_params(
        self,
        required_capabilities: Collection[CapabilityName],
        params: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        prepared = super().prepare_params(required_capabilities, params)
        if "json_mode" in required_capabilities:
            prepared.setdefault("format", "json")
        return prepared

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        base_url: str | None = None,
        default_params: dict[str, Any] | None = None,
        timeout: float = 300.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=(base_url or "http://127.0.0.1:11434").rstrip("/"),
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
            for converted in (_to_ollama_message(m) for m in messages)
            if converted is not None
        ]

        body: dict[str, Any] = {
            "model": self.model,
            "messages": chat_messages,
            "stream": True,
            **self.default_params,
            **params,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if tools:
            body["tools"] = _to_ollama_tools(tools)

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = f"{self.base_url}/api/chat"

        try:
            async with self._client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    err_text = (await resp.aread()).decode(errors="replace")
                    raise RuntimeError(f"Ollama HTTP {resp.status_code}: {err_text[:500]}")
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        logger.debug("skip non-json ollama line: %s", line[:80])
                        continue
                    for chunk in self._chunks_from_payload(payload):
                        yield chunk
                yield StreamChunk(type="done")
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc

    def _chunks_from_payload(self, payload: dict[str, Any]) -> list[StreamChunk]:
        """Convert one Ollama NDJSON object into zero or more StreamChunks."""
        chunks: list[StreamChunk] = []
        message = payload.get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content:
            chunks.append(StreamChunk(type="text", text=content))

        tool_calls = message.get("tool_calls") or []
        for idx, tc in enumerate(tool_calls):
            fn = tc.get("function") or {}
            arguments = fn.get("arguments")
            args_str = (
                json.dumps(arguments, ensure_ascii=False)
                if isinstance(arguments, dict)
                else str(arguments or "")
            )
            chunks.append(
                StreamChunk(
                    type="tool_call_delta",
                    tool_call=ToolCallDelta(
                        index=idx,
                        id=tc.get("id"),
                        name=fn.get("name"),
                        arguments_delta=args_str,
                    ),
                )
            )

        if payload.get("done"):
            usage = payload
            chunks.append(
                StreamChunk(
                    type="usage",
                    usage=Usage(
                        prompt_tokens=int(usage.get("prompt_eval_count") or 0),
                        completion_tokens=int(usage.get("eval_count") or 0),
                        total_tokens=int(usage.get("prompt_eval_count") or 0)
                        + int(usage.get("eval_count") or 0),
                    ),
                )
            )
        return chunks


__all__ = ["OllamaProvider"]
