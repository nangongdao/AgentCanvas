"""Mistral AI chat provider supporting Mixtral and Mistral Large models."""

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


def _message_to_mistral(msg: ChatMessage) -> dict[str, Any]:
    """Convert internal ChatMessage to Mistral API format."""
    item: dict[str, Any] = {"role": msg.role}

    # Mistral uses 'content' field for text and tool results
    if msg.role == "tool":
        # Tool results use a specific format
        item["content"] = msg.content or ""
        if msg.name:
            item["name"] = msg.name
        if msg.tool_call_id:
            item["tool_call_id"] = msg.tool_call_id
    else:
        item["content"] = msg.content or ""

    # Tool calls in assistant messages
    if msg.tool_calls:
        item["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for tc in msg.tool_calls
        ]

    return item


def _tools_to_mistral(tools: Sequence[ToolSchema]) -> list[dict[str, Any]]:
    """Convert internal ToolSchema to Mistral API format."""
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


@register_provider("mistral")
class MistralProvider(BaseChatProvider):
    """HTTP streaming client for Mistral AI API.

    Supports:
    - Mistral Large (mistral-large-latest)
    - Mixtral 8x7B (mixtral-8x7b-instruct)
    - Mixtral 8x22B (mixtral-8x22b-instruct)
    - Mistral Small (mistral-small-latest)
    - Mistral Medium (mistral-medium-latest)

    API docs: https://docs.mistral.ai/api/
    """

    default_capabilities = ProviderCapabilities(
        stream=True,
        tools=True,
        json_mode=True,
        reasoning=False,  # Mistral doesn't expose reasoning tokens
        usage=True,
        cost=True,
    )

    def prepare_params(
        self,
        required_capabilities: Collection[CapabilityName],
        params: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        prepared = super().prepare_params(required_capabilities, params)
        # Mistral's JSON mode uses response_format
        if "json_mode" in required_capabilities:
            prepared.setdefault("response_format", {"type": "json_object"})
        return prepared

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        base_url: str | None = None,
        default_params: dict[str, Any] | None = None,
        timeout: float = 120.0,
        client: httpx.AsyncClient | None = None,
        prompt_price_per_million_usd: str | None = None,
        completion_price_per_million_usd: str | None = None,
        pricing_version: str | None = None,
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=(base_url or "https://api.mistral.ai/v1").rstrip("/"),
            default_params=default_params,
            prompt_price_per_million_usd=prompt_price_per_million_usd,
            completion_price_per_million_usd=completion_price_per_million_usd,
            pricing_version=pricing_version,
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
        """Stream chat completions from Mistral AI API."""
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [_message_to_mistral(m) for m in messages],
            "stream": True,
            **self.default_params,
            **params,
        }

        if tools:
            body["tools"] = _tools_to_mistral(tools)
            body.setdefault("tool_choice", "auto")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"

        try:
            async with self._client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    err_text = (await resp.aread()).decode(errors="replace")
                    raise RuntimeError(f"Mistral API HTTP {resp.status_code}: {err_text[:500]}")

                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if line.startswith(":"):  # SSE comment
                        continue
                    if not line.startswith("data:"):
                        continue

                    data = line[5:].strip()
                    if data == "[DONE]":
                        yield StreamChunk(type="done")
                        return

                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        logger.debug("skip non-json sse line: %s", data[:80])
                        continue

                    # Mistral returns usage in the final chunk
                    usage_raw = payload.get("usage")
                    if usage_raw:
                        yield StreamChunk(
                            type="usage",
                            usage=Usage(
                                prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
                                completion_tokens=int(usage_raw.get("completion_tokens") or 0),
                                total_tokens=int(usage_raw.get("total_tokens") or 0),
                            ),
                        )

                    choices = payload.get("choices") or []
                    if not choices:
                        continue

                    delta = choices[0].get("delta") or {}

                    # Text content
                    if delta.get("content"):
                        yield StreamChunk(type="text", text=delta["content"])

                    # Tool calls
                    for tc in delta.get("tool_calls") or []:
                        fn = tc.get("function") or {}
                        yield StreamChunk(
                            type="tool_call_delta",
                            tool_call=ToolCallDelta(
                                index=int(tc.get("index") or 0),
                                id=tc.get("id"),
                                name=fn.get("name"),
                                arguments_delta=fn.get("arguments") or "",
                            ),
                        )

                yield StreamChunk(type="done")

        except httpx.HTTPError as exc:
            raise RuntimeError(f"Mistral API request failed: {exc}") from exc
