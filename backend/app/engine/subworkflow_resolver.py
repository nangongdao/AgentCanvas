"""Async pre-resolution of subworkflow references into a sync cache.

The workflow compiler is synchronous, but a subworkflow node references a
published version whose DSL lives in the database (an async read). Rather than
drive an async loader from inside the sync ``compile()``, the runner pre-walks
the DSL in an async context, loads every reachable subworkflow child DSL, and
hands the compiler a synchronous lookup against this cache. Cycle detection
happens here: a chain that revisits a ``workflow_id`` already on its ancestor
path is rejected before compilation begins.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app.schemas.dsl import NodeSpec, NodeType, SubworkflowConfig, WorkflowDSL

logger = logging.getLogger(__name__)


SubworkflowLoader = Callable[[str, str, frozenset[str]], Awaitable[WorkflowDSL | None]]
SubworkflowLookup = Callable[[str, str], "WorkflowDSL | None"]


class SubworkflowCycleError(ValueError):
    """Raised when a subworkflow chain revisits a workflow_id on its ancestor path."""


class SubworkflowCache:
    """Synchronous lookup over DSLs preloaded by :func:`resolve_subworkflows`."""

    def __init__(self, entries: dict[tuple[str, str], WorkflowDSL]) -> None:
        self._entries = dict(entries)

    def lookup(self, workflow_id: str, version_id: str) -> WorkflowDSL | None:
        return self._entries.get((workflow_id, version_id))

    def resolved_workflows(self) -> tuple[WorkflowDSL, ...]:
        """Return every recursively resolved child DSL for runtime inspection."""
        return tuple(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    def __bool__(self) -> bool:
        return bool(self._entries)


def _subworkflow_nodes(dsl: WorkflowDSL) -> list[tuple[NodeSpec, SubworkflowConfig]]:
    out: list[tuple[NodeSpec, SubworkflowConfig]] = []
    for node in dsl.nodes:
        if node.type != NodeType.SUBWORKFLOW and node.type != "subworkflow":
            continue
        cfg = SubworkflowConfig.model_validate(node.config or {})
        out.append((node, cfg))
    return out


async def resolve_subworkflows(
    dsl: WorkflowDSL,
    loader: SubworkflowLoader,
    *,
    ancestors: frozenset[str] = frozenset(),
) -> SubworkflowCache:
    """Recursively load every subworkflow child DSL reachable from ``dsl``.

    ``ancestors`` carries the workflow_ids on the current reference path so a
    cycle (A → B → A) is rejected at the point the revisiting edge is walked.
    """
    entries: dict[tuple[str, str], WorkflowDSL] = {}

    async def walk(current: WorkflowDSL, path: frozenset[str]) -> None:
        for _node, cfg in _subworkflow_nodes(current):
            if cfg.workflow_id in path:
                chain = " -> ".join(sorted(path | {cfg.workflow_id}))
                raise SubworkflowCycleError(
                    f"cyclic subworkflow reference detected: {chain}"
                )
            key = (cfg.workflow_id, cfg.version_id)
            if key in entries:
                continue
            child = await loader(cfg.workflow_id, cfg.version_id, path | {cfg.workflow_id})
            if child is None:
                # Missing references surface as a compile-time ValueError on the
                # node, not a silent None output. Keep walking siblings.
                logger.warning(
                    "subworkflow loader returned no DSL for workflow=%s version=%s",
                    cfg.workflow_id,
                    cfg.version_id,
                )
                continue
            entries[key] = child
            await walk(child, path | {cfg.workflow_id})

    await walk(dsl, ancestors)
    return SubworkflowCache(entries)


__all__ = [
    "SubworkflowCache",
    "SubworkflowCycleError",
    "SubworkflowLoader",
    "SubworkflowLookup",
    "resolve_subworkflows",
]
