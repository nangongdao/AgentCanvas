"""Batch an array through an isolated child workflow and aggregate item results."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar, Token
from dataclasses import replace
from typing import Any, cast

from app.engine.events import EventEmitter
from app.engine.nodes.base import BaseNodeExecutor, CompileContext, NodeFn, register_node
from app.engine.state import WorkflowState
from app.engine.templates import build_context, render_value
from app.engine.validation import DSLValidationError, validate_dsl
from app.schemas.dsl import IterationConfig, NodeSpec, NodeType
from app.schemas.events import EventType
from app.services.execution_inspection import bounded_error


class _NamespacedEmitter:
    def __init__(self, emitter: EventEmitter, node_id: str) -> None:
        self._emitter = emitter
        self._node_id = node_id
        self._index: ContextVar[int] = ContextVar(f"iteration_{id(self)}_index")

    def bind(self, index: int) -> Token[int]:
        return self._index.set(index)

    def reset(self, token: Token[int]) -> None:
        self._index.reset(token)

    async def emit(
        self,
        event_type: EventType,
        *,
        node_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        occurrence = f"{self._node_id}[{self._index.get()}]"
        qualified = f"{occurrence}.{node_id}" if node_id else occurrence
        qualified_payload = dict(payload or {})
        node_path = qualified_payload.get("node_path") or node_id
        if isinstance(node_path, str) and node_path:
            qualified_payload["node_path"] = f"{self._node_id}.{node_path}"
        raw_segments = qualified_payload.get("node_path_segments")
        if isinstance(raw_segments, list) and all(isinstance(item, str) for item in raw_segments):
            segments = [self._node_id, *raw_segments]
        elif node_id:
            segments = [self._node_id, node_id]
        else:
            segments = [self._node_id]
        qualified_payload["node_path_segments"] = segments
        if event_type == EventType.EDGE_TAKEN:
            for key in ("source", "target"):
                value = qualified_payload.get(key)
                if isinstance(value, str) and value:
                    qualified_payload[key] = f"{occurrence}.{value}"
        return await self._emitter.emit(
            event_type,
            node_id=qualified,
            payload=qualified_payload,
        )


@register_node("iteration")
class IterationNodeExecutor(BaseNodeExecutor):
    config_model = IterationConfig
    # Fixed aggregate shape: per-item result entries plus rollup counts. The
    # per-item ``output`` field is user-defined by the child subgraph, so it
    # is left open here.
    output_schema = {
        "type": "object",
        "properties": {
            "output": {
                "type": "object",
                "description": "与下述字段同构的聚合输出对象",
            },
            "results": {
                "type": "array",
                "description": "每个项目的执行结果(index/status/output)",
                "items": {"type": "object"},
            },
            "failures": {
                "type": "array",
                "description": "失败项目的明细(index/status/error)",
                "items": {"type": "object"},
            },
            "items_total": {"type": "integer", "description": "项目总数"},
            "items_succeeded": {"type": "integer", "description": "成功项目数"},
            "items_failed": {"type": "integer", "description": "失败项目数"},
        },
    }

    def validate_config(self, config: dict[str, Any]) -> None:
        parsed = IterationConfig.model_validate(config or {})
        workflow = parsed.subgraph.to_workflow()
        if any(node.type == NodeType.HUMAN for node in workflow.nodes):
            raise ValueError("iteration subgraphs do not support human approval nodes")
        try:
            validate_dsl(workflow)
        except DSLValidationError as exc:
            raise ValueError(f"invalid iteration subgraph: {exc}") from exc

    def build(self, node: NodeSpec, ctx: CompileContext) -> NodeFn:
        from app.engine.compiler import WorkflowCompiler

        config = IterationConfig.model_validate(node.config or {})
        child_dsl = config.subgraph.to_workflow(name=f"{node.id}-item")
        validate_dsl(child_dsl)
        namespaced_emitter = _NamespacedEmitter(ctx.emitter, node.id)
        child_ctx = replace(
            ctx,
            emitter=cast(EventEmitter, namespaced_emitter),
        )
        graph = WorkflowCompiler().compile(child_dsl, child_ctx)

        async def execute_item(
            state: WorkflowState,
            *,
            item: Any,
            index: int,
        ) -> dict[str, Any]:
            child_inputs = {
                **dict(state.get("inputs") or {}),
                config.item_variable: item,
                config.index_variable: index,
            }
            child_state: WorkflowState = {
                "inputs": child_inputs,
                "node_outputs": {},
                "loop_counts": {},
                "messages": [],
                "route": None,
                "error": None,
                "final_output": None,
            }
            token = namespaced_emitter.bind(index)
            try:
                try:
                    result = await graph.ainvoke(
                        child_state,
                        config={"recursion_limit": config.recursion_limit},
                    )
                finally:
                    namespaced_emitter.reset(token)
            except Exception as exc:
                return {
                    "index": index,
                    "status": "failed",
                    "error": bounded_error(exc),
                }
            return {
                "index": index,
                "status": "succeeded",
                "output": result.get("final_output"),
            }

        async def run(state: WorkflowState) -> dict[str, Any]:
            template_ctx = build_context(
                inputs=state.get("inputs") or {},
                node_outputs=state.get("node_outputs") or {},
            )
            items = render_value(config.items, template_ctx, strict=True)
            if not isinstance(items, list):
                raise TypeError(f"iteration node '{node.id}' items must resolve to an array")

            collected: list[dict[str, Any]] = []
            failures: list[dict[str, Any]] = []
            semaphore = asyncio.Semaphore(config.concurrency_limit)

            async def limited(item: Any, index: int) -> dict[str, Any]:
                async with semaphore:
                    return await execute_item(state, item=item, index=index)

            for batch_start in range(0, len(items), config.batch_size):
                batch = items[batch_start : batch_start + config.batch_size]
                tasks = [
                    asyncio.create_task(limited(item, batch_start + offset))
                    for offset, item in enumerate(batch)
                ]
                entries_by_index: dict[int, dict[str, Any]] = {}
                try:
                    for completed in asyncio.as_completed(tasks):
                        entry = await completed
                        entries_by_index[int(entry["index"])] = entry
                        if entry["status"] == "failed" and config.failure_strategy == "abort":
                            raise RuntimeError(
                                f"iteration item {entry['index']} failed: {entry['error']}"
                            )
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                entries = [entries_by_index[index] for index in sorted(entries_by_index)]
                for entry in entries:
                    if entry["status"] == "failed":
                        failures.append(entry)
                        if config.failure_strategy == "skip":
                            continue
                    collected.append(entry)

            succeeded = sum(entry["status"] == "succeeded" for entry in collected)
            output = {
                "results": collected,
                "failures": failures,
                "items_total": len(items),
                "items_succeeded": succeeded,
                "items_failed": len(failures),
            }
            return {"node_outputs": {node.id: {"output": output, **output}}}

        return run
