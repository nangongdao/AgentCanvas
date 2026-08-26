"""Versioned, deterministic migrations for persisted workflow DSL documents."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.schemas.dsl import WorkflowDSL

CURRENT_DSL_VERSION = "1.0"


class UnsupportedDSLVersion(ValueError):
    """Raised when no safe migration path exists for an imported DSL."""


def _migrate_0_9(raw: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(raw)
    migrated["version"] = CURRENT_DSL_VERSION
    if not migrated.get("name") and migrated.get("display_name"):
        migrated["name"] = migrated.pop("display_name")

    settings = dict(migrated.get("settings") or {})
    settings.setdefault("max_loop_iterations", 20)
    settings.setdefault("timeout_seconds", 300)
    settings.setdefault("recursion_limit", 50)
    migrated["settings"] = settings
    migrated.setdefault("variables", [])

    nodes: list[dict[str, Any]] = []
    for source in migrated.get("nodes") or []:
        node = dict(source)
        if "type" not in node and "kind" in node:
            node["type"] = node.pop("kind")
        if "position" not in node:
            node["position"] = {
                "x": node.pop("x", 0),
                "y": node.pop("y", 0),
            }
        node.setdefault("config", {})
        nodes.append(node)
    migrated["nodes"] = nodes

    edges: list[dict[str, Any]] = []
    for source in migrated.get("edges") or []:
        edge = dict(source)
        if "source_handle" not in edge and "sourceHandle" in edge:
            edge["source_handle"] = edge.pop("sourceHandle")
        if "target_handle" not in edge and "targetHandle" in edge:
            edge["target_handle"] = edge.pop("targetHandle")
        edges.append(edge)
    migrated["edges"] = edges
    return migrated


def normalize_workflow_dsl(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate a supported document and return canonical current-version JSON."""
    version = str(raw.get("version") or "0.9")
    if version == "0.9":
        candidate = _migrate_0_9(raw)
    elif version == CURRENT_DSL_VERSION:
        candidate = deepcopy(raw)
    else:
        raise UnsupportedDSLVersion(
            f"unsupported workflow DSL version {version!r}; "
            f"current version is {CURRENT_DSL_VERSION}"
        )
    return WorkflowDSL.model_validate(candidate).model_dump(mode="json")


__all__ = [
    "CURRENT_DSL_VERSION",
    "UnsupportedDSLVersion",
    "normalize_workflow_dsl",
]
