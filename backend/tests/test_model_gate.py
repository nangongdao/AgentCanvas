"""Public contracts for outbound model-call budgets and timeouts."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

import pytest

from app.core.model_budget import (
    ModelCallBudgetExceeded,
    ModelCallConcurrencyLimit,
    ModelCallConfig,
    ModelCallCostBudgetExceeded,
    ModelCallExecutionConcurrencyLimit,
    ModelCallGate,
    ModelCallRateLimit,
    ModelCallTimeout,
    ModelCallTokenBudgetExceeded,
    ModelCallUsage,
)
from app.providers.base import BaseChatProvider, ChatMessage, StreamChunk, Usage


class _ScriptedProvider(BaseChatProvider):
    def __init__(self, *, delay: float = 0, started: asyncio.Event | None = None) -> None:
        super().__init__(model="scripted")
        self.delay = delay
        self.started = started

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools=(),
        **params,
    ) -> AsyncIterator[StreamChunk]:
        if self.started is not None:
            self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        yield StreamChunk(type="text", text="ok")
        yield StreamChunk(type="done")


class _UsageProvider(BaseChatProvider):
    """Emits a scripted token usage chunk so budgets can price and bound it."""

    def __init__(
        self,
        *,
        prompt: int = 0,
        completion: int = 0,
        price_prompt: str | None = None,
        price_completion: str | None = None,
        version: str | None = None,
    ) -> None:
        super().__init__(
            model="usage",
            prompt_price_per_million_usd=price_prompt,
            completion_price_per_million_usd=price_completion,
            pricing_version=version,
        )
        self.prompt = prompt
        self.completion = completion

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools=(),
        **params,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(
            type="usage",
            usage=Usage(self.prompt, self.completion, self.prompt + self.completion),
        )
        yield StreamChunk(type="done")


class _MultiChunkUsageProvider(BaseChatProvider):
    """Anthropic-style stream splitting prompt/completion across usage chunks."""

    def __init__(
        self,
        *,
        prompt: int = 0,
        completion: int = 0,
        price_prompt: str | None = None,
        price_completion: str | None = None,
        version: str | None = None,
    ) -> None:
        super().__init__(
            model="multi-chunk",
            prompt_price_per_million_usd=price_prompt,
            completion_price_per_million_usd=price_completion,
            pricing_version=version,
        )
        self.prompt = prompt
        self.completion = completion

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools=(),
        **params,
    ) -> AsyncIterator[StreamChunk]:
        # ``message_start`` carries only prompt tokens, ``message_delta`` only
        # completion tokens; a last-usage-wins consumer would drop the prompt.
        yield StreamChunk(type="text", text="hello")
        yield StreamChunk(type="usage", usage=Usage(self.prompt, 0, self.prompt))
        yield StreamChunk(type="text", text=" world")
        yield StreamChunk(type="usage", usage=Usage(0, self.completion, self.completion))
        yield StreamChunk(type="done")


@pytest.mark.asyncio
async def test_model_call_gate_enforces_per_execution_budget() -> None:
    gate = ModelCallGate(
        ModelCallConfig(max_concurrent=2, max_calls_per_execution=1, timeout_seconds=1)
    )
    budget = gate.new_budget()
    provider = _ScriptedProvider()

    first = await budget.chat(provider, [])
    assert first.content == "ok"
    with pytest.raises(ModelCallBudgetExceeded):
        await budget.chat(provider, [])


@pytest.mark.asyncio
async def test_model_call_gate_applies_timeout_to_streams() -> None:
    gate = ModelCallGate(
        ModelCallConfig(max_concurrent=1, max_calls_per_execution=2, timeout_seconds=0.01)
    )
    budget = gate.new_budget()
    provider = _ScriptedProvider(delay=0.05)

    with pytest.raises(ModelCallTimeout):
        async for _chunk in budget.stream_chat(provider, []):
            pass

    recovered = await budget.chat(_ScriptedProvider(), [])
    assert recovered.content == "ok"


@pytest.mark.asyncio
async def test_model_call_gate_enforces_process_rate_window() -> None:
    gate = ModelCallGate(
        ModelCallConfig(
            max_concurrent=2,
            max_calls_per_execution=3,
            timeout_seconds=1,
            max_calls_per_window=1,
            window_seconds=60,
        )
    )
    budget = gate.new_budget()
    provider = _ScriptedProvider()

    await budget.chat(provider, [])
    with pytest.raises(ModelCallRateLimit) as exc_info:
        await budget.chat(provider, [])

    assert exc_info.value.retry_after >= 1


@pytest.mark.asyncio
async def test_model_call_budget_is_reused_for_an_execution_resume() -> None:
    gate = ModelCallGate(
        ModelCallConfig(max_concurrent=2, max_calls_per_execution=1, timeout_seconds=1)
    )
    provider = _ScriptedProvider()

    await gate.budget_for("execution-1").chat(provider, [])
    with pytest.raises(ModelCallBudgetExceeded):
        await gate.budget_for("execution-1").chat(provider, [])

    gate.discard_budget("execution-1")
    await gate.budget_for("execution-1").chat(provider, [])


@pytest.mark.asyncio
async def test_model_call_budget_restores_durable_usage_after_process_restart() -> None:
    config = ModelCallConfig(
        max_concurrent=2,
        max_calls_per_execution=2,
        timeout_seconds=1,
    )
    provider = _UsageProvider(
        prompt=2,
        completion=3,
        price_prompt="1",
        price_completion="2",
        version="price-v1",
    )
    first_gate = ModelCallGate(config)
    await first_gate.budget_for("execution-1").chat(provider, [])
    durable = ModelCallUsage.from_dict(
        first_gate.budget_for("execution-1").snapshot().to_dict()
    )
    assert durable is not None
    assert durable.model_calls == 1

    restarted_gate = ModelCallGate(config)
    restored = restarted_gate.budget_for("execution-1", initial_usage=durable)
    await restored.chat(provider, [])

    snapshot = restored.snapshot()
    assert snapshot.model_calls == 2
    assert snapshot.prompt_tokens == 4
    assert snapshot.completion_tokens == 6
    assert snapshot.total_tokens == 10
    assert snapshot.estimated_cost_usd == "0.000016000000"
    assert snapshot.price_versions == ("price-v1",)
    with pytest.raises(ModelCallBudgetExceeded):
        await restored.chat(provider, [])


@pytest.mark.asyncio
async def test_model_call_gate_rejects_saturated_process_concurrency() -> None:
    gate = ModelCallGate(
        ModelCallConfig(max_concurrent=1, max_calls_per_execution=2, timeout_seconds=1)
    )
    started = asyncio.Event()
    first = asyncio.create_task(
        gate.new_budget().chat(_ScriptedProvider(delay=0.05, started=started), [])
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    with pytest.raises(ModelCallConcurrencyLimit):
        await gate.new_budget().chat(_ScriptedProvider(), [])

    assert (await first).content == "ok"


@pytest.mark.asyncio
async def test_model_call_gate_enforces_token_ceiling() -> None:
    gate = ModelCallGate(
        ModelCallConfig(
            max_concurrent=2,
            max_calls_per_execution=5,
            timeout_seconds=1,
            max_tokens_per_execution=3,
        )
    )
    budget = gate.new_budget()
    provider = _UsageProvider(prompt=2, completion=2)

    with pytest.raises(ModelCallTokenBudgetExceeded):
        await budget.chat(provider, [])

    assert budget.breach is not None
    assert budget.breach.kind == "tokens"
    assert budget.breach.limit_value == "3"
    assert budget.breach.actual_value == "4"
    assert budget.snapshot().total_tokens == 4


@pytest.mark.asyncio
async def test_model_call_gate_enforces_cost_ceiling() -> None:
    gate = ModelCallGate(
        ModelCallConfig(
            max_concurrent=2,
            max_calls_per_execution=5,
            timeout_seconds=1,
            max_cost_usd_per_execution="0.000001",
        )
    )
    budget = gate.new_budget()
    provider = _UsageProvider(prompt=100, price_prompt="10")

    with pytest.raises(ModelCallCostBudgetExceeded):
        await budget.chat(provider, [])

    assert budget.breach is not None
    assert budget.breach.kind == "cost"
    assert budget.breach.limit_value == "0.000001"
    snapshot = budget.snapshot()
    assert snapshot.cost_known is True
    assert snapshot.estimated_cost_usd == "0.001000000000"


@pytest.mark.asyncio
async def test_cost_ceiling_ignored_when_pricing_is_unknown() -> None:
    gate = ModelCallGate(
        ModelCallConfig(
            max_concurrent=2,
            max_calls_per_execution=5,
            timeout_seconds=1,
            max_cost_usd_per_execution="0.000001",
        )
    )
    budget = gate.new_budget()
    provider = _UsageProvider(prompt=100)  # usage present, pricing absent

    result = await budget.chat(provider, [])
    assert result.content == ""
    snapshot = budget.snapshot()
    assert snapshot.cost_known is False
    assert snapshot.estimated_cost_usd is None
    assert budget.breach is None


@pytest.mark.asyncio
async def test_model_call_budget_accumulates_usage_and_price_versions() -> None:
    gate = ModelCallGate(
        ModelCallConfig(max_concurrent=2, max_calls_per_execution=5, timeout_seconds=1)
    )
    budget = gate.new_budget()
    provider = _UsageProvider(
        prompt=10,
        completion=5,
        price_prompt="1",
        price_completion="2",
        version="v1",
    )

    await budget.chat(provider, [])
    await budget.chat(provider, [])

    snapshot = budget.snapshot()
    assert snapshot.prompt_tokens == 20
    assert snapshot.completion_tokens == 10
    assert snapshot.total_tokens == 30
    assert snapshot.cost_known is True
    assert snapshot.price_versions == ("v1",)
    # (20 * 1 + 10 * 2) / 1_000_000 USD, quantized to the cost quantum.
    assert snapshot.estimated_cost_usd == "0.000040000000"
    assert budget.breach is None


@pytest.mark.asyncio
async def test_stream_accumulates_multi_chunk_usage_totals() -> None:
    gate = ModelCallGate(
        ModelCallConfig(max_concurrent=2, max_calls_per_execution=5, timeout_seconds=1)
    )
    budget = gate.new_budget()
    provider = _MultiChunkUsageProvider(
        prompt=10,
        completion=5,
        price_prompt="1",
        price_completion="2",
        version="v2",
    )

    parts: list[str] = []
    async for chunk in budget.stream_chat(provider, []):
        if chunk.type == "text":
            parts.append(chunk.text)

    assert "".join(parts) == "hello world"
    snapshot = budget.snapshot()
    assert snapshot.prompt_tokens == 10
    assert snapshot.completion_tokens == 5
    assert snapshot.total_tokens == 15
    assert snapshot.cost_known is True
    assert snapshot.price_versions == ("v2",)
    # (10 * 1 + 5 * 2) / 1_000_000 USD, quantized to the cost quantum.
    assert snapshot.estimated_cost_usd == "0.000020000000"
    assert budget.breach is None


@pytest.mark.asyncio
async def test_abandoned_stream_still_counts_partial_usage_chunks() -> None:
    gate = ModelCallGate(
        ModelCallConfig(
            max_concurrent=2,
            max_calls_per_execution=5,
            timeout_seconds=1,
            max_cost_usd_per_execution="0.000005",
        )
    )
    budget = gate.new_budget()
    provider = _MultiChunkUsageProvider(prompt=100, price_prompt="10")

    # The prompt-only usage chunk arrives before the completion chunk; the cost
    # ceiling must trip on the partial usage and count it toward the snapshot.
    with pytest.raises(ModelCallCostBudgetExceeded):
        async for _chunk in budget.stream_chat(provider, []):
            pass

    assert budget.breach is not None
    assert budget.breach.kind == "cost"
    assert budget.snapshot().prompt_tokens == 100


@pytest.mark.asyncio
async def test_model_call_budget_enforces_per_execution_concurrency() -> None:
    gate = ModelCallGate(
        ModelCallConfig(
            max_concurrent=2,
            max_calls_per_execution=5,
            timeout_seconds=1,
            max_concurrent_per_execution=1,
        )
    )
    budget = gate.new_budget()
    started = asyncio.Event()
    first = asyncio.create_task(
        budget.chat(_ScriptedProvider(delay=0.05, started=started), [])
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    with pytest.raises(ModelCallExecutionConcurrencyLimit):
        await budget.chat(_ScriptedProvider(), [])

    assert budget.breach is not None
    assert budget.breach.kind == "concurrency"
    assert (await first).content == "ok"


def test_model_call_config_validates_cost_governance_fields() -> None:
    base = dict(max_concurrent=2, max_calls_per_execution=5, timeout_seconds=1)
    with pytest.raises(ValueError):
        ModelCallConfig(
            max_concurrent=2,
            max_calls_per_execution=5,
            timeout_seconds=1,
            max_tokens_per_execution=-1,
        )
    with pytest.raises(ValueError):
        ModelCallConfig(
            max_concurrent=2,
            max_calls_per_execution=5,
            timeout_seconds=1,
            max_concurrent_per_execution=-1,
        )
    with pytest.raises(ValueError):
        ModelCallConfig(
            max_concurrent=2,
            max_calls_per_execution=5,
            timeout_seconds=1,
            max_cost_usd_per_execution="abc",
        )
    with pytest.raises(ValueError):
        ModelCallConfig(
            max_concurrent=2,
            max_calls_per_execution=5,
            timeout_seconds=1,
            max_cost_usd_per_execution="-0.01",
        )

    valid = ModelCallConfig(
        **base,
        max_tokens_per_execution=100,
        max_cost_usd_per_execution="0.01",
    )
    assert valid.max_tokens_per_execution == 100
    assert valid.max_cost_usd_per_execution == "0.01"
