"""Agent structured output (schema-constrained JSON) — Backlog item.

Covers:
- ``AgentConfig.output.schema`` validation against ``output.format``.
- mock provider honoring ``response_format.json_schema`` to emit schema-shaped JSON.
- agent node wiring ``output.schema`` into provider params so OpenAI-compatible
  providers receive ``response_format={"type": "json_schema", ...}``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.provider_capabilities import ProviderCapabilities
from app.engine.nodes.agent import _structured_output_params
from app.providers.base import ChatMessage
from app.providers.mock_provider import MockChatProvider, _mock_json_reply
from app.schemas.dsl import AgentConfig


def _agent(output: dict[str, Any]) -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "model_config_id": "default",
            "output": output,
        }
    )


# ---------------------------------------------------------------- DSL 校验


def test_dsl_accepts_schema_with_json_format() -> None:
    cfg = _agent({"format": "json", "schema": {"type": "object", "properties": {"a": {"type": "string"}}}})
    assert cfg.output["schema"]["type"] == "object"


def test_dsl_accepts_schema_with_json_schema_format() -> None:
    cfg = _agent({"format": "json_schema", "schema": {"type": "object", "properties": {"a": {"type": "string"}}}})
    assert cfg.output["format"] == "json_schema"


def test_dsl_rejects_schema_with_text_format() -> None:
    with pytest.raises(ValueError, match="output.schema requires output.format"):
        _agent({"format": "text", "schema": {"type": "object"}})


def test_dsl_rejects_non_object_schema() -> None:
    with pytest.raises(ValueError, match="output.schema must be a JSON Schema object"):
        _agent({"format": "json", "schema": ["not", "an", "object"]})


def test_dsl_accepts_plain_json_format_without_schema() -> None:
    cfg = _agent({"format": "json"})
    assert cfg.output["format"] == "json"


# ------------------------------------------------------------ mock provider


def test_mock_json_reply_default_shape() -> None:
    assert json.loads(_mock_json_reply({})) == {"next": "FINISH", "reason": "mock response"}


def test_mock_json_reply_honors_json_schema() -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "count": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    }
    reply = json.loads(
        _mock_json_reply(
            {"response_format": {"type": "json_schema", "json_schema": {"schema": schema}}}
        )
    )
    assert reply["name"] == "mock"
    assert reply["count"] == 1
    assert reply["tags"] == ["mock"]


def test_mock_json_reply_ignores_non_json_schema_response_format() -> None:
    reply = json.loads(
        _mock_json_reply({"response_format": {"type": "json_object"}})
    )
    assert "next" in reply


async def test_mock_provider_streams_schema_shaped_json() -> None:
    demo = MockChatProvider(model="mock", delay=0)
    demo.capabilities = ProviderCapabilities(stream=True, json_mode=True, usage=True)
    params = demo.prepare_params(
        frozenset({"stream", "json_mode"}),
        {
            "json_mode": True,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "schema": {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}, "score": {"type": "number"}},
                    }
                },
            },
        },
    )
    chunks = [
        chunk
        async for chunk in demo.stream_chat(
            [ChatMessage(role="user", content="route")], **params
        )
    ]
    reply = json.loads("".join(chunk.text for chunk in chunks if chunk.type == "text"))
    assert reply == {"answer": "mock", "score": 1.0}


# ---------------------------------------------------------- agent 节点接线


def test_structured_output_params_adds_json_object_without_schema() -> None:
    result = _structured_output_params(_agent({"format": "json"}))
    assert result["response_format"] == {"type": "json_object"}


def test_structured_output_params_adds_json_schema() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    result = _structured_output_params(_agent({"format": "json_schema", "schema": schema}))
    assert result["response_format"] == {
        "type": "json_schema",
        "json_schema": schema,
    }


def test_structured_output_params_preserves_existing_response_format() -> None:
    result = _structured_output_params(
        _agent({"format": "json", "params": {"response_format": {"type": "json_object"}}})
    )
    assert result["response_format"] == {"type": "json_object"}


def test_structured_output_params_untouched_for_text() -> None:
    result = _structured_output_params(_agent({"format": "text", "params": {"temperature": 0.3}}))
    assert result == {"temperature": 0.3}
