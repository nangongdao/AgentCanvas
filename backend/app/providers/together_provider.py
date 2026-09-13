"""Together AI provider supporting open-source models."""

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


def _message_to_together(msg: ChatMessage) -> dict[str, Any]:
    """Convert internal ChatMessage to Together API format (OpenAI-compatible)."""
    item: dict[str, Any] = {"role": msg.role, "content": msg.content or ""}

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

    # Tool results
    if msg.role == "tool":
        item["tool_call_id"] = msg.tool_call_id
        item["name"] = msg.name

    return item


def _tools_to_together(tools: Sequence[ToolSchema]) -> list[dict[str, Any]]:
    """Convert internal ToolSchema to Together API format (OpenAI-compatible)."""
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


@register_provider("together")
class TogetherProvider(BaseChatProvider):
    """HTTP streaming client for Together AI API.

    Supports hundreds of open-source models including:
    - Meta Llama (llama-3.1-405b, llama-3.1-70b, llama-3.1-8b)
    - Mistral/Mixtral (mixtral-8x22b, mixtral-8x7b)
    - Qwen (qwen-2.5-72b, qwen-2.5-7b)
    - DeepSeek (deepseek-llm-67b)

    API is OpenAI-compatible.
    API docs: https://docs.together.ai/docs/chat-overview
    """

    default_capabilities = ProviderCapabilities(
        stream=True,
        tools=True,
        json_mode=True,
        reasoning=False,
        usage=True,
        cost=True,
    )

    def prepare_params(
        self,
        required_capabilities: Collection[CapabilityName],
        params: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        prepared = super().prepare_params(required_capabilities, params)
        # Together's JSON mode uses response_format
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
            base_url=(base_url or "https://api.together.xyz").rstrip("/"),
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
        """Stream chat completions from Together AI API."""
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [_message_to_together(m) for m in messages],
            "stream": True,
            **self.default_params,
            **params,
        }

        if tools:
            body["tools"] = _tools_to_together(tools)
            body.setdefault("tool_choice", "auto")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/v1/chat/completions"

        try:
            async with self._client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    err_text = (await resp.aread()).decode(errors="replace")
                    raise RuntimeError(f"Together API HTTP {resp.status_code}: {err_text[:500]}")

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

                    # Usage information
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
            raise RuntimeError(f"Together API request failed: {exc}") from exc
