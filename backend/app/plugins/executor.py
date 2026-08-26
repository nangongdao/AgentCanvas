"""Adapter from the generic node executor contract to a plugin process."""

from __future__ import annotations

import uuid
from typing import Any

from jsonschema import Draft202012Validator

from app.core.logging import redact_value
from app.engine.nodes.base import BaseNodeExecutor, CompileContext, NodeFn
from app.engine.state import WorkflowState
from app.plugins.protocol import PluginDescriptor, PluginRequest
from app.plugins.runner import PluginProcessRunner
from app.schemas.dsl import NodeSpec, PluginConfig
from app.schemas.events import EventType
from app.services.execution_inspection import bounded_json_snapshot


class PluginNodeExecutor(BaseNodeExecutor):
    """Version-pinned executor whose arbitrary code never runs in this process."""

    config_model = PluginConfig

    def __init__(self, descriptor: PluginDescriptor, runner: PluginProcessRunner) -> None:
        self.descriptor = descriptor
        self.runner = runner
        self.plugin_id = descriptor.manifest.plugin_id
        self.node_type = descriptor.manifest.node_type  # type: ignore[misc]
        Draft202012Validator.check_schema(descriptor.manifest.config_schema)

    @property
    def manifest(self):
        return self.descriptor.manifest

    def metadata(self) -> dict[str, Any]:
        manifest = self.manifest
        return {
            "type": manifest.node_type,
            "label": manifest.label,
            "description": manifest.description,
            "config_schema": manifest.config_schema,
            "plugin": {
                "id": manifest.plugin_id,
                "version": manifest.version,
                "api_version": manifest.api_version,
                "protocol_version": manifest.protocol_version,
                "ui_hints": manifest.ui_hints.model_dump(mode="json"),
                "permissions": manifest.permissions.model_dump(mode="json"),
            },
        }

    def validate_config(self, config: dict[str, Any]) -> None:
        errors = sorted(
            Draft202012Validator(self.manifest.config_schema).iter_errors(config or {}),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            path = ".".join(str(item) for item in errors[0].absolute_path) or "config"
            raise ValueError(f"{path}: {errors[0].message}")

    def build(self, node: NodeSpec, ctx: CompileContext) -> NodeFn:
        config = dict(node.config or {})
        self.validate_config(config)
        manifest = self.manifest

        async def run(state: WorkflowState) -> dict[str, Any]:
            inputs = self._bounded_mapping(state.get("inputs"))
            upstream_outputs = self._bounded_mapping(state.get("node_outputs"))
            await ctx.emitter.emit(
                EventType.NODE_STREAMING,
                node_id=node.id,
                payload={
                    "kind": "plugin_started",
                    "plugin_id": manifest.plugin_id,
                    "plugin_version": manifest.version,
                    "api_version": manifest.api_version,
                    "permissions": manifest.permissions.model_dump(mode="json"),
                },
            )
            result = await self.runner.run(
                self.descriptor,
                PluginRequest(
                    request_id=uuid.uuid4().hex,
                    node_type=manifest.node_type,
                    config=config,
                    inputs=inputs,
                    upstream_outputs=upstream_outputs,
                ),
            )
            for event in result.events:
                safe_data = bounded_json_snapshot(redact_value(event.data))
                await ctx.emitter.emit(
                    EventType.NODE_STREAMING,
                    node_id=node.id,
                    payload={
                        "kind": "plugin_event",
                        "plugin_id": manifest.plugin_id,
                        "plugin_version": manifest.version,
                        "event": {"kind": event.kind, "data": safe_data},
                    },
                )
            return {
                "node_outputs": {
                    node.id: {
                        "output": result.output,
                        "meta": {
                            "plugin_id": manifest.plugin_id,
                            "plugin_version": manifest.version,
                            "node_type": manifest.node_type,
                        },
                    }
                }
            }

        return run

    @staticmethod
    def _bounded_mapping(value: Any) -> dict[str, Any]:
        snapshot = bounded_json_snapshot(value if isinstance(value, dict) else {})
        return snapshot if isinstance(snapshot, dict) else {"_truncated": True}


__all__ = ["PluginNodeExecutor"]
