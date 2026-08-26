"""Provider package: registry + factory."""

from __future__ import annotations

from typing import Any

from app.core.provider_capabilities import ProviderCapabilities
from app.core.secret_providers import SecretResolver
from app.core.security import SecretBox
from app.db.models import ModelConfig
from app.providers import anthropic_provider as _anthropic  # noqa: F401
from app.providers import mock_provider as _mock  # noqa: F401
from app.providers import ollama_provider as _ollama  # noqa: F401
from app.providers import openai_provider as _openai  # noqa: F401
from app.providers.base import (
    PROVIDERS,
    BaseChatProvider,
    ChatMessage,
    ChatResult,
    StreamChunk,
    ToolCall,
    ToolSchema,
    Usage,
    get_provider_class,
    register_provider,
)


def provider_default_capabilities(name: str) -> ProviderCapabilities:
    return get_provider_class(name).default_capabilities


def create_chat_provider(
    row: ModelConfig,
    secret_box: SecretBox,
    *,
    client: Any = None,
    secret_resolver: SecretResolver | None = None,
) -> BaseChatProvider:
    cls = get_provider_class(row.provider)
    resolver = secret_resolver or SecretResolver(secret_box)
    api_key = resolver.decrypt(row.api_key_encrypted or "")
    kwargs: dict[str, Any] = {
        "model": row.model_name,
        "api_key": api_key,
        "base_url": row.base_url,
        "default_params": dict(row.params_json or {}),
    }
    if client is not None and row.provider == "openai_compat":
        kwargs["client"] = client
    provider = cls(**kwargs)
    provider.capabilities = cls.default_capabilities.for_model(
        row.capabilities_json,
        is_chat=row.kind == "chat",
        has_complete_pricing=(
            row.prompt_price_per_million_usd is not None
            and row.completion_price_per_million_usd is not None
        ),
    )
    # D2 cost governance: carry the model-config pricing so the per-execution
    # budget can record price versions and enforce cost ceilings at call time.
    provider.prompt_price_per_million_usd = row.prompt_price_per_million_usd
    provider.completion_price_per_million_usd = row.completion_price_per_million_usd
    provider.pricing_version = row.pricing_version
    return provider


__all__ = [
    "PROVIDERS",
    "BaseChatProvider",
    "ChatMessage",
    "ChatResult",
    "StreamChunk",
    "ToolCall",
    "ToolSchema",
    "Usage",
    "create_chat_provider",
    "get_provider_class",
    "provider_default_capabilities",
    "register_provider",
]
