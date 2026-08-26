"""P3 MCP integration tests: owner task, tool node, and agent tool loop."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.engine.compiler import WorkflowCompiler
from app.mcphub.connection import McpConnection, McpServerSpec
from app.providers.base import ChatMessage, ChatResult, ToolCall, ToolSchema, Usage
from app.schemas.dsl import WorkflowDSL
from app.schemas.events import EventType
from tests.conftest import FakeChatProvider, FakeEmitter, make_ctx

INIT_STATE: dict[str, Any] = {
    "inputs": {},
    "node_outputs": {},
    "loop_counts": {},
    "messages": [],
    "route": None,
    "error": None,
    "final_output": None,
}


class FakeMcpManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def list_tools(self, server_id: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        return [
            {
                "name": "eval_expr",
                "description": "Evaluate math",
                "input_schema": {
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
            }
        ]

    async def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append((server_id, tool_name, arguments))
        return {"content": "42", "is_error": False}


class ToolCallingProvider(FakeChatProvider):
    def __init__(self) -> None:
        super().__init__()
        self.chat_round = 0

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] = (),
        **_params: Any,
    ) -> ChatResult:
        self.chat_round += 1
        self.calls.append(list(messages))
        if self.chat_round == 1:
            assert [tool.name for tool in tools] == ["eval_expr"]
            return ChatResult(
                content="",
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name="eval_expr",
                        arguments='{"expression":"6 * 7"}',
                    ),
                ),
                usage=Usage(10, 2, 12),
            )
        assert messages[-1].role == "tool"
        assert messages[-1].content == "42"
        return ChatResult(content="The answer is 42.", usage=Usage(15, 5, 20))


async def test_owner_task_calculator_round_trip() -> None:
    script = Path(__file__).resolve().parents[1] / "mcp_servers" / "calculator.py"
    connection = McpConnection(
        McpServerSpec(
            id="calculator-test",
            name="Calculator",
            transport="stdio",
            command=sys.executable,
            args=(str(script),),
        )
    )
    await connection.start()
    try:
        assert connection.session is not None
        assert connection.session.protocol_version == "2026-07-28"
        tools = await connection.list_tools()
        result = await connection.call_tool("eval_expr", {"expression": "6 * 7"})
    finally:
        await connection.stop()

    assert {tool["name"] for tool in tools} >= {"eval_expr", "convert_units"}
    assert result == {"content": "42", "is_error": False}


async def test_tool_node_renders_arguments(fake_emitter: FakeEmitter) -> None:
    mcp = FakeMcpManager()
    ctx = replace(make_ctx(fake_emitter), mcp_manager=mcp)
    dsl = WorkflowDSL.model_validate(
        {
            "name": "tool-node",
            "nodes": [
                {"id": "start", "type": "start"},
                {
                    "id": "calc",
                    "type": "tool",
                    "config": {
                        "server_id": "demo-calculator",
                        "tool_name": "eval_expr",
                        "arguments": {"expression": "{{input.expression}}"},
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "config": {"output_template": {"answer": "{{nodes.calc.output}}"}},
                },
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "calc"},
                {"id": "e2", "source": "calc", "target": "end"},
            ],
        }
    )
    state = {**INIT_STATE, "inputs": {"expression": "6 * 7"}}
    result = await WorkflowCompiler().compile(dsl, ctx).ainvoke(state)

    assert mcp.calls == [("demo-calculator", "eval_expr", {"expression": "6 * 7"})]
    assert result["node_outputs"]["calc"]["output"] == 42
    assert [
        event["payload"]["kind"] for event in fake_emitter.of_type(EventType.NODE_STREAMING)
    ] == ["tool_call", "tool_result"]


async def test_agent_executes_bound_mcp_tool(fake_emitter: FakeEmitter) -> None:
    mcp = FakeMcpManager()
    provider = ToolCallingProvider()
    ctx = replace(make_ctx(fake_emitter, provider), mcp_manager=mcp)
    dsl = WorkflowDSL.model_validate(
        {
            "name": "agent-tools",
            "nodes": [
                {"id": "start", "type": "start"},
                {
                    "id": "agent",
                    "type": "agent",
                    "config": {
                        "system_prompt": "Use tools.",
                        "user_prompt": "{{input.question}}",
                        "tools": [
                            {
                                "server_id": "demo-calculator",
                                "tool_name": "eval_expr",
                            }
                        ],
                    },
                },
                {"id": "end", "type": "end"},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "agent"},
                {"id": "e2", "source": "agent", "target": "end"},
            ],
        }
    )
    state = {**INIT_STATE, "inputs": {"question": "What is 6 * 7?"}}
    result = await WorkflowCompiler().compile(dsl, ctx).ainvoke(state)

    assert result["node_outputs"]["agent"]["output"] == "The answer is 42."
    assert mcp.calls == [("demo-calculator", "eval_expr", {"expression": "6 * 7"})]
    streaming = fake_emitter.of_type(EventType.NODE_STREAMING)
    assert [event["payload"]["kind"] for event in streaming] == [
        "tool_call",
        "tool_result",
    ]
