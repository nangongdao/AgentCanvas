"""AWS Bedrock provider supporting Claude, Llama, and other models via AWS."""

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


def _message_to_bedrock(msg: ChatMessage) -> dict[str, Any]:
    """Convert internal ChatMessage to Bedrock Converse API format."""
    item: dict[str, Any] = {"role": msg.role}

    # Bedrock uses 'user' and 'assistant' roles
    if msg.role == "system":
        # System messages handled separately in system parameter
        return {}

    content_list: list[dict[str, Any]] = []

    # Text content
    if msg.content:
        content_list.append({"text": msg.content})

    # Tool use in assistant messages
    if msg.tool_calls:
        for tc in msg.tool_calls:
            content_list.append({
                "toolUse": {
                    "toolUseId": tc.id,
                    "name": tc.name,
                    "input": json.loads(tc.arguments) if tc.arguments else {},
                }
            })

    # Tool results in tool messages
    if msg.role == "tool":
        content_list.append({
            "toolResult": {
                "toolUseId": msg.tool_call_id,
                "content": [{"text": msg.content or ""}],
            }
        })
        item["role"] = "user"  # Bedrock requires tool results in user role

    item["content"] = content_list if content_list else [{"text": ""}]
    return item


def _tools_to_bedrock(tools: Sequence[ToolSchema]) -> list[dict[str, Any]]:
    """Convert internal ToolSchema to Bedrock tool format."""
    return [
        {
            "toolSpec": {
                "name": t.name,
                "description": t.description,
                "inputSchema": {
                    "json": t.parameters or {"type": "object", "properties": {}}
                },
            }
        }
        for t in tools
    ]


@register_provider("bedrock")
class BedrockProvider(BaseChatProvider):
    """HTTP client for AWS Bedrock Converse API.

    Supports models including:
    - Anthropic Claude (anthropic.claude-3-5-sonnet-20241022-v2:0)
    - Meta Llama (meta.llama3-2-90b-instruct-v1:0)
    - Mistral AI (mistral.mistral-large-2407-v1:0)
    - Amazon Titan (amazon.titan-text-premier-v1:0)
    - Cohere Command (cohere.command-r-plus-v1:0)

    API docs: https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html
    """

    default_capabilities = ProviderCapabilities(
        stream=True,
        tools=True,
        json_mode=False,  # Bedrock doesn't have explicit JSON mode
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
            base_url=base_url or "",
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
        """Stream chat completions from AWS Bedrock Converse API.

        Note: This implementation requires AWS SigV4 signing which should be
        handled by the httpx client (via httpx-auth-awssigv4 or similar).
        The api_key field should contain AWS credentials or IAM role ARN.
        """
        # Separate system message from conversation
        system_messages = [m.content for m in messages if m.role == "system" and m.content]
        conversation = [_message_to_bedrock(m) for m in messages if m.role != "system"]
        conversation = [m for m in conversation if m]  # Filter empty dicts

        body: dict[str, Any] = {
            "modelId": self.model,
            "messages": conversation,
            **self.default_params,
            **params,
        }

        if system_messages:
            body["system"] = [{"text": msg} for msg in system_messages]

        if tools:
            body["toolConfig"] = {"tools": _tools_to_bedrock(tools)}

        # Bedrock uses AWS SigV4, not Bearer token
        headers = {"Content-Type": "application/json"}

        if not self.base_url:
            raise RuntimeError(
                "Bedrock provider requires base_url (e.g., "
                "https://bedrock-runtime.us-east-1.amazonaws.com)"
            )

        url = f"{self.base_url}/model/{self.model}/converse-stream"

        try:
            async with self._client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    err_text = (await resp.aread()).decode(errors="replace")
                    raise RuntimeError(f"Bedrock API HTTP {resp.status_code}: {err_text[:500]}")

                async for line in resp.aiter_lines():
                    if not line:
                        continue

                    # Bedrock returns event-stream format
                    if not line.startswith("data:"):
                        continue

                    data = line[5:].strip()
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        logger.debug("skip non-json line: %s", data[:80])
                        continue

                    # Content block delta (text)
                    if "contentBlockDelta" in payload:
                        delta = payload["contentBlockDelta"].get("delta", {})
                        if "text" in delta:
                            yield StreamChunk(type="text", text=delta["text"])

                    # Tool use block
                    if "contentBlockStart" in payload:
                        start = payload["contentBlockStart"].get("start", {})
                        if "toolUse" in start:
                            tool_use = start["toolUse"]
                            yield StreamChunk(
                                type="tool_call_delta",
                                tool_call=ToolCallDelta(
                                    index=payload["contentBlockStart"].get("contentBlockIndex", 0),
                                    id=tool_use.get("toolUseId"),
                                    name=tool_use.get("name"),
                                    arguments_delta="",
                                ),
                            )

                    # Tool use input delta
                    if "contentBlockDelta" in payload:
                        delta = payload["contentBlockDelta"].get("delta", {})
                        if "toolUse" in delta:
                            yield StreamChunk(
                                type="tool_call_delta",
                                tool_call=ToolCallDelta(
                                    index=payload["contentBlockDelta"].get("contentBlockIndex", 0),
                                    id=None,
                                    name=None,
                                    arguments_delta=delta["toolUse"].get("input", ""),
                                ),
                            )

                    # Usage metadata
                    if "metadata" in payload:
                        usage_data = payload["metadata"].get("usage", {})
                        if usage_data:
                            yield StreamChunk(
                                type="usage",
                                usage=Usage(
                                    prompt_tokens=int(usage_data.get("inputTokens", 0)),
                                    completion_tokens=int(usage_data.get("outputTokens", 0)),
                                    total_tokens=int(usage_data.get("totalTokens", 0)),
                                ),
                            )

                    # Stream end
                    if "messageStop" in payload:
                        yield StreamChunk(type="done")
                        return

                yield StreamChunk(type="done")

        except httpx.HTTPError as exc:
            raise RuntimeError(f"Bedrock API request failed: {exc}") from exc
