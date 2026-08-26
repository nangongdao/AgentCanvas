"""Per-execution outbound model-call budgets, ceilings, and pricing.

The gate is scoped to a single process and execution; distributed accounting
remains an I1 concern. Budgets ride on the provider pricing attributes so the
cost ceiling can price each call without a second database lookup.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.model_costs import (
    COST_QUANTUM_USD,
    ModelCostMeter,
    format_cost_usd,
    parse_nonnegative_decimal,
    priced_usage_cost,
)
from app.providers.base import (
    BaseChatProvider,
    ChatMessage,
    ChatResult,
    StreamChunk,
    ToolSchema,
    Usage,
)


@dataclass(frozen=True, slots=True)
class ModelCallConfig:
    """Per-process outbound model-call ceilings."""

    max_concurrent: int
    max_calls_per_execution: int
    timeout_seconds: float
    max_calls_per_window: int = 120
    window_seconds: float = 60.0
    # Cost is an exact decimal string so money never travels through float.
    max_concurrent_per_execution: int = 0
    max_tokens_per_execution: int = 0
    max_cost_usd_per_execution: str | None = None

    def __post_init__(self) -> None:
        if self.max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        if self.max_calls_per_execution < 1:
            raise ValueError("max_calls_per_execution must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_calls_per_window < 1:
            raise ValueError("max_calls_per_window must be positive")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if self.max_concurrent_per_execution < 0:
            raise ValueError("max_concurrent_per_execution must be non-negative")
        if self.max_tokens_per_execution < 0:
            raise ValueError("max_tokens_per_execution must be non-negative")
        if (
            self.max_cost_usd_per_execution is not None
            and parse_nonnegative_decimal(self.max_cost_usd_per_execution) is None
        ):
            raise ValueError("max_cost_usd_per_execution must be a non-negative decimal string")


class ModelCallBudgetExceeded(RuntimeError):
    """One execution exceeded its outbound model-call budget."""


class ModelCallTokenBudgetExceeded(RuntimeError):
    """One execution exceeded its configured per-execution token ceiling."""


class ModelCallCostBudgetExceeded(RuntimeError):
    """One execution exceeded its configured per-execution cost ceiling."""


class ModelCallConcurrencyLimit(RuntimeError):
    """The process is already serving the configured number of model calls."""


class ModelCallExecutionConcurrencyLimit(RuntimeError):
    """One execution is already running the configured number of model calls."""


class ModelCallRateLimit(RuntimeError):
    """The process exceeded its outbound model-call window."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("model call rate limit reached")
        self.retry_after = retry_after


class ModelCallTimeout(TimeoutError):
    """An outbound model call exceeded its configured operation timeout."""


@dataclass(frozen=True, slots=True)
class ModelCallBreach:
    """Durable record of the ceiling that stopped an execution."""

    kind: str  # calls | tokens | cost | concurrency
    limit_value: str
    actual_value: str
    message: str


@dataclass(frozen=True, slots=True)
class ModelCallUsage:
    """Cumulative token and priced-cost snapshot for one execution."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: str | None
    cost_known: bool
    price_versions: tuple[str, ...] = ()
    model_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "cost_known": self.cost_known,
            "price_versions": list(self.price_versions),
            "model_calls": self.model_calls,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> ModelCallUsage | None:
        """Validate and normalize a durable usage snapshot."""
        if not isinstance(raw, Mapping):
            return None

        def count(name: str, *, default: int | None = None) -> int | None:
            value = raw.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            return value

        prompt_tokens = count("prompt_tokens")
        completion_tokens = count("completion_tokens")
        total_tokens = count("total_tokens")
        model_calls = count("model_calls", default=0)
        cost_known = raw.get("cost_known")
        raw_versions = raw.get("price_versions", [])
        if (
            prompt_tokens is None
            or completion_tokens is None
            or total_tokens is None
            or model_calls is None
            or not isinstance(cost_known, bool)
            or not isinstance(raw_versions, list)
            or any(not isinstance(version, str) for version in raw_versions)
        ):
            return None

        estimated_cost_usd: str | None = None
        if cost_known:
            parsed_cost = parse_nonnegative_decimal(raw.get("estimated_cost_usd"))
            if parsed_cost is None:
                return None
            estimated_cost_usd = format_cost_usd(parsed_cost)

        return cls(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost_usd,
            cost_known=cost_known,
            price_versions=tuple(sorted(set(raw_versions))),
            model_calls=model_calls,
        )


class ModelCallGate:
    """Create execution-scoped budgets sharing one process-wide semaphore."""

    def __init__(self, config: ModelCallConfig) -> None:
        self.config = config
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        self._rate_lock = asyncio.Lock()
        self._timestamps: deque[float] = deque()
        self._budgets: dict[str, ModelCallBudget] = {}

    def new_budget(
        self,
        *,
        cost_meter: ModelCostMeter | None = None,
        initial_usage: ModelCallUsage | None = None,
    ) -> ModelCallBudget:
        return ModelCallBudget(self, cost_meter=cost_meter, initial_usage=initial_usage)

    def budget_for(
        self,
        execution_id: str,
        *,
        cost_meter: ModelCostMeter | None = None,
        initial_usage: ModelCallUsage | None = None,
    ) -> ModelCallBudget:
        """Return a stable budget, restoring a snapshot only on first creation."""
        budget = self._budgets.get(execution_id)
        if budget is None:
            budget = ModelCallBudget(
                self,
                cost_meter=cost_meter,
                initial_usage=initial_usage,
            )
            self._budgets[execution_id] = budget
        elif cost_meter is not None:
            budget._cost_meter = cost_meter
        return budget

    def discard_budget(self, execution_id: str) -> None:
        self._budgets.pop(execution_id, None)

    async def _reserve_rate(self) -> None:
        now = time.monotonic()
        cutoff = now - self.config.window_seconds
        async with self._rate_lock:
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.config.max_calls_per_window:
                retry_after = max(
                    1,
                    math.ceil(self._timestamps[0] + self.config.window_seconds - now),
                )
                raise ModelCallRateLimit(retry_after)
            self._timestamps.append(now)


class ModelCallBudget:
    """Count, price, and bound model calls for one workflow execution.

    The budget is keyed by execution id and reused across human pause/resume, so
    cumulative token/cost accounting and the concurrent-call ceiling survive an
    interrupt. It records the first breached ceiling so the runner can persist a
    durable cost alert.
    """

    def __init__(
        self,
        gate: ModelCallGate,
        *,
        cost_meter: ModelCostMeter | None = None,
        initial_usage: ModelCallUsage | None = None,
    ) -> None:
        self._gate = gate
        self._cost_meter = cost_meter
        self._calls = initial_usage.model_calls if initial_usage is not None else 0
        self._in_flight = 0
        self._lock = asyncio.Lock()
        self._prompt_tokens = initial_usage.prompt_tokens if initial_usage is not None else 0
        self._completion_tokens = (
            initial_usage.completion_tokens if initial_usage is not None else 0
        )
        self._total_tokens = initial_usage.total_tokens if initial_usage is not None else 0
        self._exact_cost = (
            Decimal(initial_usage.estimated_cost_usd)
            if initial_usage is not None and initial_usage.estimated_cost_usd is not None
            else Decimal(0)
        )
        self._cost_known = initial_usage.cost_known if initial_usage is not None else True
        self._price_versions: set[str] = (
            set(initial_usage.price_versions) if initial_usage is not None else set()
        )
        self._breach: ModelCallBreach | None = None

    @property
    def gate(self) -> ModelCallGate:
        return self._gate

    @property
    def breach(self) -> ModelCallBreach | None:
        return self._breach

    def snapshot(self) -> ModelCallUsage:
        """Return a stable cumulative usage/cost view of this execution."""
        return ModelCallUsage(
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            total_tokens=self._total_tokens,
            estimated_cost_usd=(format_cost_usd(self._exact_cost) if self._cost_known else None),
            cost_known=self._cost_known,
            price_versions=tuple(sorted(self._price_versions)),
            model_calls=self._calls,
        )

    async def chat(
        self,
        provider: BaseChatProvider,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] = (),
        **params: Any,
    ) -> ChatResult:
        await self._reserve()
        await self._gate._reserve_rate()
        await self._acquire_slot()
        try:
            await self._preflight_cost(provider)
            try:
                result = await asyncio.wait_for(
                    provider.chat(messages, tools=tools, **params),
                    timeout=self._gate.config.timeout_seconds,
                )
            except TimeoutError as exc:
                raise ModelCallTimeout("model call timed out") from exc
            await self._record_usage(provider, result.usage)
            self._check_ceilings()
            return result
        finally:
            await self._release_slot()

    async def stream_chat(
        self,
        provider: BaseChatProvider,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] = (),
        **params: Any,
    ) -> AsyncIterator[StreamChunk]:
        await self._reserve()
        await self._gate._reserve_rate()
        await self._acquire_slot()
        saw_usage = False
        try:
            await self._preflight_cost(provider)
            try:
                async with asyncio.timeout(self._gate.config.timeout_seconds):
                    async for chunk in provider.stream_chat(messages, tools=tools, **params):
                        if chunk.type == "usage" and chunk.usage is not None:
                            # Accumulate per-chunk (Anthropic splits prompt and
                            # completion across two usage chunks) and enforce
                            # ceilings as usage arrives, so an abandoned stream
                            # still counts and cannot overspend silently.
                            saw_usage = True
                            await self._record_usage(provider, chunk.usage)
                            self._check_ceilings()
                        yield chunk
                    if not saw_usage:
                        await self._record_usage(provider, None)
            except TimeoutError as exc:
                raise ModelCallTimeout("model call timed out") from exc
        finally:
            await self._release_slot()

    async def _reserve(self) -> None:
        async with self._lock:
            if self._calls >= self._gate.config.max_calls_per_execution:
                self._set_breach(
                    "calls",
                    str(self._gate.config.max_calls_per_execution),
                    str(self._calls + 1),
                    "model call budget exceeded",
                )
                raise ModelCallBudgetExceeded("model call budget exceeded")
            self._calls += 1

    async def _acquire_slot(self) -> None:
        config = self._gate.config
        max_per_execution = config.max_concurrent_per_execution
        async with self._lock:
            if max_per_execution > 0 and self._in_flight >= max_per_execution:
                self._set_breach(
                    "concurrency",
                    str(max_per_execution),
                    str(self._in_flight),
                    "per-execution model call concurrency limit reached",
                )
                raise ModelCallExecutionConcurrencyLimit(
                    "per-execution model call concurrency limit reached"
                )
            semaphore = self._gate._semaphore
            if semaphore.locked():
                raise ModelCallConcurrencyLimit("model call concurrency limit reached")
            # The lock and known-free permit make this acquire non-suspending.
            await semaphore.acquire()
            self._in_flight += 1

    async def _release_slot(self) -> None:
        async with self._lock:
            self._in_flight -= 1
        self._gate._semaphore.release()

    async def _preflight_cost(self, provider: BaseChatProvider) -> None:
        if self._cost_meter is not None:
            await self._cost_meter.preflight(provider)

    async def _record_usage(self, provider: BaseChatProvider, usage: Usage | None) -> None:
        self._accumulate(provider, usage)
        if self._cost_meter is not None:
            await self._cost_meter.record(provider, usage)

    def _accumulate(self, provider: BaseChatProvider, usage: Usage | None) -> None:
        """Add one completed call's usage and priced cost to the running totals."""
        if usage is None:
            self._cost_known = False
            return
        self._prompt_tokens += usage.prompt_tokens
        self._completion_tokens += usage.completion_tokens
        self._total_tokens += usage.total_tokens
        delta = priced_usage_cost(provider, usage)
        if delta is None:
            self._cost_known = False
        else:
            self._exact_cost += delta
        version = getattr(provider, "pricing_version", None)
        key = f"{provider.name}:{getattr(provider, 'model', 'unknown')}"
        self._price_versions.add(str(version or f"{key}:unversioned"))

    def _check_ceilings(self) -> None:
        """Fail the execution when a configured token/cost ceiling is crossed."""
        config = self._gate.config
        if config.max_tokens_per_execution > 0 and (
            self._total_tokens > config.max_tokens_per_execution
        ):
            self._set_breach(
                "tokens",
                str(config.max_tokens_per_execution),
                str(self._total_tokens),
                (
                    f"token budget exceeded: {self._total_tokens} "
                    f"> {config.max_tokens_per_execution}"
                ),
            )
            raise ModelCallTokenBudgetExceeded(
                f"token budget exceeded: {self._total_tokens} > {config.max_tokens_per_execution}"
            )
        limit = parse_nonnegative_decimal(config.max_cost_usd_per_execution)
        if limit is not None and self._cost_known and self._exact_cost > limit:
            actual = format_cost_usd(self._exact_cost)
            self._set_breach(
                "cost",
                format(limit, "f"),
                actual,
                (f"cost budget exceeded: {actual} > {format(limit, 'f')}"),
            )
            raise ModelCallCostBudgetExceeded(
                f"cost budget exceeded: {actual} > {format(limit, 'f')}"
            )

    def _set_breach(self, kind: str, limit_value: str, actual_value: str, message: str) -> None:
        if self._breach is None:
            self._breach = ModelCallBreach(kind, limit_value, actual_value, message)


__all__ = [
    "COST_QUANTUM_USD",
    "ModelCallBreach",
    "ModelCallBudget",
    "ModelCallBudgetExceeded",
    "ModelCallConcurrencyLimit",
    "ModelCallConfig",
    "ModelCallCostBudgetExceeded",
    "ModelCallExecutionConcurrencyLimit",
    "ModelCallGate",
    "ModelCallRateLimit",
    "ModelCallTimeout",
    "ModelCallTokenBudgetExceeded",
    "ModelCallUsage",
]
