"""Cohere chat provider supporting Command R and Command R+ models."""

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


def _message_to_cohere(msg: ChatMessage) -> dict[str, Any]:
    """Convert internal ChatMessage to Cohere API format."""
    item: dict[str, Any] = {"role": msg.role}

    # Cohere uses different role names
    if msg.role == "assistant":
        item["role"] = "chatbot"
    elif msg.role == "tool":
        # Tool results in Cohere use a specific format
        return {
            "role": "tool",
            "tool_results": [
                {
                    "call": {"name": msg.name, "parameters": {}},
                    "outputs": [{"text": msg.content or ""}],
                }
            ],
        }

    item["message"] = msg.content or ""

    # Tool calls in assistant messages
    if msg.tool_calls:
        item["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": tc.arguments,
                },
            }
            for tc in msg.tool_calls
        ]

    return item


def _tools_to_cohere(tools: Sequence[ToolSchema]) -> list[dict[str, Any]]:
    """Convert internal ToolSchema to Cohere API format."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "parameter_definitions": (
                t.parameters.get("properties", {}) if t.parameters else {}
            ),
        }
        for t in tools
    ]


@register_provider("cohere")
class CohereProvider(BaseChatProvider):
    """HTTP streaming client for Cohere API.

    Supports:
    - Command R+ (command-r-plus)
    - Command R (command-r)
    - Command (command)
    - Command Light (command-light)

    API docs: https://docs.cohere.com/reference/chat
    """

    default_capabilities = ProviderCapabilities(
        stream=True,
        tools=True,
        json_mode=False,  # Cohere doesn't have explicit JSON mode
        reasoning=False,
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
        prompt_price_per_million_usd: str | None = None,
        completion_price_per_million_usd: str | None = None,
        pricing_version: str | None = None,
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=(base_url or "https://api.cohere.com").rstrip("/"),
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
        """Stream chat completions from Cohere API."""
        # Cohere requires separating system message and chat history
        system_msg = ""
        chat_history: list[dict[str, Any]] = []
        user_message = ""

        for i, msg in enumerate(messages):
            if msg.role == "system":
                system_msg = msg.content or ""
            elif msg.role == "user":
                # Last user message becomes the main message, others go to history
                if i == len(messages) - 1:
                    user_message = msg.content or ""
                else:
                    chat_history.append(_message_to_cohere(msg))
            else:
                chat_history.append(_message_to_cohere(msg))

        body: dict[str, Any] = {
            "model": self.model,
            "message": user_message,
            "stream": True,
            **self.default_params,
            **params,
        }

        if system_msg:
            body["preamble"] = system_msg

        if chat_history:
            body["chat_history"] = chat_history

        if tools:
            body["tools"] = _tools_to_cohere(tools)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/v1/chat"

        try:
            async with self._client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    err_text = (await resp.aread()).decode(errors="replace")
                    raise RuntimeError(f"Cohere API HTTP {resp.status_code}: {err_text[:500]}")

                async for line in resp.aiter_lines():
                    if not line:
                        continue

                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        logger.debug("skip non-json line: %s", line[:80])
                        continue

                    event_type = payload.get("event_type")

                    # Text content
                    if event_type == "text-generation":
                        text = payload.get("text", "")
                        if text:
                            yield StreamChunk(type="text", text=text)

                    # Tool calls
                    elif event_type == "tool-calls-generation":
                        tool_calls = payload.get("tool_calls", [])
                        for idx, tc in enumerate(tool_calls):
                            yield StreamChunk(
                                type="tool_call_delta",
                                tool_call=ToolCallDelta(
                                    index=idx,
                                    id=tc.get("id"),
                                    name=tc.get("name"),
                                    arguments_delta=json.dumps(tc.get("parameters", {})),
                                ),
                            )

                    # Usage information
                    elif event_type == "stream-end":
                        meta = payload.get("response", {}).get("meta", {})
                        tokens = meta.get("tokens", {})
                        if tokens:
                            yield StreamChunk(
                                type="usage",
                                usage=Usage(
                                    prompt_tokens=int(tokens.get("input_tokens") or 0),
                                    completion_tokens=int(tokens.get("output_tokens") or 0),
                                    total_tokens=int(
                                        (tokens.get("input_tokens") or 0)
                                        + (tokens.get("output_tokens") or 0)
                                    ),
                                ),
                            )
                        yield StreamChunk(type="done")
                        return

                yield StreamChunk(type="done")

        except httpx.HTTPError as exc:
            raise RuntimeError(f"Cohere API request failed: {exc}") from exc
