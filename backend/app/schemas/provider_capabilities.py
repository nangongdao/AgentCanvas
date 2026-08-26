"""HTTP contracts for Provider and model capability governance."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, StrictBool


class ProviderCapabilitiesOut(BaseModel):
    stream: bool = False
    tools: bool = False
    vision: bool = False
    json_mode: bool = False
    reasoning: bool = False
    usage: bool = False
    cost: bool = False


class ProviderCapabilityOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stream: StrictBool | None = None
    tools: StrictBool | None = None
    vision: StrictBool | None = None
    json_mode: StrictBool | None = None
    reasoning: StrictBool | None = None
    usage: StrictBool | None = None
    cost: StrictBool | None = None

    def compact(self) -> dict[str, bool]:
        return {name: value for name, value in self.model_dump().items() if isinstance(value, bool)}


class ProviderDescriptorOut(BaseModel):
    id: str
    capabilities: ProviderCapabilitiesOut


__all__ = [
    "ProviderCapabilitiesOut",
    "ProviderCapabilityOverrides",
    "ProviderDescriptorOut",
]
