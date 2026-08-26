"""Pure Provider and model capability contract tests."""

from __future__ import annotations

import pytest

from app.core.provider_capabilities import ProviderCapabilities
from app.db.models import ModelConfig
from app.services.model_capabilities import (
    ModelCapabilityError,
    effective_model_capabilities,
    normalize_capability_overrides,
    provider_capabilities,
)


def test_builtin_provider_defaults_are_explicit_and_immutable() -> None:
    openai = provider_capabilities("openai_compat")
    assert openai.to_dict() == {
        "stream": True,
        "tools": True,
        "vision": False,
        "json_mode": True,
        "reasoning": True,
        "usage": True,
        "cost": True,
    }
    assert provider_capabilities("anthropic").json_mode is False
    assert provider_capabilities("ollama").reasoning is False
    assert provider_capabilities("mock") == ProviderCapabilities(stream=True, usage=True)
    with pytest.raises(AttributeError):
        openai.tools = False  # type: ignore[misc]


def test_overrides_only_persist_disabled_adapter_defaults() -> None:
    assert normalize_capability_overrides(
        "openai_compat",
        {"stream": True, "tools": False, "vision": False},
    ) == {"tools": False}
    with pytest.raises(ModelCapabilityError, match="not supported"):
        normalize_capability_overrides("anthropic", {"json_mode": True})
    with pytest.raises(ModelCapabilityError, match="must be boolean"):
        normalize_capability_overrides("openai_compat", {"tools": "false"})
    with pytest.raises(ModelCapabilityError, match="unknown provider capability"):
        normalize_capability_overrides("openai_compat", {"audio": False})
    with pytest.raises(ModelCapabilityError, match="unknown provider"):
        provider_capabilities("missing")


def test_effective_model_cost_requires_chat_usage_and_complete_pricing() -> None:
    row = ModelConfig(
        id="model",
        name="Model",
        provider="openai_compat",
        model_name="gpt-test",
        capabilities_json={},
        kind="chat",
        is_default=False,
    )
    assert effective_model_capabilities(row).cost is False
    row.prompt_price_per_million_usd = "1"
    row.completion_price_per_million_usd = "2"
    assert effective_model_capabilities(row).cost is True
    row.capabilities_json = {"usage": False}
    profile = effective_model_capabilities(row)
    assert profile.usage is False
    assert profile.cost is False
    row.kind = "embedding"
    assert effective_model_capabilities(row) == ProviderCapabilities()
