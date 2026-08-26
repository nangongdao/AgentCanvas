"""HTTP contracts for model configuration and capability governance."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.provider_capabilities import (
    ProviderCapabilitiesOut,
    ProviderCapabilityOverrides,
)


class ModelConfigOut(BaseModel):
    id: str
    name: str
    provider: str
    model_name: str
    base_url: str | None = None
    kind: str
    is_default: bool
    has_api_key: bool = False
    api_key_source: str = "none"
    prompt_price_per_million_usd: str | None = None
    completion_price_per_million_usd: str | None = None
    pricing_version: str | None = None
    capability_overrides: dict[str, bool] = Field(default_factory=dict)
    capabilities: ProviderCapabilitiesOut = Field(default_factory=ProviderCapabilitiesOut)
    capability_error: str | None = None


class ModelConfigCreate(BaseModel):
    id: str | None = Field(default=None, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=4096)
    params: dict[str, Any] = Field(default_factory=dict)
    prompt_price_per_million_usd: Decimal | None = Field(default=None, ge=0)
    completion_price_per_million_usd: Decimal | None = Field(default=None, ge=0)
    pricing_version: str | None = Field(default=None, max_length=120)
    capability_overrides: ProviderCapabilityOverrides = Field(
        default_factory=ProviderCapabilityOverrides
    )
    kind: Literal["chat", "embedding"] = "chat"
    is_default: bool = False


class ModelConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    model_name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=4096)
    params: dict[str, Any] | None = None
    prompt_price_per_million_usd: Decimal | None = Field(default=None, ge=0)
    completion_price_per_million_usd: Decimal | None = Field(default=None, ge=0)
    pricing_version: str | None = Field(default=None, max_length=120)
    capability_overrides: ProviderCapabilityOverrides | None = None
    kind: Literal["chat", "embedding"] | None = None
    is_default: bool | None = None


__all__ = ["ModelConfigCreate", "ModelConfigOut", "ModelConfigUpdate"]
