"""Node executor base class, registry and instrumentation wrapper."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from langgraph.types import Command
from pydantic import BaseModel

from app.core.logging import bind_log_context
from app.core.model_budget import ModelCallBudget
from app.core.observability import Observability
from app.core.provider_capabilities import CapabilityName
from app.core.resilience import (
    CircuitOpenError,
    ResilienceConfig,
    ResilienceRegistry,
    RetryBudgetExceeded,
    is_transient_error,
)
from app.engine.events import EventEmitter
from app.engine.state import WorkflowState
from app.providers.base import BaseChatProvider, ChatMessage, ChatResult, StreamChunk, ToolSchema
from app.schemas.dsl import NodeSpec, NodeType
from app.schemas.events import EventType
from app.services.execution_inspection import (
    bounded_error,
    bounded_json_snapshot,
    node_input_snapshot,
)

try:
    from langgraph.errors import GraphInterrupt
except ImportError:  # pragma: no cover - compatibility shim

    class GraphInterrupt(BaseException):  # type: ignore[no-redef]
        pass


logger = logging.getLogger(__name__)

NodeFn = Callable[[WorkflowState], Awaitable[dict[str, Any] | Command]]


def _provider_label(provider: BaseChatProvider) -> str:
    """Return stable telemetry labels for real and lightweight test providers."""
    return str(getattr(provider, "name", provider.__class__.__name__))


def _provider_model(provider: BaseChatProvider) -> str:
    return str(getattr(provider, "model", "unknown"))


def _provider_resilience_key(provider: BaseChatProvider) -> str:
    return f"provider:{_provider_label(provider)}:{_provider_model(provider)}"


@dataclass(frozen=True)
class CompileContext:
    """Services bound at compile time and closed over by node functions."""

    execution_id: str
    emitter: EventEmitter
    get_provider: Callable[[str], Any]
    max_loop_iterations: int = 20
    project_id: str | None = None
    # Optional services filled in later phases
    mcp_manager: Any = None
    rag_service: Any = None
    memory_store: Any = None
    settings: Any = None
    model_budget: ModelCallBudget | None = None
    observability: Observability | None = None
    resilience: ResilienceRegistry | None = None
    resilience_config: ResilienceConfig | None = None
    secret_resolver: Any = None
    # C8-1/C2-2: process-level sandbox for plugin/code-node subprocesses.
    # None defers to the plugin registry's own sandbox; code nodes require one.
    sandbox: Any = None
    # C2-3: optional test-only HTTP transport. When None, HTTP nodes build a
    # fresh SSRF-safe pinned client per request; tests inject an httpx.MockTransport.
    http_transport: Any = None
    # C2-5: synchronous lookup over pre-resolved subworkflow child DSLs. The
    # runner pre-walks the DSL in an async context (subworkflow_resolver) and
    # hands the compiler this lookup so the sync compile() never blocks on DB.
    # Signature: (workflow_id, version_id) -> WorkflowDSL | None.
    subworkflow_loader: Any = None
    # C3-4: durable chat-session variable store. Agent nodes configured with
    # ``session_writes`` persist multi-turn memory through it; ``None`` keeps
    # the legacy behavior (writes are skipped).
    # Protocol: async snapshot(session_id) / async write_many(session_id, values).
    session_variable_store: Any = None
    # C2-7: debug-run options. When ``debug_breakpoints`` is non-empty (or
    # ``debug_single_step`` is set) the instrument wrapper pauses the graph
    # after the node runs by calling LangGraph ``interrupt`` so the editor can
    # inspect/edit intermediate state before resuming. ``debug_single_step``
    # treats every node as a breakpoint.
    debug_breakpoints: frozenset[str] = frozenset()
    debug_single_step: bool = False

    async def model_chat(
        self,
        provider: BaseChatProvider,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] = (),
        **params: Any,
    ) -> ChatResult:
        started = time.perf_counter()
        status = "succeeded"
        result: ChatResult | None = None
        try:
            provider_label = _provider_label(provider)

            async def call_provider() -> ChatResult:
                if self.model_budget is None:
                    return await provider.chat(messages, tools=tools, **params)
                return await self.model_budget.chat(provider, messages, tools=tools, **params)

            async def resilient_call() -> ChatResult:
                if self.resilience is None:
                    return await call_provider()
                return await self.resilience.execute(
                    _provider_resilience_key(provider),
                    call_provider,
                    config=self.resilience_config or ResilienceConfig(),
                    retryable=is_transient_error,
                )

            with bind_log_context(provider=provider_label):
                if self.observability is None:
                    result = await resilient_call()
                else:
                    with self.observability.span(
                        "provider.chat",
                        attributes={
                            "gen_ai.provider.name": provider_label,
                            "gen_ai.request.model": _provider_model(provider),
                        },
                    ):
                        result = await resilient_call()
                return result
        except Exception:
            status = "failed"
            raise
        finally:
            if self.observability is not None:
                usage = result.usage if result is not None else None
                self.observability.record_provider(
                    _provider_label(provider),
                    status=status,
                    duration=time.perf_counter() - started,
                    prompt_tokens=usage.prompt_tokens if usage else 0,
                    completion_tokens=usage.completion_tokens if usage else 0,
                )

    async def _emit_provider_fallback(
        self,
        current: BaseChatProvider,
        fallback: BaseChatProvider,
        exc: Exception,
        *,
        node_id: str | None,
    ) -> None:
        await self.emitter.emit(
            EventType.NODE_STREAMING,
            node_id=node_id,
            payload={
                "kind": "provider_fallback",
                "from_provider": _provider_label(current),
                "from_model": _provider_model(current),
                "to_provider": _provider_label(fallback),
                "to_model": _provider_model(fallback),
                "reason": type(exc).__name__,
            },
        )

    @staticmethod
    def _can_fallback(exc: Exception) -> bool:
        return isinstance(exc, (CircuitOpenError, RetryBudgetExceeded)) or is_transient_error(exc)

    async def model_chat_chain(
        self,
        providers: Sequence[BaseChatProvider],
        messages: Sequence[ChatMessage],
        *,
        required_capabilities: Collection[CapabilityName],
        tools: Sequence[ToolSchema] = (),
        params: Mapping[str, Any] | None = None,
        node_id: str | None = None,
    ) -> ChatResult:
        """Use the first healthy capable Provider in an ordered fallback chain."""
        if not providers:
            raise ValueError("provider fallback chain cannot be empty")
        for index, provider in enumerate(providers):
            try:
                prepared = provider.prepare_params(required_capabilities, params)
                return await self.model_chat(provider, messages, tools=tools, **prepared)
            except Exception as exc:
                if index + 1 >= len(providers) or not self._can_fallback(exc):
                    raise
                fallback = providers[index + 1]
                await self._emit_provider_fallback(
                    provider,
                    fallback,
                    exc,
                    node_id=node_id,
                )
        raise AssertionError("provider fallback chain exited without a result")

    async def model_stream_chat(
        self,
        provider: BaseChatProvider,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] = (),
        **params: Any,
    ) -> AsyncIterator[StreamChunk]:
        started = time.perf_counter()
        first_token: float | None = None
        prompt_tokens = 0
        completion_tokens = 0
        status = "succeeded"

        async def chunks() -> AsyncIterator[StreamChunk]:
            def stream_provider() -> AsyncIterator[StreamChunk]:
                if self.model_budget is None:
                    return provider.stream_chat(messages, tools=tools, **params)
                return self.model_budget.stream_chat(provider, messages, tools=tools, **params)

            if self.resilience is None:
                async for item in stream_provider():
                    yield item
                return
            async for item in self.resilience.stream(
                _provider_resilience_key(provider),
                stream_provider,
                config=self.resilience_config or ResilienceConfig(),
                retryable=is_transient_error,
            ):
                yield item

        try:
            provider_label = _provider_label(provider)
            with bind_log_context(provider=provider_label):
                span_context = (
                    self.observability.span(
                        "provider.stream_chat",
                        attributes={
                            "gen_ai.provider.name": provider_label,
                            "gen_ai.request.model": _provider_model(provider),
                        },
                    )
                    if self.observability is not None
                    else None
                )
                if span_context is None:
                    async for chunk in chunks():
                        yield chunk
                    return
                with span_context:
                    async for chunk in chunks():
                        if first_token is None and chunk.type == "text" and chunk.text:
                            first_token = time.perf_counter()
                        if chunk.type == "usage" and chunk.usage is not None:
                            prompt_tokens = chunk.usage.prompt_tokens
                            completion_tokens = chunk.usage.completion_tokens
                        yield chunk
        except Exception:
            status = "failed"
            raise
        finally:
            if self.observability is not None:
                self.observability.record_provider(
                    _provider_label(provider),
                    status=status,
                    duration=time.perf_counter() - started,
                    ttft=(first_token - started) if first_token is not None else None,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )

    async def model_stream_chat_chain(
        self,
        providers: Sequence[BaseChatProvider],
        messages: Sequence[ChatMessage],
        *,
        required_capabilities: Collection[CapabilityName],
        params: Mapping[str, Any] | None = None,
        node_id: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Fallback only before a Provider emits its first stream chunk."""
        if not providers:
            raise ValueError("provider fallback chain cannot be empty")
        for index, provider in enumerate(providers):
            emitted = False
            try:
                prepared = provider.prepare_params(required_capabilities, params)
                async for chunk in self.model_stream_chat(provider, messages, **prepared):
                    emitted = True
                    yield chunk
                return
            except Exception as exc:
                if emitted or index + 1 >= len(providers) or not self._can_fallback(exc):
                    raise
                fallback = providers[index + 1]
                await self._emit_provider_fallback(
                    provider,
                    fallback,
                    exc,
                    node_id=node_id,
                )


class BaseNodeExecutor(ABC):
    node_type: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]
    # Optional declarative description of the node's runtime output shape, exposed
    # via ``/api/node-types`` so the editor can render an output-schema preview in
    # the data-flow pane. Nodes whose output is user-defined (code/iteration) or
    # passes-through (subworkflow) leave this empty and the pane falls back to
    # the live run snapshot.
    output_schema: ClassVar[dict[str, Any] | None] = None

    def metadata(self) -> dict[str, Any]:
        """Return the editor/runtime metadata exposed by ``/api/node-types``."""
        meta: dict[str, Any] = {
            "type": self.node_type,
            "label": self.node_type,
            "config_schema": self.config_model.model_json_schema(),
        }
        if self.output_schema is not None:
            meta["output_schema"] = self.output_schema
        return meta

    def validate_config(self, config: dict[str, Any]) -> None:
        """Validate node configuration before a workflow is persisted or compiled."""
        self.config_model.model_validate(config or {})

    @abstractmethod
    def build(self, node: NodeSpec, ctx: CompileContext) -> NodeFn:
        """Return an async node function closed over the node config + ctx."""


NODE_REGISTRY: dict[str, BaseNodeExecutor] = {}
_EXTERNAL_NODE_TYPES: set[str] = set()


def register_node(node_type: str):
    def decorator(cls: type[BaseNodeExecutor]) -> type[BaseNodeExecutor]:
        instance = cls()
        instance.node_type = node_type  # type: ignore[misc]
        NODE_REGISTRY[node_type] = instance
        return cls

    return decorator


def register_executor_instance(node_type: str, executor: BaseNodeExecutor) -> None:
    """Register a runtime executor supplied by a versioned external plugin."""
    existing = NODE_REGISTRY.get(node_type)
    if existing is not None and existing is not executor:
        existing_plugin_id = getattr(existing, "plugin_id", None)
        plugin_id = getattr(executor, "plugin_id", None)
        if not plugin_id or existing_plugin_id != plugin_id:
            raise ValueError(f"node type '{node_type}' is already registered")
    executor.node_type = node_type  # type: ignore[misc]
    NODE_REGISTRY[node_type] = executor
    _EXTERNAL_NODE_TYPES.add(node_type)


def clear_external_executors() -> None:
    """Remove dynamically loaded executors before a new app registry is loaded."""
    for node_type in tuple(_EXTERNAL_NODE_TYPES):
        NODE_REGISTRY.pop(node_type, None)
    _EXTERNAL_NODE_TYPES.clear()


def external_node_types() -> tuple[str, ...]:
    """Return dynamically registered node types for lifecycle/test inspection."""
    return tuple(sorted(_EXTERNAL_NODE_TYPES))


def get_executor(node_type: str | NodeType) -> BaseNodeExecutor:
    key = node_type.value if isinstance(node_type, NodeType) else node_type
    if key not in NODE_REGISTRY:
        raise KeyError(f"No executor registered for node type '{key}'")
    return NODE_REGISTRY[key]


class LoopLimitExceeded(RuntimeError):
    """Raised when a node is entered more times than max_loop_iterations."""


def instrument(node_id: str, fn: NodeFn, ctx: CompileContext) -> NodeFn:
    """Wrap a node fn to emit lifecycle events, time it, and enforce loop limits."""

    async def wrapped(state: WorkflowState) -> dict[str, Any] | Command:
        counts = dict(state.get("loop_counts") or {})
        counts[node_id] = counts.get(node_id, 0) + 1
        if counts[node_id] > ctx.max_loop_iterations:
            msg = (
                f"loop limit exceeded on node '{node_id}' "
                f"({counts[node_id]} > {ctx.max_loop_iterations})"
            )
            await ctx.emitter.emit(
                EventType.NODE_FAILED,
                node_id=node_id,
                payload={
                    "error": bounded_error(msg),
                    "node_path_segments": [node_id],
                },
            )
            raise LoopLimitExceeded(msg)

        await ctx.emitter.emit(
            EventType.NODE_STARTED,
            node_id=node_id,
            payload={
                "input": node_input_snapshot(state),
                "node_path_segments": [node_id],
            },
        )
        t0 = time.perf_counter()
        try:
            with bind_log_context(node_id=node_id):
                if ctx.observability is None:
                    result = await fn(state)
                else:
                    with ctx.observability.span(
                        "workflow.node",
                        attributes={"agentcanvas.node.id": node_id},
                    ):
                        result = await fn(state)
        except GraphInterrupt:
            raise
        except Exception as exc:
            elapsed = int((time.perf_counter() - t0) * 1000)
            await ctx.emitter.emit(
                EventType.NODE_FAILED,
                node_id=node_id,
                payload={
                    "error": bounded_error(exc),
                    "elapsed_ms": elapsed,
                    "node_path_segments": [node_id],
                },
            )
            raise

        elapsed = int((time.perf_counter() - t0) * 1000)
        # Extract output snapshot for the finished event
        output_preview: Any = None
        update: dict[str, Any] = {}
        if isinstance(result, Command):
            update = dict(result.update or {})
            output_preview = (update.get("node_outputs") or {}).get(node_id)
        elif isinstance(result, dict):
            update = result
            output_preview = (result.get("node_outputs") or {}).get(node_id)

        # Always merge loop count into the update
        loop_update = {"loop_counts": {node_id: counts[node_id]}}
        if isinstance(result, Command):
            merged_update = {**update, **loop_update}
            # If node_outputs already present, keep; loop_counts merge via reducer
            result = Command(update=merged_update, goto=result.goto, resume=result.resume)
        else:
            result = {**update, **loop_update}

        await ctx.emitter.emit(
            EventType.NODE_FINISHED,
            node_id=node_id,
            payload={
                "output": bounded_json_snapshot(output_preview),
                "elapsed_ms": elapsed,
                "node_path_segments": [node_id],
            },
        )
        return result

    return wrapped
