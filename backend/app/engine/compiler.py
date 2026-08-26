"""Compile WorkflowDSL into a LangGraph StateGraph.

Supports: linear chains, condition branches (conditional edges + edge_taken
events), parallel fan-out with join barriers, supervisor dynamic routing
(Command), and double-layer loop protection.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Hashable

from langgraph.graph import END, START, StateGraph

from app.engine.conditions import pick_branch
from app.engine.debug_breakpoints import (
    breakpoint_node_id,
    build_breakpoint_fn,
    should_break,
)
from app.engine.graph_analysis import execution_reachable_nodes
from app.engine.nodes import CompileContext, get_executor, instrument
from app.engine.nodes.supervisor import build_supervisor_fn
from app.engine.state import WorkflowState
from app.engine.templates import build_context
from app.engine.validation import validate_dsl
from app.schemas.dsl import AgentConfig, ConditionConfig, EdgeSpec, NodeType, WorkflowDSL
from app.schemas.events import EventType

logger = logging.getLogger(__name__)


def _is_supervisor(node) -> bool:
    return (
        node.type == NodeType.AGENT
        and (node.config or {}).get("agent_mode") == "supervisor"
    )


class WorkflowCompiler:
    """DSL → compiled LangGraph."""

    def compile(
        self,
        dsl: WorkflowDSL,
        ctx: CompileContext,
        *,
        checkpointer=None,
        start_node_id: str | None = None,
    ):
        validate_dsl(dsl)
        builder = StateGraph(WorkflowState)
        node_map = dsl.node_map()
        if start_node_id is not None and start_node_id not in node_map:
            raise ValueError(f"rerun start node '{start_node_id}' not found")

        out_edges: dict[str, list[EdgeSpec]] = defaultdict(list)
        for edge in dsl.edges:
            out_edges[edge.source].append(edge)

        # ---- 1. Register nodes ----
        breakpoint_after: dict[str, str] = {}
        for node in dsl.nodes:
            if _is_supervisor(node):
                cfg = AgentConfig.model_validate(node.config or {})
                finish_target = self._supervisor_finish_target(node.id, out_edges, dsl)
                fn = build_supervisor_fn(node, cfg, ctx, finish_target=finish_target)
                # LangGraph 1.x add_node overloads require the action return to
                # be typed against WorkflowState; our node fns return a plain
                # ``dict[str, Any] | Command`` (see NodeFn). Runtime is valid.
                builder.add_node(node.id, instrument(node.id, fn, ctx))  # type: ignore[call-overload]
            else:
                executor = get_executor(node.type)
                fn = instrument(node.id, executor.build(node, ctx), ctx)
                builder.add_node(node.id, fn)  # type: ignore[call-overload]
            # C2-7: inject a side-effect-free breakpoint node after linear nodes
            # flagged for debug pause. The breakpoint reuses the human interrupt
            # mechanism and is registered here so the edge rewrite below can
            # route through it without touching conditional/switch/supervisor
            # routing. Only nodes with a single linear exit edge are eligible;
            # multi-exit nodes keep their plain join-barrier routing.
            if should_break(ctx, node) and len(out_edges.get(node.id, [])) == 1:
                bp_id = breakpoint_node_id(node.id)
                breakpoint_after[node.id] = bp_id
                bp_fn = build_breakpoint_fn(node.id, ctx)
                builder.add_node(bp_id, instrument(bp_id, bp_fn, ctx))  # type: ignore[call-overload]

        # ---- 2. Entry / exits ----
        starts = [n for n in dsl.nodes if n.type == NodeType.START]
        entry_node_id = start_node_id or starts[0].id
        active_nodes = (
            set(execution_reachable_nodes(dsl, entry_node_id))
            if start_node_id
            else set(node_map)
        )
        builder.add_edge(START, entry_node_id)
        for node in dsl.nodes:
            if node.id in active_nodes and node.type == NodeType.END:
                builder.add_edge(node.id, END)

        # ---- 3. Edges ----
        # Group plain edges by TARGET to build join barriers:
        # a target with multiple plain predecessors waits for all of them.
        plain_by_target: dict[str, list[str]] = defaultdict(list)

        for source, edges in out_edges.items():
            if source not in active_nodes:
                continue
            src = node_map[source]
            if src.type == NodeType.END:
                continue
            if _is_supervisor(src):
                # Supervisor routes via Command; edges are visual only.
                continue

            if src.type == NodeType.CONDITION or src.type == NodeType.SWITCH:
                self._add_condition_edges(
                    builder,
                    src,
                    [edge for edge in edges if edge.target in active_nodes],
                    ctx,
                )
                continue

            for e in edges:
                if e.target not in active_nodes:
                    continue
                injected_bp: str | None = breakpoint_after.get(source)
                if injected_bp is not None:
                    # Route source -> breakpoint -> original target. The
                    # breakpoint is a single linear node, so it connects to the
                    # target with a plain edge (no join barrier needed here;
                    # other non-breakpoint predecessors of the same target are
                    # still merged below).
                    builder.add_edge(source, injected_bp)
                    builder.add_edge(injected_bp, e.target)
                    continue
                # Workers pointing back at a supervisor are plain edges (loop back)
                plain_by_target[e.target].append(source)

        for target, sources in plain_by_target.items():
            uniq = sorted(set(sources))
            if len(uniq) == 1:
                builder.add_edge(uniq[0], target)
            elif _is_supervisor(node_map[target]):
                # Workers loop back to a supervisor one at a time (Command
                # routing) — a join barrier here would deadlock. Individual
                # edges let any single worker re-trigger the supervisor.
                for source in uniq:
                    builder.add_edge(source, target)
            else:
                # Join barrier: wait for ALL predecessors
                builder.add_edge(uniq, target)

        compiled = builder.compile(checkpointer=checkpointer)
        logger.info(
            "compiled workflow '%s' nodes=%d edges=%d",
            dsl.name,
            len(dsl.nodes),
            len(dsl.edges),
        )
        return compiled

    # ------------------------------------------------------------------

    def _supervisor_finish_target(
        self,
        supervisor_id: str,
        out_edges: dict[str, list[EdgeSpec]],
        dsl: WorkflowDSL,
    ) -> str:
        """FINISH goes to an explicitly-connected end node, else the first end."""
        node_map = dsl.node_map()
        for e in out_edges.get(supervisor_id, []):
            if node_map[e.target].type == NodeType.END:
                return e.target
        for n in dsl.nodes:
            if n.type == NodeType.END:
                return n.id
        return END  # unreachable: validation requires an end node

    def _add_condition_edges(
        self,
        builder: StateGraph,
        src,
        edges: list[EdgeSpec],
        ctx: CompileContext,
    ) -> None:
        cfg = ConditionConfig.model_validate(src.config or {})
        path_map: dict[Hashable, str] = {}
        for e in edges:
            handle = e.source_handle or cfg.default_branch or "else"
            path_map[handle] = e.target

        # Ensure a default path exists
        default_key = cfg.default_branch or "else"
        if default_key not in path_map and path_map:
            path_map[default_key] = next(iter(path_map.values()))

        source_id = src.id
        emitter = ctx.emitter

        async def router(state: WorkflowState) -> str:
            template_ctx = build_context(
                inputs=state.get("inputs") or {},
                node_outputs=state.get("node_outputs") or {},
            )
            branch = pick_branch(cfg, template_ctx)
            if branch not in path_map:
                branch = default_key
            target = path_map.get(branch)
            if target:
                await emitter.emit(
                    EventType.EDGE_TAKEN,
                    node_id=source_id,
                    payload={"source": source_id, "target": target, "branch": branch},
                )
            return branch

        builder.add_conditional_edges(source_id, router, path_map)
