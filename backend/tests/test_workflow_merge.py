"""Three-way workflow merge semantics and conflict paths."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.workflow_merge import merge_workflow_snapshots


def _dsl() -> dict[str, Any]:
    return {
        "version": "1.0",
        "name": "Base",
        "variables": [{"name": "query", "type": "string", "required": True}],
        "settings": {"timeout_seconds": 30, "recursion_limit": 50},
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "position": {"x": 0, "y": 0},
                "config": {},
            },
            {
                "id": "end",
                "type": "end",
                "name": "End",
                "position": {"x": 200, "y": 0},
                "config": {"output_template": {"answer": "base"}},
            },
        ],
        "edges": [{"id": "edge", "source": "start", "target": "end"}],
    }


def test_disjoint_nested_and_keyed_list_changes_merge() -> None:
    base = _dsl()
    local = deepcopy(base)
    remote = deepcopy(base)
    local["name"] = "Local"
    local["variables"][0]["required"] = False
    local["nodes"][1]["name"] = "Local end"
    local["nodes"][1]["config"]["output_template"]["answer"] = "local"
    local["edges"][0]["source_handle"] = "success"
    remote["settings"]["timeout_seconds"] = 60
    remote["nodes"][1]["position"]["x"] = 320
    remote["edges"][0]["target_handle"] = "input"

    result = merge_workflow_snapshots(
        base_name="Base",
        base_dsl=base,
        local_name="Local",
        local_dsl=local,
        remote_name="Base",
        remote_dsl=remote,
    )

    assert result.conflicts == ()
    assert result.name == "Local"
    assert result.dsl["name"] == "Local"
    assert result.dsl["settings"]["timeout_seconds"] == 60
    assert result.dsl["variables"][0]["required"] is False
    assert result.dsl["nodes"][1]["name"] == "Local end"
    assert result.dsl["nodes"][1]["config"]["output_template"]["answer"] == "local"
    assert result.dsl["nodes"][1]["position"]["x"] == 320
    assert result.dsl["edges"][0]["source_handle"] == "success"
    assert result.dsl["edges"][0]["target_handle"] == "input"


def test_same_leaf_change_reports_deterministic_conflict_path() -> None:
    base = _dsl()
    local = deepcopy(base)
    remote = deepcopy(base)
    local["nodes"][1]["position"]["x"] = 280
    remote["nodes"][1]["position"]["x"] = 360

    result = merge_workflow_snapshots(
        base_name="Base",
        base_dsl=base,
        local_name="Base",
        local_dsl=local,
        remote_name="Base",
        remote_dsl=remote,
    )

    assert [conflict.path for conflict in result.conflicts] == ["dsl.nodes[id=end].position.x"]
    conflict = result.conflicts[0]
    assert conflict.base.value == 200
    assert conflict.local.value == 280
    assert conflict.remote.value == 360
    assert result.dsl["nodes"][1]["position"]["x"] == 360


def test_delete_versus_modify_conflicts_without_losing_remote_preview() -> None:
    base = _dsl()
    local = deepcopy(base)
    remote = deepcopy(base)
    local["nodes"] = [local["nodes"][0]]
    remote["nodes"][1]["name"] = "Remote end"

    result = merge_workflow_snapshots(
        base_name="Base",
        base_dsl=base,
        local_name="Base",
        local_dsl=local,
        remote_name="Base",
        remote_dsl=remote,
    )

    assert [conflict.path for conflict in result.conflicts] == ["dsl.nodes[id=end]"]
    assert result.conflicts[0].local.present is False
    assert result.dsl["nodes"][1]["name"] == "Remote end"


def test_independent_additions_merge_but_same_identity_collision_conflicts() -> None:
    base = _dsl()
    local = deepcopy(base)
    remote = deepcopy(base)
    local["nodes"].append({"id": "local", "type": "end", "position": {"x": 1, "y": 1}})
    remote["nodes"].append({"id": "remote", "type": "end", "position": {"x": 2, "y": 2}})
    merged = merge_workflow_snapshots(
        base_name="Base",
        base_dsl=base,
        local_name="Base",
        local_dsl=local,
        remote_name="Base",
        remote_dsl=remote,
    )
    assert merged.conflicts == ()
    assert [node["id"] for node in merged.dsl["nodes"]] == [
        "start",
        "end",
        "remote",
        "local",
    ]

    local["nodes"][-1]["id"] = "collision"
    remote["nodes"][-1]["id"] = "collision"
    collided = merge_workflow_snapshots(
        base_name="Base",
        base_dsl=base,
        local_name="Base",
        local_dsl=local,
        remote_name="Base",
        remote_dsl=remote,
    )
    assert [conflict.path for conflict in collided.conflicts] == ["dsl.nodes[id=collision]"]
