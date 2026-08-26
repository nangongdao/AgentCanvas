"""Semantic summaries for two workflow DSL snapshots."""

from __future__ import annotations

from typing import Any


def _items_by_id(items: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        return {}
    return {
        str(item["id"]): item
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _item_diff(
    base: dict[str, dict[str, Any]], target: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str], list[str]]:
    base_ids = set(base)
    target_ids = set(target)
    added = sorted(target_ids - base_ids)
    removed = sorted(base_ids - target_ids)
    changed = sorted(
        item_id for item_id in base_ids & target_ids if base[item_id] != target[item_id]
    )
    return added, removed, changed


def diff_workflow_dsl(
    base_id: str,
    base: dict[str, Any],
    target_id: str,
    target: dict[str, Any],
) -> dict[str, Any]:
    added_nodes, removed_nodes, changed_nodes = _item_diff(
        _items_by_id(base.get("nodes")), _items_by_id(target.get("nodes"))
    )
    added_edges, removed_edges, changed_edges = _item_diff(
        _items_by_id(base.get("edges")), _items_by_id(target.get("edges"))
    )
    return {
        "base_id": base_id,
        "target_id": target_id,
        "added_nodes": added_nodes,
        "removed_nodes": removed_nodes,
        "changed_nodes": changed_nodes,
        "added_edges": added_edges,
        "removed_edges": removed_edges,
        "changed_edges": changed_edges,
        "settings_changed": base.get("settings") != target.get("settings"),
        "variables_changed": base.get("variables") != target.get("variables"),
        "canvas_changed": base.get("canvas", {}) != target.get("canvas", {}),
    }


__all__ = ["diff_workflow_dsl"]
