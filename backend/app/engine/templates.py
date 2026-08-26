"""Safe template renderer for {{input.x}} / {{nodes.id.output}} expressions."""

from __future__ import annotations

import re
from typing import Any

_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


class TemplateError(ValueError):
    """Raised when a template path cannot be resolved (strict mode)."""


def _lookup(path: str, ctx: dict[str, Any]) -> Any:
    parts = path.split(".")
    cur: Any = ctx
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise TemplateError(f"cannot resolve '{{{{{path}}}}}'")
    return cur


def build_context(
    *,
    inputs: dict[str, Any] | None = None,
    node_outputs: dict[str, Any] | None = None,
    vars_: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the root context used by template rendering.

    ``inputs`` may carry the engine-reserved ``_session_vars`` snapshot injected
    by the execution runner for chat sessions; it is exposed to templates as
    the ``{{session.<name>}}`` root so multi-turn memory survives across sends.
    """
    nodes: dict[str, Any] = {}
    for nid, output in (node_outputs or {}).items():
        if isinstance(output, dict) and "output" in output and len(output) == 1:
            nodes[nid] = output
        elif isinstance(output, dict):
            # Allow both {{nodes.id.output}} and {{nodes.id.field}}
            nodes[nid] = {"output": output, **output}
        else:
            nodes[nid] = {"output": output}
    raw_session = (inputs or {}).get("_session_vars")
    session_vars = dict(raw_session) if isinstance(raw_session, dict) else {}
    return {
        "input": inputs or {},
        "inputs": inputs or {},
        "nodes": nodes,
        "vars": vars_ or {},
        "session": session_vars,
    }


def render_string(template: str, ctx: dict[str, Any], *, strict: bool = False) -> str:
    """Replace {{path}} placeholders. Missing paths become empty string unless strict."""

    def repl(match: re.Match[str]) -> str:
        path = match.group(1)
        try:
            value = _lookup(path, ctx)
        except TemplateError:
            if strict:
                raise
            return ""
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            import json

            return json.dumps(value, ensure_ascii=False)
        return str(value)

    return _PATTERN.sub(repl, template)


def render_value(value: Any, ctx: dict[str, Any], *, strict: bool = False) -> Any:
    """Recursively render strings inside nested dict/list structures."""
    if isinstance(value, str):
        if "{{" in value and "}}" in value:
            # If the whole string is a single placeholder, preserve native type.
            m = _PATTERN.fullmatch(value.strip())
            if m:
                try:
                    return _lookup(m.group(1), ctx)
                except TemplateError:
                    if strict:
                        raise
                    return None
            return render_string(value, ctx, strict=strict)
        return value
    if isinstance(value, list):
        return [render_value(v, ctx, strict=strict) for v in value]
    if isinstance(value, dict):
        return {k: render_value(v, ctx, strict=strict) for k, v in value.items()}
    return value
