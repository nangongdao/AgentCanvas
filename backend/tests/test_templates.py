"""D1 official/user workflow template library tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.base import Base, create_engine, create_session_factory
from app.db.repositories import WorkflowTemplateRepo
from app.main import create_app
from app.services.template_renderer import (
    TemplateParameterError,
    render_template_dsl,
    resolve_template_parameters,
)
from tests.test_auth import (
    EDITOR_TOKEN,
    VIEWER_TOKEN,
    auth_settings,
    bearer,
    workflow_body,
)


def test_template_parameter_types_and_recursive_rendering() -> None:
    definitions = [
        {"name": "title", "type": "string", "required": True, "default": None},
        {"name": "retries", "type": "number", "default": 2},
        {"name": "enabled", "type": "boolean", "default": False},
    ]
    resolved = resolve_template_parameters(definitions, {"title": "Review"})
    rendered = render_template_dsl(
        {
            "name": "${parameter.title}",
            "settings": {"retries": "${parameter.retries}"},
            "label": "${parameter.title} flow",
            "enabled": "${parameter.enabled}",
        },
        resolved,
    )
    assert rendered == {
        "name": "Review",
        "settings": {"retries": 2.0},
        "label": "Review flow",
        "enabled": False,
    }

    try:
        resolve_template_parameters(definitions, {})
    except TemplateParameterError as exc:
        assert "required parameter 'title'" in str(exc)
    else:
        raise AssertionError("missing required template parameter was accepted")

    with pytest.raises(TemplateParameterError, match="required parameter 'title'"):
        resolve_template_parameters(definitions, {"title": "   "})


@pytest.mark.asyncio
async def test_template_tag_filter_applies_before_result_limit(tmp_path) -> None:
    engine = create_engine(Settings(data_dir=tmp_path))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            repo = WorkflowTemplateRepo(session)
            matching = await repo.create(
                name="Matching",
                description="",
                category="test",
                tags=["target"],
                parameters=[],
                dsl={},
            )
            matching.updated_at = datetime.now(UTC) - timedelta(days=1)
            nonmatching = await repo.create(
                name="Newer non-match",
                description="",
                category="test",
                tags=["other"],
                parameters=[],
                dsl={},
            )
            nonmatching.updated_at = datetime.now(UTC)
            await session.commit()

            rows = await repo.search(tag="TARGET", limit=1)
            assert [row.id for row in rows] == [matching.id]
    finally:
        await engine.dispose()


def test_official_and_user_template_lifecycle_over_http(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        viewer = bearer(VIEWER_TOKEN)
        editor = bearer(EDITOR_TOKEN)

        official = client.get("/api/workflow-templates", headers=viewer, params={"official": True})
        assert official.status_code == 200, official.text
        assert len(official.json()) == 8
        assert all(row["is_official"] for row in official.json())
        iteration = next(row for row in official.json() if row["id"] == "official-iteration")
        assert iteration["category"] == "data-processing"
        assert {"iteration", "batch"}.issubset(iteration["tags"])
        http_template = next(row for row in official.json() if row["id"] == "official-http")
        assert http_template["category"] == "integration"
        assert {"http", "outbound"}.issubset(http_template["tags"])
        code_template = next(row for row in official.json() if row["id"] == "official-code")
        assert code_template["category"] == "data-processing"
        assert {"code", "sandbox"}.issubset(code_template["tags"])
        switch_template = next(row for row in official.json() if row["id"] == "official-switch")
        assert switch_template["category"] == "routing"
        assert {"switch", "routing"}.issubset(switch_template["tags"])
        sub_template = next(row for row in official.json() if row["id"] == "official-subworkflow")
        assert sub_template["category"] == "orchestration"
        assert {"subworkflow", "orchestration"}.issubset(sub_template["tags"])

        searched = client.get(
            "/api/workflow-templates",
            headers=viewer,
            params={"search": "linear", "tag": "starter"},
        )
        assert searched.status_code == 200, searched.text
        assert [row["id"] for row in searched.json()] == ["official-linear"]

        instantiated = client.post(
            "/api/workflow-templates/official-linear/instantiate",
            headers=editor,
            json={
                "name": "Parameterized assistant",
                "parameters": {"system_prompt": "Answer with verified facts."},
            },
        )
        assert instantiated.status_code == 201, instantiated.text
        generated = instantiated.json()
        assert generated["dsl"]["name"] == "Parameterized assistant"
        assert generated["dsl"]["nodes"][1]["config"]["system_prompt"] == (
            "Answer with verified facts."
        )

        iteration_workflow = client.post(
            "/api/workflow-templates/official-iteration/instantiate",
            headers=editor,
            json={"name": "Batch processor"},
        )
        assert iteration_workflow.status_code == 201, iteration_workflow.text
        iteration_dsl = iteration_workflow.json()["dsl"]
        assert iteration_dsl["name"] == "Batch processor"
        assert iteration_dsl["variables"] == [
            {"name": "items", "type": "array", "required": True, "default": None}
        ]
        iteration_node = next(node for node in iteration_dsl["nodes"] if node["type"] == "iteration")
        assert iteration_node["config"]["failure_strategy"] == "collect_error"

        unknown = client.post(
            "/api/workflow-templates/official-linear/instantiate",
            headers=editor,
            json={"parameters": {"unknown": "value"}},
        )
        assert unknown.status_code == 422

        source = client.post("/api/workflows", headers=editor, json=workflow_body()).json()
        version = client.get(f"/api/workflows/{source['id']}/versions", headers=viewer).json()[0]
        user_template = client.post(
            "/api/workflow-templates",
            headers=editor,
            json={
                "name": "Team handoff",
                "description": "A reusable team flow",
                "category": "team",
                "tags": ["handoff", "review", "handoff"],
                "workflow_id": source["id"],
                "version_id": version["id"],
            },
        )
        assert user_template.status_code == 201, user_template.text
        template = user_template.json()
        assert template["tags"] == ["handoff", "review"]
        assert template["is_official"] is False

        tagged = client.get(
            "/api/workflow-templates", headers=viewer, params={"tag": "REVIEW"}
        ).json()
        assert [row["id"] for row in tagged] == [template["id"]]
        assert (
            client.post(
                "/api/workflow-templates",
                headers=viewer,
                json={"name": "Denied", "workflow_id": source["id"]},
            ).status_code
            == 403
        )
        assert (
            client.delete("/api/workflow-templates/official-linear", headers=editor).status_code
            == 403
        )
        deleted = client.delete(f"/api/workflow-templates/{template['id']}", headers=editor)
        assert deleted.status_code == 204, deleted.text
