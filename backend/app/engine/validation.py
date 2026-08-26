"""DSL static validation before compilation."""

from __future__ import annotations

from collections import defaultdict, deque

from app.engine.nodes.base import get_executor
from app.schemas.dsl import ConditionConfig, NodeType, WorkflowDSL


class DSLValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_dsl(dsl: WorkflowDSL, *, strict: bool = True) -> None:
    """Raise DSLValidationError if the workflow graph is invalid.

    ``strict`` controls reachability/cycle enforcement. Editors draft graphs
    in intermediate states (e.g. a pasted subgraph not yet wired back to
    ``start``); saving those drafts must not be rejected, so draft-save paths
    pass ``strict=False`` and skip reachability + cycle checks.
    Compile/publish/run paths keep the default ``strict=True`` so an
    unrunnable graph can never be executed — reachability is rebuilt from the
    stored draft before compile.
    """
    errors: list[str] = []
    if not dsl.nodes:
        errors.append("workflow must contain at least one node")
        raise DSLValidationError(errors)

    ids = [n.id for n in dsl.nodes]
    if len(ids) != len(set(ids)):
        errors.append("duplicate node ids")

    node_map = dsl.node_map()
    starts = [n for n in dsl.nodes if n.type == NodeType.START]
    ends = [n for n in dsl.nodes if n.type == NodeType.END]
    if len(starts) != 1:
        errors.append(f"exactly one start node required, found {len(starts)}")
    if len(ends) < 1:
        errors.append("at least one end node required")

    # Resolve every executor before graph checks. This makes an unavailable
    # plugin fail at the save/publish/compile boundary instead of halfway
    # through a running graph.
    for node in dsl.nodes:
        try:
            executor = get_executor(node.type)
        except KeyError:
            errors.append(f"unknown node type '{node.type}' on node '{node.id}'")
            continue
        try:
            executor.validate_config(node.config or {})
        except Exception as exc:  # noqa: BLE001 - expose a stable DSL error
            errors.append(f"invalid config for node '{node.id}': {exc}")

    for edge in dsl.edges:
        if edge.source not in node_map:
            errors.append(f"edge {edge.id} source '{edge.source}' not found")
        if edge.target not in node_map:
            errors.append(f"edge {edge.id} target '{edge.target}' not found")

    # Condition handle checks
    for edge in dsl.edges:
        src = node_map.get(edge.source)
        if src and src.type == NodeType.CONDITION and edge.source_handle:
            cfg = ConditionConfig.model_validate(src.config or {})
            branch_ids = {b.id for b in cfg.branches} | {cfg.default_branch, "else"}
            if edge.source_handle not in branch_ids:
                errors.append(
                    f"edge {edge.id} source_handle '{edge.source_handle}' "
                    f"not in condition branches of {edge.source}"
                )

    # Reachability from start (+ static cycle detection). Only enforced in
    # strict mode: draft saves allow an unreachable pasted subgraph to rest on
    # the canvas until the editor wires it back in; compile/publish/run still
    # pass strict=True and will reject the same graph before execution.
    if strict and starts and not errors:
        start_id = starts[0].id
        adj: dict[str, list[str]] = defaultdict(list)
        for edge in dsl.edges:
            adj[edge.source].append(edge.target)
        seen: set[str] = set()
        q: deque[str] = deque([start_id])
        while q:
            cur = q.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            for nxt in adj[cur]:
                q.append(nxt)
        unreachable = [n.id for n in dsl.nodes if n.id not in seen]
        if unreachable:
            errors.append(f"nodes unreachable from start: {', '.join(unreachable)}")

        # Pure static cycle detection (no condition/supervisor on cycle → error)
        white, gray, black = 0, 1, 2
        color = {n.id: white for n in dsl.nodes}
        cycle_nodes: list[str] = []

        def dfs(u: str, path: list[str]) -> None:
            color[u] = gray
            path.append(u)
            for v in adj[u]:
                if color[v] == gray:
                    cycle = path[path.index(v) :] + [v]
                    # Allowed if any node on cycle is condition/supervisor agent
                    allowed = False
                    for cid in cycle[:-1]:
                        n = node_map[cid]
                        if n.type == NodeType.CONDITION:
                            allowed = True
                            break
                        if n.type == NodeType.AGENT:
                            mode = (n.config or {}).get("agent_mode")
                            if mode == "supervisor":
                                allowed = True
                                break
                    if not allowed:
                        cycle_nodes.append(" -> ".join(cycle))
                elif color[v] == white:
                    dfs(v, path)
            path.pop()
            color[u] = black

        dfs(start_id, [])
        for cyc in cycle_nodes:
            errors.append(f"static cycle without condition/supervisor: {cyc}")

    if errors:
        raise DSLValidationError(errors)
