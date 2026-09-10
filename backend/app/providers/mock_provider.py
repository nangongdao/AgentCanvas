"""Mock provider that streams a canned response — for demos/tests without an API key."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Collection, Mapping, Sequence
from typing import Any

from app.core.provider_capabilities import CapabilityName, ProviderCapabilities
from app.providers.base import (
    COPILOT_PROMPT_MARKER,
    BaseChatProvider,
    ChatMessage,
    StreamChunk,
    ToolCallDelta,
    ToolSchema,
    Usage,
    register_provider,
)


def _mock_value_for_type(schema: dict[str, Any]) -> Any:
    """Return a deterministic mock value guided by a JSON Schema property."""
    schema_type = schema.get("type")
    if schema_type == "array":
        items = schema.get("items")
        return [_mock_value_for_type(items) if isinstance(items, dict) else "item"]
    if schema_type == "integer":
        return 1
    if schema_type == "number":
        return 1.0
    if schema_type == "boolean":
        return True
    if schema_type == "null":
        return None
    if schema_type == "object":
        return _mock_schema_payload(schema.get("properties", {}))
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    return "mock"


def _mock_schema_payload(properties: dict[str, Any]) -> dict[str, Any]:
    return {key: _mock_value_for_type(schema) for key, schema in properties.items()}


def _mock_json_reply(params: Mapping[str, Any]) -> str:
    """Render a deterministic JSON reply, honoring response_format.json_schema."""
    response_format = params.get("response_format") or {}
    schema = None
    if isinstance(response_format, dict) and response_format.get("type") == "json_schema":
        candidate = response_format.get("json_schema") or {}
        if isinstance(candidate, dict):
            schema = candidate.get("schema") or candidate
    properties = {}
    if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
        properties = schema["properties"]
    payload = _mock_schema_payload(properties) if properties else {
        "next": "FINISH",
        "reason": "mock response",
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


# A minimal but fully valid workflow: exactly one start, one end, reachable from
# start, no cycles, every config within its node schema. Used only as the demo
# adapter's answer to the copilot so the feature is demonstrable without an API
# key — it is a stand-in, never presented as a model's plan.
_MOCK_COPILOT_DSL: dict[str, Any] = {
    "version": "1.0",
    "name": "示例工作流(Mock)",
    "variables": [],
    "settings": {"max_loop_iterations": 20, "timeout_seconds": 300, "recursion_limit": 50},
    "nodes": [
        {
            "id": "start",
            "type": "start",
            "name": "开始",
            "position": {"x": 0, "y": 0},
            "config": {
                "input_schema": [
                    {"name": "user_query", "type": "string", "required": True, "default": None}
                ]
            },
        },
        {
            "id": "agent",
            "type": "agent",
            "name": "智能体",
            "position": {"x": 320, "y": 0},
            "config": {
                "model_config_id": "default",
                "system_prompt": "你是一个有帮助的助手。",
                "user_prompt": "{{input.user_query}}",
            },
        },
        {
            "id": "end",
            "type": "end",
            "name": "结束",
            "position": {"x": 640, "y": 0},
            "config": {"output_template": {"answer": "{{nodes.agent.output}}"}},
        },
    ],
    "edges": [
        {"id": "edge-start-agent", "source": "start", "target": "agent"},
        {"id": "edge-agent-end", "source": "agent", "target": "end"},
    ],
    "canvas": {"groups": [], "notes": []},
}


def _mock_copilot_reply(messages: Sequence[ChatMessage]) -> str | None:
    """Answer the workflow-copilot prompt with a canned valid DSL draft.

    Returns ``None`` for every other request so normal mock behaviour is
    untouched; the copilot reply is only produced when its system prompt
    carries :data:`COPILOT_PROMPT_MARKER`.
    """
    for message in messages:
        if message.role == "system" and COPILOT_PROMPT_MARKER in (message.content or ""):
            return json.dumps(_MOCK_COPILOT_DSL, ensure_ascii=False, separators=(",", ":"))
    return None


@register_provider("mock")
class MockChatProvider(BaseChatProvider):
    """Streams a deterministic reply token-by-token with small delays."""

    default_capabilities = ProviderCapabilities(stream=True, usage=True)

    def prepare_params(
        self,
        required_capabilities: Collection[CapabilityName],
        params: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        prepared = super().prepare_params(required_capabilities, params)
        if "json_mode" in required_capabilities:
            prepared["_mock_json_mode"] = True
        return prepared

    def __init__(
        self,
        *,
        model: str = "mock",
        api_key: str = "",
        base_url: str | None = None,
        default_params: dict[str, Any] | None = None,
        delay: float = 0.03,
    ) -> None:
        super().__init__(
            model=model, api_key=api_key, base_url=base_url, default_params=default_params
        )
        self.delay = delay

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] = (),
        **params: Any,
    ) -> AsyncIterator[StreamChunk]:
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        if tools and not any(message.role == "tool" for message in messages):
            tool = tools[0]
            yield StreamChunk(
                type="tool_call_delta",
                tool_call=ToolCallDelta(
                    index=0,
                    id="mock-tool-call",
                    name=tool.name,
                    arguments_delta="{}",
                ),
            )
            yield StreamChunk(
                type="usage",
                usage=Usage(prompt_tokens=len(user), completion_tokens=1),
            )
            yield StreamChunk(type="done")
            return
        copilot_reply = _mock_copilot_reply(messages)
        if copilot_reply is not None:
            # A copilot draft is consumed whole (parsed as JSON), never rendered
            # token-by-token to a human, so the per-character demo pacing would
            # only make the request slow.
            yield StreamChunk(type="text", text=copilot_reply)
            yield StreamChunk(
                type="usage",
                usage=Usage(prompt_tokens=len(user), completion_tokens=len(copilot_reply)),
            )
            yield StreamChunk(type="done")
            return
        if params.pop("_mock_json_mode", False):
            reply = _mock_json_reply(params)
        else:
            reply = (
                f"[Mock 模式] 我收到了你的输入:「{user[:80]}」。"
                "当前未配置 OPENAI_API_KEY,系统使用模拟模型演示流式输出与执行链路。"
                "请在 .env 中填入真实 API Key 后重启后端即可接入真实模型。"
            )
        for ch in reply:
            await asyncio.sleep(self.delay)
            yield StreamChunk(type="text", text=ch)
        yield StreamChunk(
            type="usage",
            usage=Usage(prompt_tokens=len(user), completion_tokens=len(reply)),
        )
        yield StreamChunk(type="done")
