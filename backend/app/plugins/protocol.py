"""Versioned protocol models shared by the plugin registry and subprocess runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PLUGIN_API_VERSION: Literal["1.0"] = "1.0"
PLUGIN_PROTOCOL_VERSION: Literal["1.0"] = "1.0"


def _bounded_strings(values: list[str], *, field_name: str) -> list[str]:
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value) > 255:
            raise ValueError(f"{field_name} entries must be non-empty strings up to 255 chars")
        normalized.append(value.strip())
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} entries must be unique")
    return normalized


class PluginPermissions(BaseModel):
    """Declarative permissions shown to operators and attached to events."""

    model_config = ConfigDict(extra="forbid")

    network: list[str] = Field(default_factory=list, max_length=16)
    filesystem: list[str] = Field(default_factory=list, max_length=16)
    commands: list[str] = Field(default_factory=list, max_length=16)

    _network = field_validator("network")(
        lambda values: _bounded_strings(values, field_name="network")
    )
    _filesystem = field_validator("filesystem")(
        lambda values: _bounded_strings(values, field_name="filesystem")
    )
    _commands = field_validator("commands")(
        lambda values: _bounded_strings(values, field_name="commands")
    )


class PluginUiHints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    icon: str = Field(default="Puzzle", min_length=1, max_length=64)
    color: str = Field(default="#a78bfa", min_length=1, max_length=32)
    inspector_group: str = Field(default="Plugins", min_length=1, max_length=64)


class PluginManifest(BaseModel):
    """Immutable metadata for one versioned node plugin."""

    model_config = ConfigDict(extra="forbid")

    plugin_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9._-]*$")
    version: str = Field(
        min_length=5,
        max_length=32,
        pattern=r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$",
    )
    api_version: Literal["1.0"] = PLUGIN_API_VERSION
    protocol_version: Literal["1.0"] = PLUGIN_PROTOCOL_VERSION
    node_type: str = Field(
        min_length=9,
        max_length=72,
        pattern=r"^plugin\.[a-z][a-z0-9._-]{1,62}$",
    )
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    config_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    ui_hints: PluginUiHints = Field(default_factory=PluginUiHints)
    permissions: PluginPermissions = Field(default_factory=PluginPermissions)
    entrypoint: str = Field(min_length=1, max_length=255)
    timeout_seconds: float = Field(default=5.0, gt=0, le=30)

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint_text(cls, value: str) -> str:
        if "\x00" in value or value.strip() != value:
            raise ValueError("entrypoint must be a clean relative path")
        return value


class PluginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["1.0"] = PLUGIN_PROTOCOL_VERSION
    request_id: str = Field(min_length=16, max_length=64)
    node_type: str = Field(min_length=9, max_length=72)
    config: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)
    upstream_outputs: dict[str, Any] = Field(default_factory=dict)


class PluginEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z][a-zA-Z0-9._:-]*$")
    data: dict[str, Any] = Field(default_factory=dict)


class PluginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["1.0"]
    request_id: str = Field(min_length=16, max_length=64)
    ok: bool
    output: Any = None
    events: list[PluginEvent] = Field(default_factory=list, max_length=64)
    error: str | None = Field(default=None, max_length=4000)


@dataclass(frozen=True)
class PluginDescriptor:
    manifest: PluginManifest
    directory: Path

    @property
    def entrypoint(self) -> Path:
        return (self.directory / self.manifest.entrypoint).resolve()
