"""Exact model loading and Provider lifecycle for workflow execution."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Iterable
from typing import Any

from app.core.secret_providers import SecretResolver
from app.db.models import ModelConfig
from app.db.repositories import ModelConfigRepo
from app.providers import BaseChatProvider, create_chat_provider
from app.providers.mock_provider import MockChatProvider
from app.schemas.dsl import WorkflowDSL
from app.services.model_capabilities import effective_model_capabilities
from app.services.workflow_capabilities import workflow_model_ids

logger = logging.getLogger(__name__)


async def _close_providers(providers: Iterable[BaseChatProvider]) -> None:
    seen: set[int] = set()
    for provider in providers:
        if id(provider) in seen:
            continue
        seen.add(id(provider))
        close = getattr(provider, "aclose", None)
        if close is None:
            continue
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - cleanup must not mask run finalization
            logger.warning("failed to close Provider '%s'", provider.name, exc_info=True)


class ProviderRuntimeMixin:
    def _provider_from_row(self: Any, row: ModelConfig) -> BaseChatProvider:
        resolver = getattr(self, "secret_resolver", None)
        if resolver is None:
            if self.secret_box is None:
                raise RuntimeError("secret resolver is not configured")
            resolver = SecretResolver(self.secret_box)
        api_key = resolver.decrypt(row.api_key_encrypted or "")
        if row.id == "default" and row.provider == "openai_compat" and not api_key:
            logger.warning(
                "seeded default model has no API key; using the protocol-capable demo adapter"
            )
            provider = MockChatProvider(
                model=row.model_name,
                default_params=dict(row.params_json or {}),
            )
            provider.capabilities = effective_model_capabilities(row)
            provider.prompt_price_per_million_usd = row.prompt_price_per_million_usd
            provider.completion_price_per_million_usd = row.completion_price_per_million_usd
            provider.pricing_version = row.pricing_version
            return provider
        return create_chat_provider(row, self.secret_box, secret_resolver=resolver)

    async def load_workflow_providers(
        self: Any,
        dsl: WorkflowDSL,
    ) -> dict[str, BaseChatProvider]:
        model_ids = set(workflow_model_ids(dsl))
        async with self.session_factory() as session:
            rows = await ModelConfigRepo(session).get_many(model_ids)
        missing = sorted(model_ids - set(rows))
        if missing:
            raise RuntimeError(f"workflow model(s) not found: {', '.join(missing)}")

        providers: dict[str, BaseChatProvider] = {}
        try:
            for model_id in sorted(model_ids):
                row = rows[model_id]
                if row.kind != "chat":
                    raise RuntimeError(
                        f"workflow model '{model_id}' is '{row.kind}', expected 'chat'"
                    )
                providers[model_id] = self._provider_from_row(row)
        except Exception:
            await _close_providers(providers.values())
            raise
        return providers

    async def close_workflow_providers(
        self: Any,
        providers: dict[str, BaseChatProvider],
    ) -> None:
        await _close_providers(providers.values())

    async def load_provider(self: Any, model_config_id: str) -> BaseChatProvider:
        cached = self._provider_cache.get(model_config_id)
        if cached is not None:
            return cached
        async with self.session_factory() as session:
            row = await ModelConfigRepo(session).get(model_config_id)
        if row is None:
            raise KeyError(model_config_id)
        if row.kind != "chat":
            raise ValueError(f"model '{model_config_id}' is '{row.kind}', expected 'chat'")
        provider = self._provider_from_row(row)
        self._provider_cache[model_config_id] = provider
        return provider

    async def invalidate_provider(self: Any, model_config_id: str) -> None:
        provider = self._provider_cache.pop(model_config_id, None)
        if provider is not None:
            await _close_providers((provider,))

    async def _close_cached_providers(self: Any) -> None:
        providers = tuple(self._provider_cache.values())
        self._provider_cache.clear()
        await _close_providers(providers)


__all__ = ["ProviderRuntimeMixin"]
