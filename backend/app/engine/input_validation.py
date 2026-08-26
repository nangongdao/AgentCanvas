"""Workflow execution-input validation and default application."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.schemas.dsl import StartConfig, VariableType, WorkflowDSL


@dataclass(frozen=True)
class InputDefinition:
    name: str
    type: VariableType
    required: bool
    default: Any


class WorkflowInputError(ValueError):
    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = errors
        super().__init__("; ".join(error["message"] for error in errors))


def input_definitions(dsl: WorkflowDSL) -> dict[str, InputDefinition]:
    """Merge workflow variables with start-node fields; top-level variables win."""
    definitions: dict[str, InputDefinition] = {}
    for variable in dsl.variables:
        definitions[variable.name] = InputDefinition(
            name=variable.name,
            type=variable.type,
            required=variable.required,
            default=variable.default,
        )

    for node in dsl.nodes:
        if node.type != "start":
            continue
        config = StartConfig.model_validate(node.config or {})
        for field in config.input_schema:
            definitions.setdefault(
                field.name,
                InputDefinition(
                    name=field.name,
                    type=field.type,
                    required=field.required,
                    default=field.default,
                ),
            )
    return definitions


def _matches_type(value: Any, variable_type: VariableType) -> bool:
    if variable_type == VariableType.STRING:
        return isinstance(value, str)
    if variable_type == VariableType.NUMBER:
        return (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    if variable_type == VariableType.BOOLEAN:
        return isinstance(value, bool)
    if variable_type == VariableType.OBJECT:
        return isinstance(value, dict)
    if variable_type == VariableType.ARRAY:
        return isinstance(value, list)
    return False


def validate_execution_inputs(
    dsl: WorkflowDSL, inputs: dict[str, Any]
) -> dict[str, Any]:
    """Validate declared workflow inputs and return a copy with defaults applied.

    Engine-reserved metadata keys (``_session_id``, ``_session_vars``) are
    passed through without needing to be declared as workflow variables.
    """
    definitions = input_definitions(dsl)
    if not definitions:
        return dict(inputs)

    engine_keys = {"_session_id", "_session_vars"}
    normalized = {k: v for k, v in inputs.items() if k in engine_keys}
    errors: list[dict[str, str]] = []

    for name in sorted(inputs.keys() - definitions.keys() - engine_keys):
        errors.append(
            {
                "field": name,
                "code": "unknown",
                "message": f"input '{name}' is not declared by the workflow",
            }
        )

    for name, definition in definitions.items():
        if name not in inputs:
            if definition.default is not None:
                normalized[name] = definition.default
            elif definition.required:
                errors.append(
                    {
                        "field": name,
                        "code": "required",
                        "message": f"input '{name}' is required",
                    }
                )
            continue

        value = inputs[name]
        if not _matches_type(value, definition.type):
            errors.append(
                {
                    "field": name,
                    "code": "type",
                    "message": f"input '{name}' must be {definition.type.value}",
                }
            )
        else:
            normalized[name] = value

    if errors:
        raise WorkflowInputError(errors)
    return normalized
