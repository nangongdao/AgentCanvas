"""Provider defaults and effective model capability profiles."""

from __future__ import annotations

from collections.abc import Mapping

from app.core.provider_capabilities import CAPABILITY_NAMES, ProviderCapabilities
from app.db.models import ModelConfig
from app.providers import provider_default_capabilities


class ModelCapabilityError(ValueError):
    """Raised when a persisted or requested model profile is invalid."""


def provider_capabilities(provider_name: str) -> ProviderCapabilities:
    try:
        return provider_default_capabilities(provider_name)
    except KeyError as exc:
        raise ModelCapabilityError(f"unknown provider '{provider_name}'") from exc


def normalize_capability_overrides(
    provider_name: str,
    overrides: Mapping[str, object] | None,
) -> dict[str, bool]:
    """Return the minimal persisted profile, containing only disabled defaults."""
    defaults = provider_capabilities(provider_name)
    try:
        defaults.narrowed(overrides)
    except ValueError as exc:
        raise ModelCapabilityError(str(exc)) from exc
    requested = dict(overrides or {})
    return {
        name: False
        for name in CAPABILITY_NAMES
        if requested.get(name) is False and bool(getattr(defaults, name))
    }


def normalize_model_capability_overrides(
    provider_name: str,
    kind: str,
    overrides: Mapping[str, object] | None,
) -> dict[str, bool]:
    requested = dict(overrides or {})
    if kind != "chat":
        if requested:
            raise ModelCapabilityError("embedding models cannot define chat capability overrides")
        provider_capabilities(provider_name)
        return {}
    return normalize_capability_overrides(provider_name, requested)


def effective_model_capabilities(row: ModelConfig) -> ProviderCapabilities:
    defaults = provider_capabilities(row.provider)
    try:
        return defaults.for_model(
            row.capabilities_json,
            is_chat=row.kind == "chat",
            has_complete_pricing=(
                row.prompt_price_per_million_usd is not None
                and row.completion_price_per_million_usd is not None
            ),
        )
    except ValueError as exc:
        raise ModelCapabilityError(str(exc)) from exc


__all__ = [
    "ModelCapabilityError",
    "effective_model_capabilities",
    "normalize_capability_overrides",
    "normalize_model_capability_overrides",
    "provider_capabilities",
]
