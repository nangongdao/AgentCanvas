"""Provider capability profiles shared by adapters, persistence, and validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final, Literal

CapabilityName = Literal[
    "stream",
    "tools",
    "vision",
    "json_mode",
    "reasoning",
    "usage",
    "cost",
]

CAPABILITY_NAMES: Final[tuple[CapabilityName, ...]] = (
    "stream",
    "tools",
    "vision",
    "json_mode",
    "reasoning",
    "usage",
    "cost",
)


@dataclass(frozen=True)
class ProviderCapabilities:
    stream: bool = False
    tools: bool = False
    vision: bool = False
    json_mode: bool = False
    reasoning: bool = False
    usage: bool = False
    cost: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {name: bool(getattr(self, name)) for name in CAPABILITY_NAMES}

    def narrowed(self, overrides: Mapping[str, object] | None) -> ProviderCapabilities:
        values: dict[str, bool] = {}
        for name, value in dict(overrides or {}).items():
            if name not in CAPABILITY_NAMES:
                raise ValueError(f"unknown provider capability '{name}'")
            if not isinstance(value, bool):
                raise ValueError(f"provider capability '{name}' must be boolean")
            if value and not getattr(self, name):
                raise ValueError(f"provider capability '{name}' is not supported by the adapter")
            values[name] = value
        return replace(self, **values)

    def for_model(
        self,
        overrides: Mapping[str, object] | None,
        *,
        is_chat: bool,
        has_complete_pricing: bool,
    ) -> ProviderCapabilities:
        if not is_chat:
            return ProviderCapabilities()
        effective = self.narrowed(overrides)
        return replace(
            effective,
            cost=effective.cost and effective.usage and has_complete_pricing,
        )


__all__ = ["CAPABILITY_NAMES", "CapabilityName", "ProviderCapabilities"]
