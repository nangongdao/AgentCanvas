"""Deterministic three-way merge for workflow names and DSL snapshots."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

_MISSING = object()
type StructuralPath = tuple[str, ...]
_KEYED_LISTS: dict[StructuralPath, str] = {
    ("dsl", "nodes"): "id",
    ("dsl", "edges"): "id",
    ("dsl", "variables"): "name",
}


@dataclass(frozen=True)
class MergeValue:
    present: bool
    value: Any = None


@dataclass(frozen=True)
class WorkflowMergeConflict:
    path: str
    base: MergeValue
    local: MergeValue
    remote: MergeValue


@dataclass(frozen=True)
class WorkflowMergeResult:
    name: str
    dsl: dict[str, Any]
    conflicts: tuple[WorkflowMergeConflict, ...]


def merge_workflow_snapshots(
    *,
    base_name: str,
    base_dsl: dict[str, Any],
    local_name: str,
    local_dsl: dict[str, Any],
    remote_name: str,
    remote_dsl: dict[str, Any],
) -> WorkflowMergeResult:
    """Merge local and remote changes relative to one immutable base snapshot."""
    conflicts: list[WorkflowMergeConflict] = []
    name = _merge_value(base_name, local_name, remote_name, ("name",), conflicts)
    dsl = _merge_value(base_dsl, local_dsl, remote_dsl, ("dsl",), conflicts)
    if not isinstance(name, str) or not isinstance(dsl, dict):
        raise TypeError("workflow merge produced an invalid root value")
    return WorkflowMergeResult(name=name, dsl=dsl, conflicts=tuple(conflicts))


def _merge_value(
    base: Any,
    local: Any,
    remote: Any,
    path: StructuralPath,
    conflicts: list[WorkflowMergeConflict],
) -> Any:
    if _equal(local, remote):
        return _copy(local)
    if _equal(local, base):
        return _copy(remote)
    if _equal(remote, base):
        return _copy(local)

    if _MISSING in (base, local, remote):
        return _record_conflict(base, local, remote, path, conflicts)

    if isinstance(base, dict) and isinstance(local, dict) and isinstance(remote, dict):
        merged: dict[str, Any] = {}
        keys = sorted(set(base) | set(local) | set(remote))
        for key in keys:
            value = _merge_value(
                base.get(key, _MISSING),
                local.get(key, _MISSING),
                remote.get(key, _MISSING),
                (*path, key),
                conflicts,
            )
            if value is not _MISSING:
                merged[key] = value
        return merged

    list_key = _KEYED_LISTS.get(path)
    if (
        list_key is not None
        and isinstance(base, list)
        and isinstance(local, list)
        and isinstance(remote, list)
    ):
        return _merge_keyed_list(base, local, remote, path, list_key, conflicts)

    return _record_conflict(base, local, remote, path, conflicts)


def _merge_keyed_list(
    base: list[Any],
    local: list[Any],
    remote: list[Any],
    path: StructuralPath,
    key: str,
    conflicts: list[WorkflowMergeConflict],
) -> list[Any]:
    base_items = _index_items(base, key)
    local_items = _index_items(local, key)
    remote_items = _index_items(remote, key)
    if base_items is None or local_items is None or remote_items is None:
        return _record_conflict(base, local, remote, path, conflicts)

    merged_by_id: dict[str, Any] = {}
    all_ids = set(base_items) | set(local_items) | set(remote_items)
    for item_id in sorted(all_ids):
        base_item = base_items.get(item_id, _MISSING)
        local_item = local_items.get(item_id, _MISSING)
        remote_item = remote_items.get(item_id, _MISSING)
        item_path = (*path, f"[{key}={item_id}]")
        if base_item is _MISSING and local_item is not _MISSING and remote_item is not _MISSING:
            if _equal(local_item, remote_item):
                merged_by_id[item_id] = _copy(local_item)
            else:
                merged_by_id[item_id] = _record_conflict(
                    base_item,
                    local_item,
                    remote_item,
                    item_path,
                    conflicts,
                )
            continue
        value = _merge_value(
            base_item,
            local_item,
            remote_item,
            item_path,
            conflicts,
        )
        if value is not _MISSING:
            merged_by_id[item_id] = value

    order = [str(item[key]) for item in remote]
    order.extend(str(item[key]) for item in local if str(item[key]) not in remote_items)
    return [merged_by_id[item_id] for item_id in order if item_id in merged_by_id]


def _index_items(items: list[Any], key: str) -> dict[str, dict[str, Any]] | None:
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get(key), str):
            return None
        item_id = str(item[key])
        if item_id in indexed:
            return None
        indexed[item_id] = item
    return indexed


def _record_conflict(
    base: Any,
    local: Any,
    remote: Any,
    path: StructuralPath,
    conflicts: list[WorkflowMergeConflict],
) -> Any:
    conflicts.append(
        WorkflowMergeConflict(
            path=_display_path(path),
            base=_view(base),
            local=_view(local),
            remote=_view(remote),
        )
    )
    return _copy(remote if remote is not _MISSING else local)


def _view(value: Any) -> MergeValue:
    return (
        MergeValue(present=False)
        if value is _MISSING
        else MergeValue(present=True, value=_copy(value))
    )


def _display_path(path: StructuralPath) -> str:
    text = path[0]
    for segment in path[1:]:
        text += segment if segment.startswith("[") else f".{segment}"
    return text


def _copy(value: Any) -> Any:
    return _MISSING if value is _MISSING else deepcopy(value)


def _equal(left: Any, right: Any) -> bool:
    if left is _MISSING or right is _MISSING:
        return left is right
    return bool(left == right)


__all__ = [
    "MergeValue",
    "WorkflowMergeConflict",
    "WorkflowMergeResult",
    "merge_workflow_snapshots",
]
