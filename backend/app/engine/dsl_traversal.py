"""Traversal helpers for top-level and nested workflow nodes."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass

from app.schemas.dsl import IterationConfig, NodeSpec, NodeType, WorkflowDSL


@dataclass(frozen=True)
class LocatedNode:
    path: str
    segments: tuple[str, ...]
    node: NodeSpec

    @property
    def path_key(self) -> str:
        return node_path_key(self.segments)


def node_path_key(segments: tuple[str, ...]) -> str:
    """Encode path segments without allowing node IDs to collide."""
    return json.dumps(list(segments), ensure_ascii=False, separators=(",", ":"))


def iter_located_workflow_nodes(
    dsl: WorkflowDSL,
    *,
    prefix: tuple[str, ...] = (),
) -> Iterator[LocatedNode]:
    for node in dsl.nodes:
        segments = (*prefix, node.id)
        yield LocatedNode(path=".".join(segments), segments=segments, node=node)
        if node.type != NodeType.ITERATION:
            continue
        config = IterationConfig.model_validate(node.config or {})
        child = config.subgraph.to_workflow(name=f"{node.id}-item")
        yield from iter_located_workflow_nodes(child, prefix=segments)


def iter_workflow_nodes(dsl: WorkflowDSL) -> Iterator[NodeSpec]:
    """Yield every node, recursively entering iteration subgraphs."""
    for located in iter_located_workflow_nodes(dsl):
        yield located.node


__all__ = [
    "LocatedNode",
    "iter_located_workflow_nodes",
    "iter_workflow_nodes",
    "node_path_key",
]
