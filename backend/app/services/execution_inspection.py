"""Bounded node snapshots and event-backed execution inspection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from app.core.logging import redact_value
from app.engine.dsl_traversal import node_path_key

MAX_NODE_SNAPSHOT_BYTES = 64 * 1024
MAX_NODE_ERROR_CHARS = 4000


def _json_default(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return str(value)


def bounded_json_snapshot(value: Any) -> Any:
    """Return JSON-safe data, or a digest marker when the payload is too large."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=_json_default,
    ).encode("utf-8")
    if len(encoded) > MAX_NODE_SNAPSHOT_BYTES:
        return {
            "_truncated": True,
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
    return json.loads(encoded)


def node_input_snapshot(state: Mapping[str, Any]) -> dict[str, Any]:
    return bounded_json_snapshot(
        {
            "inputs": state.get("inputs") or {},
            "upstream_outputs": state.get("node_outputs") or {},
        }
    )


def bounded_error(value: Any) -> str:
    text = str(value)
    if len(text) <= MAX_NODE_ERROR_CHARS:
        return text
    return f"{text[:MAX_NODE_ERROR_CHARS]}... [truncated {len(text)} chars]"


def _timestamp(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def extract_token_usage(output: Any) -> tuple[int, int, int, bool]:
    """Read normalized token usage and report whether the provider supplied it."""
    if not isinstance(output, Mapping):
        return 0, 0, 0, False
    meta = output.get("meta")
    usage = meta.get("usage") if isinstance(meta, Mapping) else output.get("usage")
    if not isinstance(usage, Mapping):
        return 0, 0, 0, False
    prompt = _count(usage.get("prompt", usage.get("prompt_tokens")))
    completion = _count(usage.get("completion", usage.get("completion_tokens")))
    total = _count(usage.get("total", usage.get("total_tokens")))
    return prompt, completion, total or prompt + completion, True


def _node_metadata(
    node_id: str,
    metadata: Mapping[str, Mapping[str, str]],
    *,
    node_path: str | None = None,
    node_path_segments: Sequence[str] | None = None,
) -> tuple[str, str]:
    structured_key = (
        node_path_key(tuple(node_path_segments)) if node_path_segments is not None else ""
    )
    node = metadata.get(structured_key) or metadata.get(node_path or "") or metadata.get(node_id) or {}
    return str(node.get("type") or "unknown"), str(node.get("label") or node_id)


def build_execution_inspection(
    events: Sequence[Any],
    *,
    node_metadata: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    """Pair durable lifecycle events into ordered, redacted node attempts."""
    attempts: list[dict[str, Any]] = []
    open_attempts: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}

    def start_attempt(event: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
        node_id = str(event.node_id or "unknown")
        counts[node_id] = counts.get(node_id, 0) + 1
        raw_node_path = payload.get("node_path")
        node_path = raw_node_path if isinstance(raw_node_path, str) else None
        raw_segments = payload.get("node_path_segments")
        node_path_segments = (
            raw_segments
            if isinstance(raw_segments, list) and all(isinstance(item, str) for item in raw_segments)
            else None
        )
        node_type, node_label = _node_metadata(
            node_id,
            node_metadata,
            node_path=node_path,
            node_path_segments=node_path_segments,
        )
        attempt = {
            "node_id": node_id,
            "node_type": node_type,
            "node_label": node_label,
            "attempt": counts[node_id],
            "status": "running",
            "started_seq": int(event.seq),
            "finished_seq": None,
            "started_at": _timestamp(event.ts),
            "finished_at": None,
            "duration_ms": None,
            "input": redact_value(payload.get("input")),
            "output": None,
            "error": None,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_usd": None,
            "price_version": None,
        }
        attempts.append(attempt)
        open_attempts[node_id] = attempt
        return attempt

    for event in events:
        payload = event.payload_json if isinstance(event.payload_json, Mapping) else {}
        event_type = str(event.event_type)
        node_id = str(event.node_id or payload.get("node_id") or "unknown")
        if event_type == "node_started":
            start_attempt(event, payload)
            continue
        if event_type not in {
            "node_finished",
            "node_failed",
            "workflow_interrupted",
            "workflow_cancelled",
        }:
            continue

        if event_type == "workflow_cancelled":
            for active_attempt in open_attempts.values():
                active_attempt["status"] = "cancelled"
                active_attempt["finished_seq"] = int(event.seq)
                active_attempt["finished_at"] = _timestamp(event.ts)
            open_attempts.clear()
            continue

        attempt = open_attempts.get(node_id)
        if attempt is None and event_type in {"node_finished", "node_failed"}:
            attempt = start_attempt(event, {})
        if attempt is None:
            continue

        if event_type == "workflow_interrupted":
            attempt["status"] = "interrupted"
        else:
            attempt["status"] = "succeeded" if event_type == "node_finished" else "failed"
        attempt["finished_seq"] = int(event.seq)
        attempt["finished_at"] = _timestamp(event.ts)
        attempt["duration_ms"] = _count(payload.get("elapsed_ms"))
        if event_type == "node_finished":
            output = payload.get("output")
            prompt, completion, total, _usage_supplied = extract_token_usage(output)
            attempt["output"] = redact_value(output)
            attempt["prompt_tokens"] = prompt
            attempt["completion_tokens"] = completion
            attempt["total_tokens"] = total
        elif payload.get("error") is not None:
            attempt["error"] = redact_value(str(payload["error"]))
        open_attempts.pop(node_id, None)

    return {
        "attempts": attempts,
        "total_duration_ms": sum(item["duration_ms"] or 0 for item in attempts),
        "prompt_tokens": sum(item["prompt_tokens"] for item in attempts),
        "completion_tokens": sum(item["completion_tokens"] for item in attempts),
        "total_tokens": sum(item["total_tokens"] for item in attempts),
        "estimated_cost_usd": None,
    }


__all__ = [
    "MAX_NODE_SNAPSHOT_BYTES",
    "bounded_error",
    "bounded_json_snapshot",
    "build_execution_inspection",
    "extract_token_usage",
    "node_input_snapshot",
]
