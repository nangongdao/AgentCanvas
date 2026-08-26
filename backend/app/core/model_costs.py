"""Exact model-call pricing primitives and the optional metering protocol."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol

from app.providers.base import BaseChatProvider, Usage

COST_QUANTUM_USD = Decimal("0.000000000001")
TOKENS_PER_MILLION = Decimal(1_000_000)


class ModelCostMeter(Protocol):
    """Project or account meter attached to one execution budget."""

    async def preflight(self, provider: BaseChatProvider) -> None: ...

    async def record(self, provider: BaseChatProvider, usage: Usage | None) -> None: ...


def parse_nonnegative_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


def provider_rates(
    provider: BaseChatProvider,
) -> tuple[Decimal | None, Decimal | None]:
    return (
        parse_nonnegative_decimal(getattr(provider, "prompt_price_per_million_usd", None)),
        parse_nonnegative_decimal(getattr(provider, "completion_price_per_million_usd", None)),
    )


def has_complete_pricing(provider: BaseChatProvider) -> bool:
    prompt_rate, completion_rate = provider_rates(provider)
    return prompt_rate is not None and completion_rate is not None


def priced_usage_cost(provider: BaseChatProvider, usage: Usage | None) -> Decimal | None:
    if usage is None:
        return None
    prompt_rate, completion_rate = provider_rates(provider)
    if (usage.prompt_tokens and prompt_rate is None) or (
        usage.completion_tokens and completion_rate is None
    ):
        return None
    return (
        Decimal(usage.prompt_tokens) * (prompt_rate or Decimal(0))
        + Decimal(usage.completion_tokens) * (completion_rate or Decimal(0))
    ) / TOKENS_PER_MILLION


def format_cost_usd(value: Decimal) -> str:
    return format(value.quantize(COST_QUANTUM_USD, rounding=ROUND_HALF_UP), "f")


def usd_to_cost_units(value: Decimal) -> int:
    units = (value / COST_QUANTUM_USD).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(units)


def cost_units_to_usd(units: int) -> str:
    return format_cost_usd(Decimal(units) * COST_QUANTUM_USD)


__all__ = [
    "COST_QUANTUM_USD",
    "ModelCostMeter",
    "cost_units_to_usd",
    "format_cost_usd",
    "has_complete_pricing",
    "parse_nonnegative_decimal",
    "priced_usage_cost",
    "provider_rates",
    "usd_to_cost_units",
]
