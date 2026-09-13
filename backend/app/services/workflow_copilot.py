"""Workflow AI Copilot — natural language in, validated Workflow DSL out.

The copilot does not invent a second schema. It prompts the configured chat
model with the *live* node catalog (:func:`~app.engine.nodes.list_node_types`)
and a description of the DSL envelope, then round-trips whatever comes back
through the very same :func:`~app.engine.validation.validate_dsl` gate the
editor save path and the compiler use. A draft that cannot be parsed, or that
fails validation, is repaired in a bounded loop — the model is shown the exact
validator errors and asked for a corrected document.

The caller always receives both the draft and the validation report, so a
rejected draft stays inspectable instead of being silently dropped: the copilot
proposes, the user disposes.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.engine.nodes import list_node_types
from app.engine.validation import DSLValidationError, validate_dsl
from app.providers import COPILOT_PROMPT_MARKER, BaseChatProvider, ChatMessage
from app.schemas.dsl import NodeType, WorkflowDSL
from app.services.dsl_migrations import UnsupportedDSLVersion, normalize_workflow_dsl

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_PROMPT_CHARS = 4_000
_MAX_CATALOG_VALUE_CHARS = 48
_MAX_REPAIR_ERRORS = 12
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class WorkflowCopilotError(RuntimeError):
    """The model never produced anything usable as a workflow document."""


@dataclass(frozen=True)
class CopilotDraft:
    """A drafted document plus the validator's verdict on it."""

    dsl: dict[str, Any]
    name: str
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    attempts: int = 1
    provider: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _compact_value(value: Any) -> str:
    if isinstance(value, str):
        text = value.replace("\n", " ")
        if len(text) > _MAX_CATALOG_VALUE_CHARS:
            text = text[: _MAX_CATALOG_VALUE_CHARS - 1] + "…"
        return json.dumps(text, ensure_ascii=False)
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return "?"
    if len(text) > _MAX_CATALOG_VALUE_CHARS:
        text = text[: _MAX_CATALOG_VALUE_CHARS - 1] + "…"
    return text


def _field_type(schema: Any) -> str:
    """Render a JSON-Schema property as a short, model-readable type name."""
    if not isinstance(schema, dict):
        return "any"
    if "$ref" in schema:
        return "object"
    if "const" in schema:
        return json.dumps(schema["const"], ensure_ascii=False)
    if "enum" in schema and isinstance(schema["enum"], list):
        return "|".join(json.dumps(v, ensure_ascii=False) for v in schema["enum"])
    if isinstance(schema.get("anyOf"), list):
        names = [_field_type(option) for option in schema["anyOf"]]
        deduped = list(dict.fromkeys(name for name in names if name != "null"))
        if not deduped:
            return "null"
        return "|".join(deduped)
    if schema.get("type") == "array":
        return f"array<{_field_type(schema.get('items'))}>"
    return str(schema.get("type") or "any")


def _catalog_line(entry: dict[str, Any]) -> str:
    schema = entry.get("config_schema") or {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    pieces: list[str] = []
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        piece = f"{name}: {_field_type(prop)}"
        if name in required:
            piece += " (required)"
        if "default" in prop:
            piece += f" = {_compact_value(prop['default'])}"
        pieces.append(piece)
    body = "; ".join(pieces) if pieces else "no fields"
    return f"- {entry.get('type')}: {body}"


def node_catalog_block() -> str:
    """Render ``/api/node-types`` as a compact field list for the prompt.

    Derived from the live registry rather than hand-written, so a new node type
    or a changed config field reaches the copilot without a second edit here.
    """
    return "\n".join(_catalog_line(entry) for entry in list_node_types())


def _system_prompt() -> str:
    return f"""{COPILOT_PROMPT_MARKER}

You are the AgentCanvas workflow copilot. Turn the user's request into one valid
AgentCanvas workflow document.

Reply with a single JSON object and nothing else — no prose, no markdown fences.

Top-level keys:
  version    always "1.0"
  name       short human title for the workflow
  variables  [{{"name","type","required","default"}}]  type in string|number|boolean|object|array
  settings   {{"max_loop_iterations","timeout_seconds","recursion_limit"}}
  nodes      [{{"id","type","name","position":{{"x","y"}},"config":{{...}}}}]
  edges      [{{"id","source","target","source_handle","target_handle","label"}}]
  canvas     {{"groups":[],"notes":[]}}   leave empty unless the user asks for notes

Hard requirements the validator enforces:
- exactly one node of type "start"; at least one node of type "end"
- node ids are unique; every edge source/target is an existing node id
- every node is reachable from "start"
- no cycles, unless a "condition" node or a "supervisor" agent lies on the cycle
- give every node an "id", "type", "name" and "position"; lay the graph left to
  right (x = 0, 320, 640 …), putting branch rows at y = 160
- reference data with templates: {{{{input.<variable>}}}} and {{{{nodes.<node_id>.output}}}}
- write "name" and other user-facing strings in the same language as the request

Node types and their config fields (field: type = default):
{node_catalog_block()}"""


def _user_prompt(prompt: str, base_dsl: dict[str, Any] | None) -> str:
    sections = [f"Request:\n{prompt.strip()}"]
    if base_dsl:
        sections.append(
            "Current workflow to modify — keep whatever still satisfies the request, "
            "and reply with the complete corrected document:\n"
            + json.dumps(base_dsl, ensure_ascii=False)
        )
    return "\n\n".join(sections)


def _repair_message(errors: Sequence[str]) -> str:
    listed = "\n".join(f"- {error}" for error in errors[:_MAX_REPAIR_ERRORS])
    return (
        "That document was rejected by the AgentCanvas validator. Fix exactly these "
        "problems and reply with the corrected JSON object only:\n" + listed
    )


def _extract_json(text: str) -> dict[str, Any]:
    candidate = text.strip()
    fenced = _FENCE_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    payload: Any = None
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end > start:
            try:
                payload = json.loads(candidate[start : end + 1])
            except json.JSONDecodeError as exc:
                raise WorkflowCopilotError(f"model reply was not valid JSON: {exc.msg}") from exc
    if payload is None:
        raise WorkflowCopilotError("model reply did not contain a JSON object")
    if not isinstance(payload, dict):
        raise WorkflowCopilotError("model reply was not a JSON object")
    return payload


def _format_validation_errors(exc: ValidationError) -> list[str]:
    errors: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
        errors.append(f"{location}: {error.get('msg') or 'invalid value'}")
    return errors or ["document does not match the workflow DSL schema"]


def _collect_warnings(
    dsl: WorkflowDSL,
    available_model_ids: Collection[str] | None,
) -> list[str]:
    """Surface problems the hard validator deliberately tolerates."""
    warnings: list[str] = []
    if not dsl.name.strip():
        warnings.append("workflow name is empty")
    if available_model_ids is None:
        return warnings
    known = set(available_model_ids)
    for node in dsl.nodes:
        if node.type != NodeType.AGENT:
            continue
        config = node.config or {}
        referenced = [
            str(config.get("model_config_id") or ""),
            *(str(model_id) for model_id in config.get("fallback_model_config_ids") or []),
        ]
        unknown = sorted({model_id for model_id in referenced if model_id and model_id not in known})
        if unknown:
            warnings.append(
                f"agent '{node.id}' references unknown chat model(s): {', '.join(unknown)}"
            )
    return warnings


def _validate_payload(payload: dict[str, Any]) -> tuple[WorkflowDSL | None, list[str]]:
    """Return ``(document, errors)``; a non-None document may still carry errors."""
    try:
        dsl = WorkflowDSL.model_validate(normalize_workflow_dsl(payload))
    except ValidationError as exc:
        return None, _format_validation_errors(exc)
    except UnsupportedDSLVersion as exc:
        return None, [f"unsupported workflow version: {exc}"]
    except ValueError as exc:
        return None, [str(exc)]
    try:
        validate_dsl(dsl, strict=True)
    except DSLValidationError as exc:
        return dsl, list(exc.errors)
    return dsl, []


async def draft_workflow(
    provider: BaseChatProvider,
    *,
    prompt: str,
    base_dsl: dict[str, Any] | None = None,
    available_model_ids: Collection[str] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> CopilotDraft:
    """Ask ``provider`` for a workflow draft, validating and repairing it in a bounded loop.

    Raises :class:`WorkflowCopilotError` when the final attempt did not even match
    the DSL schema. A schema-valid but graph-invalid document is returned with
    ``valid=False`` and the validator's ``errors``, so the rejection is inspectable.
    """
    if not prompt.strip():
        raise ValueError("copilot prompt cannot be empty")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=_system_prompt()),
        ChatMessage(role="user", content=_user_prompt(prompt, base_dsl)),
    ]
    params: dict[str, Any] = {"temperature": 0.2}
    if bool(getattr(provider.capabilities, "json_mode", False)):
        params["response_format"] = {"type": "json_object"}

    prompt_tokens = 0
    completion_tokens = 0
    attempts = 0
    last_dsl: WorkflowDSL | None = None
    last_errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        result = await provider.chat(tuple(messages), **params)
        if result.usage is not None:
            prompt_tokens += result.usage.prompt_tokens
            completion_tokens += result.usage.completion_tokens
        try:
            payload = _extract_json(result.content)
        except WorkflowCopilotError as exc:
            last_dsl, last_errors = None, [str(exc)]
            reply = result.content[:MAX_PROMPT_CHARS]
        else:
            last_dsl, last_errors = _validate_payload(payload)
            if last_dsl is not None and not last_errors:
                return CopilotDraft(
                    dsl=last_dsl.model_dump(mode="json"),
                    name=last_dsl.name,
                    valid=True,
                    errors=[],
                    warnings=_collect_warnings(last_dsl, available_model_ids),
                    attempts=attempt,
                    provider=provider.name,
                    model=provider.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            reply = json.dumps(payload, ensure_ascii=False)[:MAX_PROMPT_CHARS]
        messages.append(ChatMessage(role="assistant", content=reply))
        messages.append(ChatMessage(role="user", content=_repair_message(last_errors)))

    if last_dsl is None:
        raise WorkflowCopilotError(
            "copilot model did not return a workflow document matching the DSL schema; "
            "last problem: " + "; ".join(last_errors)
        )
    logger.info("copilot returned a schema-valid but graph-invalid draft after %d attempts", attempts)
    return CopilotDraft(
        dsl=last_dsl.model_dump(mode="json"),
        name=last_dsl.name,
        valid=False,
        errors=last_errors,
        warnings=_collect_warnings(last_dsl, available_model_ids),
        attempts=attempts,
        provider=provider.name,
        model=provider.model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


__all__ = [
    "MAX_ATTEMPTS",
    "CopilotDraft",
    "WorkflowCopilotError",
    "draft_workflow",
    "node_catalog_block",
]
