"""Execution-aware workflow graph analysis shared by planning and compilation."""

from __future__ import annotations

from collections import defaultdict, deque

from app.schemas.dsl import AgentConfig, NodeType, WorkflowDSL


def execution_reachable_nodes(dsl: WorkflowDSL, start_node_id: str) -> frozenset[str]:
    """Return nodes reachable through static edges and supervisor commands."""
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in dsl.edges:
        adjacency[edge.source].append(edge.target)

    end_ids = [node.id for node in dsl.nodes if node.type == NodeType.END]
    for node in dsl.nodes:
        if node.type != NodeType.AGENT:
            continue
        config = AgentConfig.model_validate(node.config or {})
        if config.agent_mode != "supervisor":
            continue
        adjacency[node.id].extend(config.workers)
        has_explicit_end = any(target in end_ids for target in adjacency[node.id])
        if not has_explicit_end and end_ids:
            adjacency[node.id].append(end_ids[0])

    seen: set[str] = set()
    queue: deque[str] = deque([start_node_id])
    while queue:
        node_id = queue.popleft()
        if node_id in seen:
            continue
        seen.add(node_id)
        queue.extend(adjacency[node_id])
    return frozenset(seen)


__all__ = ["execution_reachable_nodes"]
