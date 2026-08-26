"""Subworkflow node — embed a referenced published version as a child graph (C2-5)."""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from dataclasses import replace
from typing import Any, cast

from app.engine.events import EventEmitter
from app.engine.nodes.base import BaseNodeExecutor, CompileContext, NodeFn, register_node
from app.engine.state import WorkflowState
from app.engine.templates import build_context, render_value
from app.engine.validation import DSLValidationError, validate_dsl
from app.schemas.dsl import NodeSpec, SubworkflowConfig
from app.schemas.events import EventType
from app.services.execution_inspection import bounded_error

logger = logging.getLogger(__name__)


class SubworkflowCycleError(ValueError):
    """Raised when a subworkflow chain revisits a workflow_id on its ancestor path."""


class _NamespacedEmitter:
    """Qualify child-graph events under the parent subworkflow node id.

    Child node ``n`` under subworkflow node ``sub`` becomes ``sub.n`` so the
    parent execution tree can expand the child attempts in history (C2-5).
    The node_path_segments are prefixed the same way so cost attribution and
    Inspector grouping stay consistent with the iteration node.
    """

    def __init__(self, emitter: EventEmitter, node_id: str) -> None:
        self._emitter = emitter
        self._node_id = node_id
        self._occurrence = node_id
        self._active: ContextVar[int] = ContextVar(f"subworkflow_{id(self)}_active")

    def bind(self) -> Token[int]:
        return self._active.set(0)

    def reset(self, token: Token[int]) -> None:
        self._active.reset(token)

    async def emit(
        self,
        event_type: EventType,
        *,
        node_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        occurrence = self._occurrence
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


def _resolve_output(child_output: Any, path: str) -> Any:
    """Reduce the child final_output by an optional dotted path."""
    if not path:
        return child_output
    cursor: Any = child_output
    for segment in path.split("."):
        segment = segment.strip()
        if not segment:
            continue
        if isinstance(cursor, dict):
            cursor = cursor.get(segment)
        else:
            return None
    return cursor


@register_node("subworkflow")
class SubworkflowNodeExecutor(BaseNodeExecutor):
    """Embed a referenced workflow's published version as an inlined child graph.

    At compile time the ctx ``subworkflow_loader`` resolves the referenced
    ``workflow_id`` / ``version_id`` into a ``WorkflowDSL``. The child DSL is
    validated and recursively compiled with a namespaced emitter so its events
    nest under this node in the parent execution tree. Cycle detection happens
    during the async pre-resolution pass (see ``subworkflow_resolver``) before
    compilation begins, so by the time ``build`` runs the child is already known
    to be acyclic.
    """

    config_model = SubworkflowConfig
    # Output is the resolved child final_output (mapped by output_mapping, or
    # the child's whole final_output when no mapping is declared). Fields are
    # user-defined by the referenced workflow, so the shape is left open.
    output_schema = {
        "type": "object",
        "description": "子流程 final_output 按 output_mapping 映射后的结果",
        "properties": {"output": {"description": "子流程映射后的输出对象"}},
        "additionalProperties": True,
    }

    def metadata(self) -> dict[str, Any]:
        meta = super().metadata()
        meta["label"] = "Subworkflow"
        meta["description"] = "引用另一工作流的已发布版本作为子流程节点,执行树可展开。"
        return meta

    def validate_config(self, config: dict[str, Any]) -> None:
        cfg = SubworkflowConfig.model_validate(config or {})
        if cfg.version_id and cfg.workflow_id == cfg.version_id:
            raise ValueError("subworkflow workflow_id and version_id must differ")

    def build(self, node: NodeSpec, ctx: CompileContext) -> NodeFn:
        from app.engine.compiler import WorkflowCompiler

        cfg = SubworkflowConfig.model_validate(node.config or {})
        lookup = ctx.subworkflow_loader
        if lookup is None:
            raise SubworkflowCycleError(
                f"subworkflow node '{node.id}' requires a subworkflow_loader on the "
                "compile context; the runtime did not bind one"
            )
        child_dsl = lookup(cfg.workflow_id, cfg.version_id)
        if child_dsl is None:
            raise ValueError(
                f"subworkflow node '{node.id}' referenced version "
                f"{cfg.version_id} of workflow {cfg.workflow_id} could not be loaded"
            )
        try:
            validate_dsl(child_dsl)
        except DSLValidationError as exc:
            raise ValueError(f"invalid subworkflow child of node '{node.id}': {exc}") from exc
        namespaced_emitter = _NamespacedEmitter(ctx.emitter, node.id)
        child_ctx = replace(ctx, emitter=cast(EventEmitter, namespaced_emitter))
        graph = WorkflowCompiler().compile(child_dsl, child_ctx, start_node_id=None)

        async def run(state: WorkflowState) -> dict[str, Any]:
            template_ctx = build_context(
                inputs=state.get("inputs") or {},
                node_outputs=state.get("node_outputs") or {},
            )
            child_inputs: dict[str, Any] = {}
            for name, expr in cfg.input_mapping.items():
                child_inputs[name] = render_value(expr, template_ctx, strict=True)
            child_state: WorkflowState = {
                "inputs": child_inputs,
                "node_outputs": {},
                "loop_counts": {},
                "messages": [],
                "route": None,
                "error": None,
                "final_output": None,
            }
            token = namespaced_emitter.bind()
            await ctx.emitter.emit(
                EventType.NODE_STREAMING,
                node_id=node.id,
                payload={
                    "kind": "subworkflow_started",
                    "workflow_id": cfg.workflow_id,
                    "version_id": cfg.version_id,
                    "node_path_segments": [node.id],
                },
            )
            try:
                try:
                    result = await graph.ainvoke(
                        child_state,
                        config={"recursion_limit": cfg.recursion_limit},
                    )
                finally:
                    namespaced_emitter.reset(token)
            except Exception as exc:
                raise RuntimeError(
                    f"subworkflow node '{node.id}' failed: {bounded_error(exc)}"
                ) from exc
            child_final = result.get("final_output") or {}
            if not isinstance(child_final, dict):
                child_final = {"result": child_final}
            resolved: dict[str, Any] = {}
            if cfg.output_mapping:
                for key, path in cfg.output_mapping.items():
                    resolved[key] = _resolve_output(child_final, path)
            else:
                resolved = dict(child_final)
            await ctx.emitter.emit(
                EventType.NODE_STREAMING,
                node_id=node.id,
                payload={
                    "kind": "subworkflow_finished",
                    "workflow_id": cfg.workflow_id,
                    "version_id": cfg.version_id,
                    "node_path_segments": [node.id],
                },
            )
            return {"node_outputs": {node.id: {"output": resolved, **resolved}}}

        return run


__all__ = ["SubworkflowCycleError", "SubworkflowNodeExecutor"]
