"""D2 Phase 4 A/B comparison and cost-accounting acceptance tests."""

from __future__ import annotations

import time
from copy import deepcopy
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.dsl import WorkflowDSL
from app.services.evaluation_costs import estimate_execution_cost
from tests.test_auth import (
    EDITOR_TOKEN,
    VIEWER_TOKEN,
    auth_settings,
    bearer,
    workflow_body,
)


def _wait_comparison(client: TestClient, comparison_id: str) -> dict:
    deadline = time.monotonic() + 15
    latest: dict = {}
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/evaluation-comparisons/{comparison_id}",
            headers=bearer(VIEWER_TOKEN),
        )
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest["status"] in {"completed", "failed"}:
            return latest
        time.sleep(0.05)
    raise AssertionError(f"comparison did not finish: {latest}")


def _target_workflow() -> dict:
    body = workflow_body()
    body["name"] = "A/B target"
    body["dsl"]["name"] = body["name"]
    body["dsl"]["variables"] = [
        {"name": "question", "type": "string", "required": True}
    ]
    body["dsl"]["nodes"][1]["config"] = {
        "output_template": {"answer": "{{input.question}}"}
    }
    return body


def test_cost_estimate_has_defined_precision_and_unknown_semantics() -> None:
    dsl = WorkflowDSL.model_validate(
        {
            "version": "1.0",
            "name": "Cost probe",
            "nodes": [
                {"id": "start", "type": "start", "position": {"x": 0, "y": 0}},
                {
                    "id": "agent",
                    "type": "agent",
                    "position": {"x": 100, "y": 0},
                    "config": {"model_config_id": "priced"},
                },
                {"id": "end", "type": "end", "position": {"x": 200, "y": 0}},
            ],
            "edges": [
                {"id": "one", "source": "start", "target": "agent"},
                {"id": "two", "source": "agent", "target": "end"},
            ],
        }
    )
    events = [
        SimpleNamespace(
            node_id="agent", event_type="node_started", payload_json={}
        ),
        SimpleNamespace(
            node_id="agent",
            event_type="node_finished",
            payload_json={
                "output": {
                    "meta": {
                        "usage": {"prompt": 125, "completion": 25, "total": 150}
                    }
                }
            },
        ),
    ]
    priced = SimpleNamespace(
        prompt_price_per_million_usd="0.5",
        completion_price_per_million_usd="1.5",
        pricing_version="catalog-2026-08",
    )
    estimate = estimate_execution_cost(events, dsl=dsl, model_configs={"priced": priced})
    assert estimate.cost_known is True
    assert estimate.estimated_cost_usd == "0.000100000000"
    assert estimate.cost_error_bound_usd == "0.0000000000005"
    assert estimate.price_versions == ("catalog-2026-08",)
    assert (estimate.prompt_tokens, estimate.completion_tokens, estimate.total_tokens) == (
        125,
        25,
        150,
    )

    unknown = estimate_execution_cost(events, dsl=dsl, model_configs={})
    assert unknown.cost_known is False
    assert unknown.estimated_cost_usd is None
    assert unknown.cost_error_bound_usd is None


def test_cost_estimate_prices_iteration_item_agent_events() -> None:
    nested = {
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {
                "id": "agent",
                "type": "agent",
                "config": {"model_config_id": "priced"},
            },
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "one", "source": "start", "target": "agent"},
            {"id": "two", "source": "agent", "target": "end"},
        ],
    }
    dsl = WorkflowDSL.model_validate(
        {
            "name": "Iteration cost",
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {
                    "id": "each",
                    "type": "iteration",
                    "config": {"items": "{{input.items}}", "subgraph": nested},
                },
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "outer-one", "source": "start", "target": "each"},
                {"id": "outer-two", "source": "each", "target": "end"},
            ],
        }
    )
    events = [
        SimpleNamespace(node_id="each[0].agent", event_type="node_started", payload_json={}),
        SimpleNamespace(
            node_id="each[0].agent",
            event_type="node_finished",
            payload_json={
                "output": {
                    "meta": {"usage": {"prompt": 10, "completion": 5, "total": 15}}
                }
            },
        ),
        SimpleNamespace(node_id="each[1].agent", event_type="node_started", payload_json={}),
        SimpleNamespace(
            node_id="each[1].agent",
            event_type="node_finished",
            payload_json={
                "output": {
                    "meta": {"usage": {"prompt": 20, "completion": 10, "total": 30}}
                }
            },
        ),
    ]
    priced = SimpleNamespace(
        prompt_price_per_million_usd="1",
        completion_price_per_million_usd="2",
        pricing_version="iteration-price-v1",
    )

    estimate = estimate_execution_cost(events, dsl=dsl, model_configs={"priced": priced})

    assert (estimate.prompt_tokens, estimate.completion_tokens, estimate.total_tokens) == (
        30,
        15,
        45,
    )
    assert estimate.estimated_cost_usd == "0.000060000000"


def test_cost_estimate_uses_structured_iteration_path_for_bracketed_node_ids() -> None:
    nested = {
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {
                "id": "agent[0]",
                "type": "agent",
                "config": {"model_config_id": "priced"},
            },
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "one", "source": "start", "target": "agent[0]"},
            {"id": "two", "source": "agent[0]", "target": "end"},
        ],
    }
    dsl = WorkflowDSL.model_validate(
        {
            "name": "Structured iteration path",
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {
                    "id": "each",
                    "type": "iteration",
                    "config": {"items": "{{input.items}}", "subgraph": nested},
                },
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "outer-one", "source": "start", "target": "each"},
                {"id": "outer-two", "source": "each", "target": "end"},
            ],
        }
    )
    events = [
        SimpleNamespace(
            node_id="each[2].agent[0]",
            event_type="node_started",
            payload_json={"node_path": "each.agent[0]"},
        ),
        SimpleNamespace(
            node_id="each[2].agent[0]",
            event_type="node_finished",
            payload_json={
                "node_path": "each.agent[0]",
                "output": {
                    "meta": {"usage": {"prompt": 10, "completion": 5, "total": 15}}
                },
            },
        ),
    ]
    priced = SimpleNamespace(
        prompt_price_per_million_usd="1",
        completion_price_per_million_usd="2",
        pricing_version="structured-path-v1",
    )

    estimate = estimate_execution_cost(events, dsl=dsl, model_configs={"priced": priced})

    assert (estimate.prompt_tokens, estimate.completion_tokens, estimate.total_tokens) == (
        10,
        5,
        15,
    )
    assert estimate.estimated_cost_usd == "0.000020000000"


def test_cost_estimate_keeps_dotted_outer_and_nested_paths_distinct() -> None:
    dsl = WorkflowDSL.model_validate(
        {
            "name": "Dotted iteration paths",
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {
                    "id": "each",
                    "type": "iteration",
                    "config": {
                        "items": "{{input.items}}",
                        "subgraph": {
                            "nodes": [
                                {"id": "item_start", "type": "start", "config": {}},
                                {
                                    "id": "work",
                                    "type": "agent",
                                    "config": {"model_config_id": "nested"},
                                },
                                {"id": "item_end", "type": "end", "config": {}},
                            ],
                            "edges": [
                                {"id": "one", "source": "item_start", "target": "work"},
                                {"id": "two", "source": "work", "target": "item_end"},
                            ],
                        },
                    },
                },
                {
                    "id": "each.work",
                    "type": "agent",
                    "config": {"model_config_id": "outer"},
                },
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "one", "source": "start", "target": "each"},
                {"id": "two", "source": "each", "target": "each.work"},
                {"id": "three", "source": "each.work", "target": "end"},
            ],
        }
    )
    events = [
        SimpleNamespace(
            node_id="each[0].work",
            event_type="node_started",
            payload_json={"node_path": "each.work", "node_path_segments": ["each", "work"]},
        ),
        SimpleNamespace(
            node_id="each[0].work",
            event_type="node_finished",
            payload_json={
                "node_path": "each.work",
                "node_path_segments": ["each", "work"],
                "output": {"meta": {"usage": {"prompt": 1, "completion": 0, "total": 1}}},
            },
        ),
        SimpleNamespace(
            node_id="each.work",
            event_type="node_started",
            payload_json={"node_path_segments": ["each.work"]},
        ),
        SimpleNamespace(
            node_id="each.work",
            event_type="node_finished",
            payload_json={
                "node_path_segments": ["each.work"],
                "output": {"meta": {"usage": {"prompt": 1, "completion": 0, "total": 1}}}
            },
        ),
    ]
    def model(rate: int) -> SimpleNamespace:
        return SimpleNamespace(
            prompt_price_per_million_usd=str(rate),
            completion_price_per_million_usd="0",
            pricing_version=f"dotted-{rate}",
        )

    estimate = estimate_execution_cost(
        events,
        dsl=dsl,
        model_configs={"nested": model(1), "outer": model(10)},
    )

    assert estimate.estimated_cost_usd == "0.000011000000"


def test_published_versions_compare_over_one_immutable_dataset(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        editor = bearer(EDITOR_TOKEN)
        dataset = client.post(
            "/api/evaluation-datasets",
            headers=editor,
            json={
                "name": "A/B corpus",
                "cases": [
                    {
                        "id": "alpha",
                        "name": "Alpha",
                        "inputs": {"question": "alpha"},
                        "expected": {"answer": "alpha"},
                    }
                ],
            },
        ).json()
        workflow = client.post(
            "/api/workflows", headers=editor, json=_target_workflow()
        ).json()
        version_a = client.post(
            f"/api/workflows/{workflow['id']}/publish", headers=editor
        ).json()

        changed_dsl = deepcopy(workflow["dsl"])
        changed_dsl["nodes"][1]["config"]["output_template"] = {
            "changed": "{{input.question}}"
        }
        changed = client.put(
            f"/api/workflows/{workflow['id']}",
            headers=editor,
            json={"dsl": changed_dsl, "version": workflow["version"]},
        )
        assert changed.status_code == 200, changed.text
        version_b = client.post(
            f"/api/workflows/{workflow['id']}/publish", headers=editor
        ).json()

        started = client.post(
            "/api/evaluation-comparisons",
            headers=editor,
            json={
                "dataset_version_id": dataset["versions"][0]["id"],
                "workflow_version_a_id": version_a["id"],
                "workflow_version_b_id": version_b["id"],
                "evaluator_type": "exact",
            },
        )
        assert started.status_code == 201, started.text
        report = _wait_comparison(client, started.json()["id"])
        assert report["status"] == "completed", report
        assert report["summary"]["quality_winner"] == "variant_a"
        assert report["summary"]["delta_b_minus_a"]["pass_rate"] == -1.0
        assert (
            report["summary"]["delta_b_minus_a"]["estimated_cost_usd"]
            == "0.000000000000"
        )
        assert report["variant_a"]["workflow_version_id"] == version_a["id"]
        assert report["variant_b"]["workflow_version_id"] == version_b["id"]
        assert report["variant_a"]["summary"]["pass_rate"] == 1.0
        assert report["variant_b"]["summary"]["pass_rate"] == 0.0
        assert report["cases"][0]["inputs"] == {"question": "alpha"}
        assert report["cases"][0]["variant_a"]["execution_id"]
        assert report["cases"][0]["variant_b"]["execution_id"]

        listed = client.get(
            "/api/evaluation-comparisons", headers=bearer(VIEWER_TOKEN)
        )
        assert listed.status_code == 200
        assert listed.json()["items"][0]["id"] == report["id"]

        same_version = client.post(
            "/api/evaluation-comparisons",
            headers=editor,
            json={
                "dataset_version_id": dataset["versions"][0]["id"],
                "workflow_version_a_id": version_a["id"],
                "workflow_version_b_id": version_a["id"],
                "evaluator_type": "exact",
            },
        )
        assert same_version.status_code == 422
