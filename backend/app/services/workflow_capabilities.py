"""Derive and validate workflow model capability requirements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.provider_capabilities import CAPABILITY_NAMES, CapabilityName
from app.db.repositories import ModelConfigRepo
from app.engine.dsl_traversal import iter_workflow_nodes
from app.schemas.dsl import AgentConfig, NodeType, WorkflowDSL
from app.services.model_capabilities import (
    ModelCapabilityError,
    effective_model_capabilities,
)

_JSON_FORMATS = frozenset({"json", "json_object", "json_schema"})
_DISABLED_VALUES = frozenset({"", "none", "off", "disabled", "false"})


def _enabled_parameter(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _DISABLED_VALUES
    return True


def _json_parameter_requested(params: dict[str, Any]) -> bool:
    if _enabled_parameter(params.get("json_mode")):
        return True
    for key in ("response_format", "format"):
        if key not in params:
            continue
        value = params[key]
        if isinstance(value, str) and value.strip().lower() in _JSON_FORMATS:
            return True
        if isinstance(value, dict):
            format_type = str(value.get("type") or "").strip().lower()
            if key == "format" or format_type in _JSON_FORMATS:
                return True
    return False


def required_agent_capabilities(config: AgentConfig) -> frozenset[CapabilityName]:
    required: set[CapabilityName] = {"stream"}
    if config.tools:
        required.add("tools")
    output_format = str(config.output.get("format") or "").strip().lower()
    if (
        config.agent_mode == "supervisor"
        or output_format in _JSON_FORMATS
        or _json_parameter_requested(config.params)
    ):
        required.add("json_mode")
    if any(
        _enabled_parameter(config.params.get(key))
        for key in ("reasoning", "reasoning_effort", "thinking")
        if key in config.params
    ):
        required.add("reasoning")
    if config.requirements.usage or config.requirements.cost:
        required.add("usage")
    if config.requirements.cost:
        required.add("cost")
    return frozenset(required)


def model_chain_ids(config: AgentConfig) -> tuple[str, ...]:
    return (config.model_config_id, *config.fallback_model_config_ids)


def workflow_model_ids(dsl: WorkflowDSL) -> frozenset[str]:
    return frozenset(
        model_id
        for node in iter_workflow_nodes(dsl)
        if node.type == NodeType.AGENT
        for model_id in model_chain_ids(AgentConfig.model_validate(node.config or {}))
    )


def _ordered_capabilities(names: set[CapabilityName]) -> tuple[CapabilityName, ...]:
    return tuple(name for name in CAPABILITY_NAMES if name in names)


@dataclass(frozen=True)
class WorkflowCapabilityIssue:
    node_id: str
    model_config_id: str
    code: str
    message: str
    required: tuple[CapabilityName, ...] = ()
    missing: tuple[CapabilityName, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "node_id": self.node_id,
            "model_config_id": self.model_config_id,
            "code": self.code,
            "message": self.message,
        }
        if self.required:
            payload["required"] = list(self.required)
        if self.missing:
            payload["missing"] = list(self.missing)
        return payload


class WorkflowCapabilityError(ValueError):
    def __init__(self, issues: list[WorkflowCapabilityIssue]) -> None:
        self.issues = tuple(issues)
        self.errors = [issue.to_dict() for issue in issues]
        super().__init__("; ".join(issue.message for issue in issues))


async def validate_workflow_capabilities(
    session: AsyncSession,
    dsl: WorkflowDSL,
) -> None:
    models = await ModelConfigRepo(session).get_many(set(workflow_model_ids(dsl)))
    issues: list[WorkflowCapabilityIssue] = []
    for node in iter_workflow_nodes(dsl):
        if node.type != NodeType.AGENT:
            continue
        config = AgentConfig.model_validate(node.config or {})
        required_set = set(required_agent_capabilities(config))
        required = _ordered_capabilities(required_set)
        for model_id in model_chain_ids(config):
            row = models.get(model_id)
            if row is None:
                issues.append(
                    WorkflowCapabilityIssue(
                        node_id=node.id,
                        model_config_id=model_id,
                        code="model_not_found",
                        message=f"node '{node.id}' references missing model '{model_id}'",
                        required=required,
                    )
                )
                continue
            if row.kind != "chat":
                issues.append(
                    WorkflowCapabilityIssue(
                        node_id=node.id,
                        model_config_id=model_id,
                        code="model_not_chat",
                        message=(
                            f"node '{node.id}' requires a chat model; "
                            f"'{model_id}' is {row.kind}"
                        ),
                        required=required,
                    )
                )
                continue
            try:
                profile = effective_model_capabilities(row)
            except ModelCapabilityError as exc:
                issues.append(
                    WorkflowCapabilityIssue(
                        node_id=node.id,
                        model_config_id=model_id,
                        code="invalid_model_profile",
                        message=f"node '{node.id}' model '{model_id}' is invalid: {exc}",
                        required=required,
                    )
                )
                continue
            missing_set = {name for name in required_set if not bool(getattr(profile, name))}
            if missing_set:
                missing = _ordered_capabilities(missing_set)
                issues.append(
                    WorkflowCapabilityIssue(
                        node_id=node.id,
                        model_config_id=model_id,
                        code="capability_mismatch",
                        message=(
                            f"node '{node.id}' model '{model_id}' lacks capabilities: "
                            f"{', '.join(missing)}"
                        ),
                        required=required,
                        missing=missing,
                    )
                )
    if issues:
        raise WorkflowCapabilityError(issues)


__all__ = [
    "WorkflowCapabilityError",
    "WorkflowCapabilityIssue",
    "model_chain_ids",
    "required_agent_capabilities",
    "validate_workflow_capabilities",
    "workflow_model_ids",
]
