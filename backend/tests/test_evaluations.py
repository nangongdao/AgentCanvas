"""D2 versioned datasets and evaluation-run acceptance tests."""

from __future__ import annotations

import time
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.evaluators import (
    evaluate_deterministic,
    evaluate_with_llm,
    resolve_actual,
)
from tests.conftest import FakeChatProvider
from tests.test_auth import (
    EDITOR_TOKEN,
    VIEWER_TOKEN,
    auth_settings,
    bearer,
    workflow_body,
)


def _wait_run(client: TestClient, run_id: str) -> dict:
    deadline = time.monotonic() + 15
    latest: dict = {}
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/evaluation-runs/{run_id}", headers=bearer(VIEWER_TOKEN)
        )
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest["status"] in {"completed", "failed", "cancelled"}:
            return latest
        time.sleep(0.05)
    raise AssertionError(f"evaluation did not finish: {latest}")


def _evaluation_workflow() -> dict:
    body = workflow_body()
    body["name"] = "Evaluation target"
    body["dsl"]["name"] = body["name"]
    body["dsl"]["variables"] = [
        {"name": "question", "type": "string", "required": True}
    ]
    body["dsl"]["nodes"][1]["config"] = {
        "output_template": {"answer": "{{input.question}}"}
    }
    return body


def test_deterministic_evaluators_and_output_paths() -> None:
    output = {"answer": {"text": "Hello World", "count": 2}}
    assert resolve_actual(output, "/answer/text") == "Hello World"
    assert resolve_actual(output, "answer.count") == 2
    assert evaluate_deterministic("exact", actual=2, expected=2).passed
    assert evaluate_deterministic(
        "contains", actual="Hello World", expected="world", case_sensitive=False
    ).passed
    assert evaluate_deterministic(
        "contains", actual="alpha beta", expected=["alpha", "beta"]
    ).passed
    schema_result = evaluate_deterministic(
        "json_schema",
        actual={"answer": "ok"},
        expected={
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        },
    )
    assert schema_result.passed
    mismatch = evaluate_deterministic(
        "json_schema", actual={"answer": 2}, expected={"type": "string"}
    )
    assert not mismatch.passed
    assert "schema mismatch" in mismatch.message


async def test_llm_judge_requires_strict_scored_json() -> None:
    provider = FakeChatProvider(['{"score":0.8,"rationale":"meets the rubric"}'])
    outcome = await evaluate_with_llm(
        provider,
        actual={"answer": "yes"},
        expected={"answer": "yes"},
        rubric="Correct answer",
        threshold=0.75,
    )
    assert outcome.passed
    assert outcome.score == 0.8
    assert outcome.message == "meets the rubric"

    with pytest.raises(ValueError, match="invalid JSON"):
        await evaluate_with_llm(
            FakeChatProvider(["not-json"]),
            actual="value",
            expected="value",
            rubric="Exactness",
            threshold=0.5,
        )


def test_versioned_dataset_and_exact_evaluation_over_http(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        editor = bearer(EDITOR_TOKEN)
        viewer = bearer(VIEWER_TOKEN)
        created_dataset = client.post(
            "/api/evaluation-datasets",
            headers=editor,
            json={
                "name": "Regression prompts",
                "description": "Fixed exact-match corpus",
                "cases": [
                    {
                        "id": "alpha",
                        "name": "Alpha",
                        "inputs": {"question": "alpha"},
                        "expected": {"answer": "alpha"},
                    },
                    {
                        "id": "beta",
                        "name": "Beta",
                        "inputs": {"question": "beta"},
                        "expected": {"answer": "beta"},
                    },
                ],
            },
        )
        assert created_dataset.status_code == 201, created_dataset.text
        dataset = created_dataset.json()
        dataset_version_1 = dataset["versions"][0]

        updated = client.put(
            f"/api/evaluation-datasets/{dataset['id']}",
            headers=editor,
            json={
                "name": "Regression prompts",
                "description": "Second immutable snapshot",
                "change_summary": "Add gamma",
                "expected_version": 1,
                "cases": [
                    *dataset_version_1["cases"],
                    {
                        "id": "gamma",
                        "name": "Gamma",
                        "inputs": {"question": "gamma"},
                        "expected": {"answer": "gamma"},
                    },
                ],
            },
        )
        assert updated.status_code == 200, updated.text
        assert [version["number"] for version in updated.json()["versions"]] == [2, 1]
        assert len(updated.json()["versions"][1]["cases"]) == 2

        stale = client.put(
            f"/api/evaluation-datasets/{dataset['id']}",
            headers=editor,
            json={
                "name": "Stale replacement",
                "description": "Must not overwrite version 2",
                "expected_version": 1,
                "cases": dataset_version_1["cases"],
            },
        )
        assert stale.status_code == 409, stale.text
        after_conflict = client.get(
            f"/api/evaluation-datasets/{dataset['id']}", headers=viewer
        ).json()
        assert after_conflict["current_version"] == 2
        assert len(after_conflict["versions"][0]["cases"]) == 3

        second_dataset = client.post(
            "/api/evaluation-datasets",
            headers=editor,
            json={
                "name": "Secondary corpus",
                "cases": [{"id": "only", "inputs": {}, "expected": {}}],
            },
        ).json()
        first_page = client.get(
            "/api/evaluation-datasets",
            headers=viewer,
            params={"limit": 1, "sort": "name", "order": "asc"},
        ).json()
        assert first_page["has_more"] is True
        assert first_page["next_cursor"]
        second_page = client.get(
            "/api/evaluation-datasets",
            headers=viewer,
            params={
                "limit": 1,
                "sort": "name",
                "order": "asc",
                "cursor": first_page["next_cursor"],
            },
        ).json()
        assert {first_page["items"][0]["id"], second_page["items"][0]["id"]} == {
            dataset["id"],
            second_dataset["id"],
        }

        workflow = client.post(
            "/api/workflows", headers=editor, json=_evaluation_workflow()
        ).json()
        published = client.post(
            f"/api/workflows/{workflow['id']}/publish", headers=editor
        )
        assert published.status_code == 200, published.text
        published_version = published.json()

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

        started = client.post(
            "/api/evaluation-runs",
            headers=editor,
            json={
                "dataset_version_id": dataset_version_1["id"],
                "workflow_version_id": published_version["id"],
                "evaluator_type": "exact",
            },
        )
        assert started.status_code == 201, started.text
        report = _wait_run(client, started.json()["id"])
        assert report["status"] == "completed", report
        assert {
            "total": 2,
            "passed": 2,
            "failed": 0,
            "error": 0,
            "pass_rate": 1.0,
            "average_score": 1.0,
            "estimated_cost_usd": "0.000000000000",
            "cost_coverage": 1.0,
        }.items() <= report["summary"].items()
        assert all(case["execution_id"] for case in report["cases"])
        assert all(case["status"] == "passed" for case in report["cases"])
        for case in report["cases"]:
            execution = client.get(
                f"/api/executions/{case['execution_id']}", headers=viewer
            ).json()
            assert execution["workflow_version_id"] == published_version["id"]

        runs = client.get("/api/evaluation-runs", headers=viewer)
        assert runs.status_code == 200
        assert runs.json()["items"][0]["id"] == report["id"]
        assert (
            client.delete(f"/api/evaluation-datasets/{dataset['id']}", headers=editor).status_code
            == 409
        )
        assert (
            client.post(
                "/api/evaluation-runs",
                headers=viewer,
                json={
                    "dataset_version_id": dataset_version_1["id"],
                    "workflow_version_id": published_version["id"],
                    "evaluator_type": "exact",
                },
            ).status_code
            == 403
        )


def test_evaluation_policy_requires_published_version_and_explicit_judge_opt_in(
    tmp_path,
) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        editor = bearer(EDITOR_TOKEN)
        dataset = client.post(
            "/api/evaluation-datasets",
            headers=editor,
            json={
                "name": "Policy",
                "cases": [{"id": "one", "inputs": {}, "expected": {}}],
            },
        ).json()
        workflow = client.post(
            "/api/workflows", headers=editor, json=workflow_body()
        ).json()
        versions = client.get(
            f"/api/workflows/{workflow['id']}/versions", headers=bearer(VIEWER_TOKEN)
        ).json()
        draft_run = client.post(
            "/api/evaluation-runs",
            headers=editor,
            json={
                "dataset_version_id": dataset["versions"][0]["id"],
                "workflow_version_id": versions[0]["id"],
                "evaluator_type": "exact",
            },
        )
        assert draft_run.status_code == 409
        assert "published" in draft_run.text

        tool_body = workflow_body()
        tool_body["name"] = "Side effect target"
        tool_body["dsl"]["name"] = tool_body["name"]
        tool_body["dsl"]["nodes"] = [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}},
            {
                "id": "tool",
                "type": "tool",
                "position": {"x": 200, "y": 0},
                "config": {"server_id": "demo", "tool_name": "mutate"},
            },
            {"id": "end", "type": "end", "position": {"x": 400, "y": 0}},
        ]
        tool_body["dsl"]["edges"] = [
            {"id": "one", "source": "start", "target": "tool"},
            {"id": "two", "source": "tool", "target": "end"},
        ]
        tool_workflow = client.post(
            "/api/workflows", headers=editor, json=tool_body
        ).json()
        tool_version = client.post(
            f"/api/workflows/{tool_workflow['id']}/publish", headers=editor
        ).json()
        blocked = client.post(
            "/api/evaluation-runs",
            headers=editor,
            json={
                "dataset_version_id": dataset["versions"][0]["id"],
                "workflow_version_id": tool_version["id"],
                "evaluator_type": "exact",
            },
        )
        assert blocked.status_code == 409
        assert "explicit opt-in required" in blocked.text

        judge = client.post(
            "/api/evaluation-runs",
            headers=editor,
            json={
                "dataset_version_id": dataset["versions"][0]["id"],
                "workflow_version_id": versions[0]["id"],
                "evaluator_type": "llm_judge",
                "model_config_id": "default",
                "rubric": "Correctness",
            },
        )
        assert judge.status_code == 422
        assert "explicit opt-in" in judge.text
