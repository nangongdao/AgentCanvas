"""Typed parameter resolution and recursive workflow-template rendering."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

_PLACEHOLDER = re.compile(r"\$\{parameter\.([a-zA-Z][a-zA-Z0-9_]*)\}")
_MISSING = object()


class TemplateParameterError(ValueError):
    """Raised when template parameters are missing or have the wrong type."""


def _coerce(name: str, kind: str, value: Any) -> Any:
    if kind == "string":
        if isinstance(value, (dict, list)):
            raise TemplateParameterError(f"parameter '{name}' must be a string")
        return str(value)
    if kind == "number":
        if isinstance(value, bool):
            raise TemplateParameterError(f"parameter '{name}' must be a number")
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise TemplateParameterError(f"parameter '{name}' must be a number") from exc
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().casefold() in {"true", "false"}:
            return value.strip().casefold() == "true"
        raise TemplateParameterError(f"parameter '{name}' must be a boolean")
    raise TemplateParameterError(f"parameter '{name}' has unsupported type '{kind}'")


def resolve_template_parameters(
    definitions: list[dict[str, Any]], provided: dict[str, Any]
) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    known: set[str] = set()
    for definition in definitions:
        name = str(definition.get("name") or "")
        if not name:
            raise TemplateParameterError("template parameter name cannot be empty")
        known.add(name)
        value = provided.get(name, definition.get("default", _MISSING))
        if value is None:
            value = _MISSING
        if definition.get("required", False) and isinstance(value, str) and not value.strip():
            value = _MISSING
        if value is _MISSING:
            if definition.get("required", False):
                raise TemplateParameterError(f"required parameter '{name}' is missing")
            continue
        resolved[name] = _coerce(name, str(definition.get("type") or "string"), value)
    unknown = sorted(set(provided) - known)
    if unknown:
        raise TemplateParameterError(f"unknown template parameters: {', '.join(unknown)}")
    return resolved


def render_template_dsl(dsl: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    def render(value: Any) -> Any:
        if isinstance(value, str):
            match = _PLACEHOLDER.fullmatch(value)
            if match:
                return parameters.get(match.group(1), value)

            def replace(match: re.Match[str]) -> str:
                name = match.group(1)
                return str(parameters.get(name, match.group(0)))

            return _PLACEHOLDER.sub(replace, value)
        if isinstance(value, list):
            return [render(item) for item in value]
        if isinstance(value, dict):
            return {key: render(item) for key, item in value.items()}
        return value

    return render(deepcopy(dsl))


__all__ = [
    "TemplateParameterError",
    "render_template_dsl",
    "resolve_template_parameters",
]
