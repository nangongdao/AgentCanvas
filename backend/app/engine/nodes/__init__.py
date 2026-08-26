"""Node registry package — import concrete executors for side-effect registration."""

from __future__ import annotations

from app.engine.nodes import agent as _agent  # noqa: F401
from app.engine.nodes import code as _code  # noqa: F401
from app.engine.nodes import condition as _condition  # noqa: F401
from app.engine.nodes import http_request as _http_request  # noqa: F401
from app.engine.nodes import human as _human  # noqa: F401
from app.engine.nodes import iteration as _iteration  # noqa: F401
from app.engine.nodes import rag as _rag  # noqa: F401
from app.engine.nodes import start_end as _start_end  # noqa: F401
from app.engine.nodes import subworkflow as _subworkflow  # noqa: F401
from app.engine.nodes import switch as _switch  # noqa: F401
from app.engine.nodes import tool as _tool  # noqa: F401
from app.engine.nodes.base import (
    NODE_REGISTRY,
    BaseNodeExecutor,
    CompileContext,
    LoopLimitExceeded,
    NodeFn,
    clear_external_executors,
    external_node_types,
    get_executor,
    instrument,
    register_executor_instance,
    register_node,
)


def list_node_types() -> list[dict]:
    """Export node type metadata + JSON Schema for the frontend SchemaForm."""
    return [NODE_REGISTRY[key].metadata() for key in sorted(NODE_REGISTRY)]


__all__ = [
    "NODE_REGISTRY",
    "BaseNodeExecutor",
    "CompileContext",
    "LoopLimitExceeded",
    "NodeFn",
    "clear_external_executors",
    "external_node_types",
    "get_executor",
    "instrument",
    "list_node_types",
    "register_executor_instance",
    "register_node",
]
