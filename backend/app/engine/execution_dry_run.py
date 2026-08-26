"""Single-node dry-run: execute one node with mock inputs, no history (C2-6).

A dry-run compiles a tiny ``start → target → end`` workflow with the caller's
mock inputs wired as the start schema, runs it on an ephemeral event bus
(``persist=None``) under a throwaway execution id, and returns the node output
plus the lifecycle events. Nothing is written to the executions table, no
queue lease is acquired, and no quota/project slot is reserved — the run is
synchronous and lives only for the request.
"""

from __future__ import annotations

import logging
from typing import Any

from app.engine.events import EventBus, EventEmitter
from app.engine.nodes import CompileContext
from app.schemas.dsl import (
    EdgeSpec,
    EndConfig,
    InputField,
    NodeSpec,
    NodeType,
    Position,
    StartConfig,
    VariableType,
    WorkflowDSL,
    WorkflowSettings,
)
from app.schemas.events import ExecutionEvent
from app.services.execution_inspection import bounded_error, bounded_json_snapshot

logger = logging.getLogger(__name__)

_DRY_RUN_PREFIX = "dryrun"
_MAX_EVENTS = 200


def _variable_type_for(value: Any) -> VariableType:
    if isinstance(value, bool):
        return VariableType.BOOLEAN
    if isinstance(value, (int, float)):
        return VariableType.NUMBER
    if isinstance(value, list):
        return VariableType.ARRAY
    if isinstance(value, dict):
        return VariableType.OBJECT
    return VariableType.STRING


class DryRunError(ValueError):
    """Raised when a node cannot be dry-run (unsupported type or bad config)."""


# Node types that make no sense as an isolated single-node dry-run.
_UNSUPPORTED_DRY_RUN_TYPES: frozenset[str] = frozenset(
    {NodeType.START.value, NodeType.END.value, NodeType.SUBWORKFLOW.value}
)


class ExecutionDryRunMixin:
    """Execute a single node with mock inputs without persisting any history."""

    async def dry_run_node(
        self: Any,
        *,
        workflow_id: str,
        node_id: str,
        node_type: str,
        node_config: dict[str, Any],
        mock_inputs: dict[str, Any],
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if node_type in _UNSUPPORTED_DRY_RUN_TYPES:
            raise DryRunError(
                f"node type '{node_type}' is not supported for single-node dry-run"
            )
        if node_type == NodeType.ITERATION.value:
            raise DryRunError(
                "iteration nodes dry-run via their subgraph; run the parent workflow instead"
            )

        # Build a minimal DSL: start(declares mock inputs) -> target -> end.
        input_schema = [
            InputField(
                name=name,
                type=_variable_type_for(value),
                required=True,
            )
            for name, value in mock_inputs.items()
        ]
        target = NodeSpec(
            id=node_id,
            type=node_type,
            config=dict(node_config or {}),
            position=Position(x=300, y=0),
        )
        dsl = WorkflowDSL(
            name=f"{_DRY_RUN_PREFIX}-{node_id}",
            variables=[],
            settings=WorkflowSettings(
                max_loop_iterations=10,
                timeout_seconds=120,
                recursion_limit=50,
            ),
            nodes=[
                NodeSpec(
                    id="start",
                    type=NodeType.START,
                    config=StartConfig(input_schema=input_schema).model_dump(),
                    position=Position(x=0, y=0),
                ),
                target,
                NodeSpec(
                    id="end",
                    type=NodeType.END,
                    config=EndConfig(
                        output_template={"output": f"{{{{nodes.{node_id}.output}}}}"}
                    ).model_dump(),
                    position=Position(x=600, y=0),
                ),
            ],
            edges=[
                EdgeSpec(id="e1", source="start", target=node_id),
                EdgeSpec(id="e2", source=node_id, target="end"),
            ],
        )

        providers: dict[str, Any] = {}
        execution_mcp: Any | None = None
        execution_id = f"{_DRY_RUN_PREFIX}-{node_id}"
        collected: list[dict[str, Any]] = []

        def collect(event: ExecutionEvent) -> None:
            if len(collected) >= _MAX_EVENTS:
                return
            collected.append(
                {
                    "event_type": event.event_type.value,
                    "node_id": event.node_id,
                    "payload": bounded_json_snapshot(event.payload or {}),
                }
            )

        # Ephemeral bus: persist is None so no rows are written and no SSE relay.
        event_bus = EventBus(observer=collect)
        emitter = EventEmitter(event_bus, execution_id)
        try:
            providers = await self.load_workflow_providers(dsl)

            def get_provider(model_config_id: str) -> Any:
                try:
                    return providers[model_config_id]
                except KeyError as exc:
                    raise RuntimeError(
                        f"Provider '{model_config_id}' was not preloaded"
                    ) from exc

            if self.mcp_manager is not None:
                execution_mcp = self.mcp_manager.for_execution(execution_id)
            subworkflow_cache = await self._resolve_subworkflow_cache(dsl)
            ctx = CompileContext(
                execution_id=execution_id,
                emitter=emitter,
                get_provider=get_provider,
                max_loop_iterations=dsl.settings.max_loop_iterations,
                project_id=project_id,
                mcp_manager=execution_mcp,
                rag_service=self.rag_service,
                memory_store=self.memory_store,
                settings=self.settings,
                observability=self.observability,
                model_budget=None,
                resilience=self.resilience,
                resilience_config=self.resilience_config,
                secret_resolver=self.secret_resolver,
                sandbox=self.sandbox,
                subworkflow_loader=(
                    subworkflow_cache.lookup if subworkflow_cache else None
                ),
            )
            graph = self.compiler.compile(dsl, ctx)
            init_state: dict[str, Any] = {
                "inputs": dict(mock_inputs),
                "node_outputs": {},
                "loop_counts": {},
                "messages": [],
                "route": None,
                "error": None,
                "final_output": None,
            }
            final_state: dict[str, Any] = dict(init_state)
            try:
                async for update in graph.astream(
                    init_state,
                    config={
                        "configurable": {"thread_id": execution_id},
                        "recursion_limit": dsl.settings.recursion_limit,
                    },
                    stream_mode="updates",
                ):
                    if not isinstance(update, dict):
                        continue
                    for _nid, delta in update.items():
                        if not isinstance(delta, dict):
                            continue
                        if "node_outputs" in delta and isinstance(
                            delta["node_outputs"], dict
                        ):
                            final_state.setdefault("node_outputs", {}).update(
                                delta["node_outputs"]
                            )
                        if "final_output" in delta:
                            final_state["final_output"] = delta["final_output"]
                        if "error" in delta and delta["error"]:
                            final_state["error"] = delta["error"]
            except Exception as exc:  # noqa: BLE001 - surface as dry-run error
                raise DryRunError(bounded_error(exc)) from exc

            node_output = (final_state.get("node_outputs") or {}).get(node_id)
            output_value = (
                node_output.get("output") if isinstance(node_output, dict) else node_output
            )
            return {
                "node_id": node_id,
                "output": bounded_json_snapshot(output_value),
                "events": collected,
                "final_output": bounded_json_snapshot(final_state.get("final_output")),
            }
        finally:
            await self.close_workflow_providers(providers)
            if execution_mcp is not None:
                try:
                    await execution_mcp.shutdown()
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    logger.exception(
                        "failed to release dry-run MCP sessions for node %s", node_id
                    )
            await event_bus.close_execution(execution_id)


__all__ = ["DryRunError", "ExecutionDryRunMixin"]
