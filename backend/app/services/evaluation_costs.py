"""Auditable token-cost snapshots for immutable evaluation executions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.engine.dsl_traversal import iter_located_workflow_nodes
from app.schemas.dsl import AgentConfig, NodeType, WorkflowDSL
from app.services.execution_inspection import extract_token_usage

COST_QUANTUM_USD = Decimal("0.000000000001")
HALF_COST_QUANTUM_USD = COST_QUANTUM_USD / 2
TOKENS_PER_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class ExecutionCost:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: str | None
    cost_known: bool
    cost_error_bound_usd: str | None
    price_versions: tuple[str, ...]


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


def _money(value: Decimal) -> str:
    return format(value.quantize(COST_QUANTUM_USD, rounding=ROUND_HALF_UP), "f")


def estimate_execution_cost(
    events: list[Any],
    *,
    dsl: WorkflowDSL,
    model_configs: dict[str, Any],
) -> ExecutionCost:
    """Price completed Agent attempts from their persisted provider usage.

    The estimate excludes evaluator overhead. A missing usage payload or missing rate
    makes the total unknown instead of silently treating the missing component as zero.
    """
    agent_models = {
        located.segments: AgentConfig.model_validate(located.node.config or {}).model_config_id
        for located in iter_located_workflow_nodes(dsl)
        if located.node.type == NodeType.AGENT
    }
    agent_models_by_path = {
        ".".join(segments): model_id for segments, model_id in agent_models.items()
    }
    started_agents: set[str] = set()
    finished_agents: set[str] = set()
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    exact_cost = Decimal(0)
    unknown = False
    priced_attempts = 0
    price_versions: set[str] = set()

    for event in events:
        node_id = str(event.node_id or "")
        payload = event.payload_json if isinstance(event.payload_json, dict) else {}
        raw_segments = payload.get("node_path_segments")
        structured_segments = (
            tuple(raw_segments)
            if isinstance(raw_segments, list) and all(isinstance(item, str) for item in raw_segments)
            else None
        )
        structured_path = payload.get("node_path")
        if structured_segments in agent_models:
            canonical_node_path = structured_segments
        elif isinstance(structured_path, str) and structured_path in agent_models_by_path:
            canonical_node_path = next(
                segments for segments in agent_models if ".".join(segments) == structured_path
            )
        elif (node_path := node_id) in agent_models_by_path:
            canonical_node_path = next(
                segments for segments in agent_models if ".".join(segments) == node_path
            )
        else:
            normalized_path = re.sub(r"\[\d+\]", "", node_id)
            canonical_node_path = next(
                (
                    segments
                    for segments in agent_models
                    if ".".join(segments) == normalized_path
                ),
                (),
            )
        if canonical_node_path not in agent_models:
            continue
        if str(event.event_type) == "node_started":
            started_agents.add(node_id)
            continue
        if str(event.event_type) != "node_finished":
            continue
        finished_agents.add(node_id)
        prompt, completion, total, supplied = extract_token_usage(payload.get("output"))
        prompt_tokens += prompt
        completion_tokens += completion
        total_tokens += total
        if not supplied:
            unknown = True
            continue

        model_id = agent_models[canonical_node_path]
        model = model_configs.get(model_id)
        prompt_rate = _decimal(
            getattr(model, "prompt_price_per_million_usd", None)
        )
        completion_rate = _decimal(
            getattr(model, "completion_price_per_million_usd", None)
        )
        if (prompt and prompt_rate is None) or (completion and completion_rate is None):
            unknown = True
            continue
        exact_cost += (
            Decimal(prompt) * (prompt_rate or Decimal(0))
            + Decimal(completion) * (completion_rate or Decimal(0))
        ) / TOKENS_PER_MILLION
        priced_attempts += 1
        version = getattr(model, "pricing_version", None)
        price_versions.add(str(version or f"{model_id}:unversioned"))

    if started_agents - finished_agents:
        unknown = True
    if unknown:
        return ExecutionCost(
            prompt_tokens,
            completion_tokens,
            total_tokens,
            None,
            False,
            None,
            tuple(sorted(price_versions)),
        )
    error_bound = HALF_COST_QUANTUM_USD if priced_attempts else Decimal(0)
    return ExecutionCost(
        prompt_tokens,
        completion_tokens,
        total_tokens,
        _money(exact_cost),
        True,
        format(error_bound, "f"),
        tuple(sorted(price_versions)),
    )


__all__ = [
    "COST_QUANTUM_USD",
    "ExecutionCost",
    "estimate_execution_cost",
]
