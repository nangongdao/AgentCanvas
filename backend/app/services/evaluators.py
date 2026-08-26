"""Deterministic evaluators plus the opt-in LLM judge adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from app.providers.base import ChatMessage


@dataclass(frozen=True)
class EvaluationOutcome:
    passed: bool
    score: float
    message: str


def resolve_actual(output: Any, path: str) -> Any:
    """Resolve an optional JSON Pointer or dotted path from workflow output."""
    normalized = path.strip()
    if not normalized:
        return output
    parts = (
        [part.replace("~1", "/").replace("~0", "~") for part in normalized[1:].split("/")]
        if normalized.startswith("/")
        else normalized.split(".")
    )
    current = output
    for part in parts:
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise ValueError(f"actual path not found: {normalized}")
    return current


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def evaluate_deterministic(
    evaluator_type: str,
    *,
    actual: Any,
    expected: Any,
    case_sensitive: bool = True,
) -> EvaluationOutcome:
    if evaluator_type == "exact":
        passed = actual == expected
        return EvaluationOutcome(passed, 1.0 if passed else 0.0, "exact match" if passed else "values differ")

    if evaluator_type == "contains":
        haystack = _text(actual)
        needles = expected if isinstance(expected, list) else [expected]
        expected_text = [_text(value) for value in needles]
        if not case_sensitive:
            haystack = haystack.casefold()
            expected_text = [value.casefold() for value in expected_text]
        passed = all(value in haystack for value in expected_text)
        message = "contains all expected values" if passed else "expected value not found"
        return EvaluationOutcome(passed, 1.0 if passed else 0.0, message)

    if evaluator_type == "json_schema":
        if not isinstance(expected, Mapping):
            return EvaluationOutcome(False, 0.0, "expected value must be a JSON Schema object")
        try:
            schema = dict(expected)
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(actual)
        except SchemaError as exc:
            return EvaluationOutcome(False, 0.0, f"invalid JSON Schema: {exc.message}")
        except ValidationError as exc:
            location = "/".join(str(part) for part in exc.absolute_path) or "$"
            return EvaluationOutcome(False, 0.0, f"schema mismatch at {location}: {exc.message}")
        return EvaluationOutcome(True, 1.0, "JSON Schema matched")

    raise ValueError(f"unsupported deterministic evaluator: {evaluator_type}")


async def evaluate_with_llm(
    provider: Any,
    *,
    actual: Any,
    expected: Any,
    rubric: str,
    threshold: float,
) -> EvaluationOutcome:
    payload = json.dumps(
        {"rubric": rubric, "expected": expected, "actual": actual},
        ensure_ascii=False,
        sort_keys=True,
    )
    result = await provider.chat(
        [
            ChatMessage(
                role="system",
                content=(
                    "You are an evaluation judge. Return only JSON with numeric score from 0 to 1 "
                    "and a short rationale: {\"score\":0.0,\"rationale\":\"...\"}."
                ),
            ),
            ChatMessage(role="user", content=payload),
        ],
        temperature=0,
    )
    try:
        judged = json.loads(result.content)
        score = float(judged["score"])
        rationale = str(judged.get("rationale") or "LLM judge completed")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("LLM judge returned invalid JSON") from exc
    if not 0 <= score <= 1:
        raise ValueError("LLM judge score must be between 0 and 1")
    return EvaluationOutcome(score >= threshold, score, rationale[:4000])


__all__ = [
    "EvaluationOutcome",
    "evaluate_deterministic",
    "evaluate_with_llm",
    "resolve_actual",
]
