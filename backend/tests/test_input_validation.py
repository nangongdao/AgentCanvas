"""Execution-input contract tests."""

from __future__ import annotations

import pytest

from app.engine.input_validation import WorkflowInputError, validate_execution_inputs
from app.schemas.dsl import WorkflowDSL


def workflow(*, variables: list[dict] | None = None, start_fields: list[dict] | None = None):
    return WorkflowDSL.model_validate(
        {
            "name": "Inputs",
            "variables": variables or [],
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "config": {"input_schema": start_fields or []},
                },
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [{"id": "edge", "source": "start", "target": "end"}],
        }
    )


def test_legacy_workflow_without_declarations_accepts_arbitrary_inputs() -> None:
    dsl = workflow()
    assert validate_execution_inputs(dsl, {"legacy": [1, 2]}) == {"legacy": [1, 2]}


def test_defaults_and_start_fields_are_applied() -> None:
    dsl = workflow(
        variables=[
            {"name": "query", "type": "string", "required": True},
            {"name": "limit", "type": "number", "default": 3},
        ],
        start_fields=[
            {"name": "approved", "type": "boolean", "required": True},
            {"name": "query", "type": "number", "required": False},
        ],
    )

    validated = validate_execution_inputs(dsl, {"query": "hello", "approved": False})

    assert validated == {"query": "hello", "approved": False, "limit": 3}


def test_required_unknown_and_type_errors_are_structured() -> None:
    dsl = workflow(
        variables=[
            {"name": "query", "type": "string", "required": True},
            {"name": "settings", "type": "object", "required": True},
            {"name": "score", "type": "number", "required": True},
        ]
    )

    with pytest.raises(WorkflowInputError) as captured:
        validate_execution_inputs(
            dsl,
            {"settings": "not-an-object", "score": True, "extra": 1},
        )

    errors = {(error["field"], error["code"]) for error in captured.value.errors}
    assert errors == {
        ("extra", "unknown"),
        ("query", "required"),
        ("settings", "type"),
        ("score", "type"),
    }


def test_array_inputs_are_declared_and_type_checked() -> None:
    dsl = workflow(variables=[{"name": "items", "type": "array", "required": True}])

    assert validate_execution_inputs(dsl, {"items": [1, {"id": "two"}]}) == {
        "items": [1, {"id": "two"}]
    }
    with pytest.raises(WorkflowInputError) as captured:
        validate_execution_inputs(dsl, {"items": {"not": "an array"}})
    assert captured.value.errors[0]["code"] == "type"
