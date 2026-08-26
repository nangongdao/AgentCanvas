"""Build safe, event-backed plans for rerunning a failed workflow node."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.engine.graph_analysis import execution_reachable_nodes
from app.schemas.dsl import AgentConfig, IterationConfig, NodeSpec, NodeType, WorkflowDSL


class ExecutionRerunError(ValueError):
    """Raised when a failed execution cannot be safely replayed from a node."""


@dataclass(frozen=True)
class ExecutionRerunPlan:
    node_id: str
    node_outputs: dict[str, Any]
    reachable_node_ids: frozenset[str]
    reused_node_ids: tuple[str, ...]


def _event_payload(event: Any) -> Mapping[str, Any]:
    payload = getattr(event, "payload_json", None)
    return payload if isinstance(payload, Mapping) else {}


def _is_truncated(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get("_truncated") is True


def node_side_effect_reason(node: NodeSpec) -> str | None:
    if node.type == NodeType.TOOL:
        return "tool calls may mutate external systems"
    if node.type == NodeType.HUMAN:
        return "human approval cannot be replayed automatically"
    if node.type == NodeType.ITERATION:
        iteration_config = IterationConfig.model_validate(node.config or {})
        for child in iteration_config.subgraph.nodes:
            reason = node_side_effect_reason(child)
            if reason:
                return f"iteration child '{child.id}': {reason}"
        return None
    if node.type != NodeType.AGENT:
        return None
    agent_config = AgentConfig.model_validate(node.config or {})
    if agent_config.tools:
        return "agent MCP tool calls may mutate external systems"
    if bool(agent_config.memory.get("enabled")):
        return "agent memory writes cannot be replayed automatically"
    return None


def _failed_input(
    events: Sequence[Any], node_id: str | None
) -> tuple[str, Mapping[str, Any], int]:
    open_inputs: dict[str, Mapping[str, Any]] = {}
    failed: list[tuple[str, Mapping[str, Any] | None, int]] = []
    for event in events:
        current_id = str(getattr(event, "node_id", "") or "")
        event_type = str(getattr(event, "event_type", ""))
        payload = _event_payload(event)
        if event_type == "node_started" and current_id:
            snapshot = payload.get("input")
            open_inputs[current_id] = snapshot if isinstance(snapshot, Mapping) else {}
        elif event_type == "node_finished" and current_id:
            open_inputs.pop(current_id, None)
        elif event_type == "node_failed" and current_id:
            failed.append(
                (
                    current_id,
                    open_inputs.pop(current_id, None),
                    int(getattr(event, "seq", 0) or 0),
                )
            )

    candidates = [item for item in failed if node_id is None or item[0] == node_id]
    if not candidates:
        label = f"node '{node_id}'" if node_id else "the execution"
        raise ExecutionRerunError(f"rerun unavailable: {label} has no failed attempt")
    failed_node_id, snapshot, failed_seq = candidates[-1]
    if snapshot is None:
        raise ExecutionRerunError(
            f"rerun unavailable: failed node '{failed_node_id}' has no input snapshot"
        )
    if _is_truncated(snapshot):
        raise ExecutionRerunError(
            f"rerun unavailable: failed node '{failed_node_id}' input snapshot is truncated"
        )
    return failed_node_id, snapshot, failed_seq


def _collect_outputs(
    events: Sequence[Any], snapshot: Mapping[str, Any], *, through_seq: int
) -> dict[str, Any]:
    upstream = snapshot.get("upstream_outputs")
    if not isinstance(upstream, Mapping):
        raise ExecutionRerunError("rerun unavailable: failed input snapshot is malformed")
    outputs = {str(key): value for key, value in upstream.items()}
    for event in events:
        if int(getattr(event, "seq", 0) or 0) > through_seq:
            break
        if str(getattr(event, "event_type", "")) != "node_finished":
            continue
        node_id = str(getattr(event, "node_id", "") or "")
        payload = _event_payload(event)
        output = payload.get("output")
        if node_id and "output" in payload and not _is_truncated(output):
            outputs[node_id] = output
    return outputs


def _validate_join_snapshots(
    dsl: WorkflowDSL,
    *,
    rerun_node_id: str,
    reachable: frozenset[str],
    outputs: Mapping[str, Any],
) -> None:
    node_map = dsl.node_map()
    missing: set[str] = set()
    for edge in dsl.edges:
        if edge.target not in reachable or edge.source in reachable:
            continue
        source = node_map[edge.source]
        if source.type == NodeType.CONDITION or (
            source.type == NodeType.AGENT
            and (source.config or {}).get("agent_mode") == "supervisor"
        ):
            continue
        if edge.source not in outputs:
            missing.add(edge.source)
    if missing:
        joined = ", ".join(sorted(missing))
        raise ExecutionRerunError(
            f"rerun unavailable: upstream snapshot missing for node(s): {joined}"
        )
    if rerun_node_id not in node_map:
        raise ExecutionRerunError(f"rerun unavailable: node '{rerun_node_id}' is not in the DSL")


def build_execution_rerun_plan(
    dsl: WorkflowDSL,
    events: Sequence[Any],
    *,
    node_id: str | None = None,
) -> ExecutionRerunPlan:
    """Plan a replay from the latest failed attempt of ``node_id`` or the run."""
    failed_node_id, input_snapshot, failed_seq = _failed_input(events, node_id)
    node_map = dsl.node_map()
    if failed_node_id not in node_map:
        raise ExecutionRerunError(
            f"rerun unavailable: failed node '{failed_node_id}' is not in the workflow version"
        )
    reachable = execution_reachable_nodes(dsl, failed_node_id)
    unknown = sorted(reachable - node_map.keys())
    if unknown:
        raise ExecutionRerunError(
            f"rerun unavailable: dynamic target node(s) not found: {', '.join(unknown)}"
        )
    for reachable_id in sorted(reachable):
        reason = node_side_effect_reason(node_map[reachable_id])
        if reason:
            raise ExecutionRerunError(
                f"rerun blocked at node '{reachable_id}': {reason}"
            )
    outputs = _collect_outputs(events, input_snapshot, through_seq=failed_seq)
    _validate_join_snapshots(
        dsl,
        rerun_node_id=failed_node_id,
        reachable=reachable,
        outputs=outputs,
    )
    reused = tuple(sorted(node_id for node_id in outputs if node_id not in reachable))
    return ExecutionRerunPlan(
        node_id=failed_node_id,
        node_outputs=outputs,
        reachable_node_ids=reachable,
        reused_node_ids=reused,
    )


__all__ = [
    "ExecutionRerunError",
    "ExecutionRerunPlan",
    "build_execution_rerun_plan",
    "node_side_effect_reason",
]
