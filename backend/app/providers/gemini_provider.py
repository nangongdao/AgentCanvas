"""Google Gemini chat provider - native ``streamGenerateContent`` SSE.

Gemini is the one adapter the OpenAI-compatible client cannot absorb: Google's
Generative Language API has its own request envelope (``contents`` /
``systemInstruction`` / ``generationConfig``), its own auth header
(``x-goog-api-key``) and its own streaming shape. DeepSeek, vLLM, SiliconFlow
and friends stay on the ``openai_compat`` adapter with a custom ``base_url``;
this module exists so a Gemini model config is a first-class choice rather than
an OpenAI shim that silently drops the system prompt.

The provider is registered as ``gemini`` and surfaces itself through
``/api/models`` and ``/api/models/provider-capabilities`` like every other
adapter, so no contract changes accompany it.
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

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"

#: Option names accepted on the unified provider interface, mapped onto the
#: ``generationConfig`` field names Gemini actually expects. Anything not listed
#: is forwarded unchanged, so camelCase-native keys already work.
_GENERATION_CONFIG_ALIASES: dict[str, str] = {
    "temperature": "temperature",
    "top_p": "topP",
    "top_k": "topK",
    "max_tokens": "maxOutputTokens",
    "max_output_tokens": "maxOutputTokens",
    "stop": "stopSequences",
    "candidate_count": "candidateCount",
    "presence_penalty": "presencePenalty",
    "frequency_penalty": "frequencyPenalty",
}


def _message_to_gemini(msg: ChatMessage) -> dict[str, Any] | None:
    """Map one unified message onto a Gemini ``contents`` entry.

    System messages are hoisted into ``systemInstruction`` by the caller, and a
    tool result becomes the ``functionResponse`` part Gemini requires on a user
    turn.
    """
    if msg.role == "system":
        return None
    if msg.role == "tool":
        return {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": msg.name or msg.tool_call_id or "tool",
                        "response": {"content": msg.content or ""},
                    }
                }
            ],
        }

    parts: list[dict[str, Any]] = []
    if msg.content:
        parts.append({"text": msg.content})
    for call in msg.tool_calls:
        try:
            args = json.loads(call.arguments) if call.arguments else {}
        except json.JSONDecodeError:
            logger.debug("skip non-json tool arguments for %s", call.name)
            args = {}
        parts.append({"functionCall": {"name": call.name, "args": args}})
    if not parts:
        parts.append({"text": ""})
    return {"role": "model" if msg.role == "assistant" else "user", "parts": parts}


def _tools_to_gemini(tools: Sequence[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {
            "functionDeclarations": [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters or {"type": "object", "properties": {}},
                }
                for t in tools
            ]
        }
    ]


@register_provider("gemini")
class GeminiProvider(BaseChatProvider):
    """Native Google Generative Language ``streamGenerateContent`` client."""

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
            prepared.setdefault("response_mime_type", "application/json")
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
            base_url=(base_url or DEFAULT_BASE_URL).rstrip("/"),
            default_params=default_params,
        )
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _generation_config(self, params: Mapping[str, Any]) -> dict[str, Any]:
        config: dict[str, Any] = {}
        for key, value in params.items():
            if key == "response_mime_type":
                config["responseMimeType"] = value
                continue
            config[_GENERATION_CONFIG_ALIASES.get(key, key)] = value
        return config

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] = (),
        **params: Any,
    ) -> AsyncIterator[StreamChunk]:
        system_parts = [m.content for m in messages if m.role == "system" and m.content]
        contents = [
            converted
            for converted in (_message_to_gemini(m) for m in messages)
            if converted is not None
        ]

        merged_params = {**self.default_params, **params}
        body: dict[str, Any] = {"contents": contents}
        generation_config = self._generation_config(merged_params)
        if generation_config:
            body["generationConfig"] = generation_config
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        if tools:
            body["tools"] = _tools_to_gemini(tools)

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        url = f"{self.base_url}/v1beta/models/{self.model}:streamGenerateContent?alt=sse"

        try:
            async with self._client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    err_text = (await resp.aread()).decode(errors="replace")
                    raise RuntimeError(f"Gemini HTTP {resp.status_code}: {err_text[:500]}")
                # usageMetadata is cumulative and repeats across chunks, so the
                # last one is kept and emitted once rather than summed.
                latest_usage: Usage | None = None
                async for line in resp.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        logger.debug("skip non-json gemini sse line: %s", data[:80])
                        continue
                    usage, chunks = self._chunks_from_payload(payload)
                    if usage is not None:
                        latest_usage = usage
                    for chunk in chunks:
                        yield chunk
                if latest_usage is not None:
                    yield StreamChunk(type="usage", usage=latest_usage)
                yield StreamChunk(type="done")
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Gemini request failed: {exc}") from exc

    def _chunks_from_payload(
        self, payload: dict[str, Any]
    ) -> tuple[Usage | None, list[StreamChunk]]:
        """Convert one Gemini SSE payload into usage plus zero or more chunks."""
        chunks: list[StreamChunk] = []
        for candidate in payload.get("candidates") or []:
            parts = (candidate.get("content") or {}).get("parts") or []
            for idx, part in enumerate(parts):
                text = part.get("text")
                if isinstance(text, str) and text:
                    chunks.append(StreamChunk(type="text", text=text))
                call = part.get("functionCall")
                if call:
                    # Gemini emits whole function calls rather than deltas, so
                    # the full argument object goes out as one delta.
                    chunks.append(
                        StreamChunk(
                            type="tool_call_delta",
                            tool_call=ToolCallDelta(
                                index=idx,
                                id=call.get("id"),
                                name=call.get("name"),
                                arguments_delta=json.dumps(
                                    call.get("args") or {}, ensure_ascii=False
                                ),
                            ),
                        )
                    )

        usage_raw = payload.get("usageMetadata")
        usage: Usage | None = None
        if usage_raw:
            prompt_tokens = int(usage_raw.get("promptTokenCount") or 0)
            completion_tokens = int(usage_raw.get("candidatesTokenCount") or 0)
            usage = Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=int(
                    usage_raw.get("totalTokenCount") or prompt_tokens + completion_tokens
                ),
            )
        return usage, chunks


__all__ = ["GeminiProvider"]
