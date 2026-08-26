"""LLM provider abstractions and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from app.core.provider_capabilities import CapabilityName, ProviderCapabilities


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON string


@dataclass(frozen=True)
class ToolCallDelta:
    index: int
    id: str | None = None
    name: str | None = None
    arguments_delta: str = ""


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def merge_usage(left: Usage, right: Usage) -> Usage:
    """Sum two usage chunks.

    Providers emit usage either as one cumulative chunk (OpenAI/Ollama) or as
    several partial chunks that together form the total (Anthropic streams
    prompt tokens at ``message_start`` and completion tokens at
    ``message_delta``). Summing is correct for both shapes, so budgets never
    drop a provider's prompt or completion side.
    """
    return Usage(
        prompt_tokens=left.prompt_tokens + right.prompt_tokens,
        completion_tokens=left.completion_tokens + right.completion_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )


@dataclass(frozen=True)
class StreamChunk:
    type: Literal["text", "tool_call_delta", "usage", "done"]
    text: str = ""
    tool_call: ToolCallDelta | None = None
    usage: Usage | None = None


@dataclass(frozen=True)
class ChatResult:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class BaseChatProvider(ABC):
    """Unified streaming chat interface across providers."""

    name: ClassVar[str] = "base"
    default_capabilities: ClassVar[ProviderCapabilities] = ProviderCapabilities(stream=True)

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        base_url: str | None = None,
        default_params: dict[str, Any] | None = None,
        prompt_price_per_million_usd: str | None = None,
        completion_price_per_million_usd: str | None = None,
        pricing_version: str | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.default_params = default_params or {}
        self.capabilities = self.default_capabilities
        # D2 cost governance: pricing rides on the provider so the per-execution
        # budget can price each call without a second database lookup. These
        # attributes default to None and are populated by create_chat_provider
        # from the ModelConfig row (prompt/completion USD-per-million).
        self.prompt_price_per_million_usd = prompt_price_per_million_usd
        self.completion_price_per_million_usd = completion_price_per_million_usd
        self.pricing_version = pricing_version

    def prepare_params(
        self,
        required_capabilities: Collection[CapabilityName],
        params: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        missing = sorted(
            name
            for name in required_capabilities
            if not bool(getattr(self.capabilities, name, False))
        )
        if missing:
            raise RuntimeError(
                f"provider '{self.name}' lacks required capabilities: {', '.join(missing)}"
            )
        prepared = dict(params or {})
        prepared.pop("json_mode", None)
        return prepared

    @abstractmethod
    def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] = (),
        **params: Any,
    ) -> AsyncIterator[StreamChunk]:
        """Yield stream chunks; must end with a done chunk when possible."""

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] = (),
        **params: Any,
    ) -> ChatResult:
        content_parts: list[str] = []
        tool_acc: dict[int, dict[str, str]] = {}
        usage: Usage | None = None
        async for chunk in self.stream_chat(messages, tools=tools, **params):
            if chunk.type == "text" and chunk.text:
                content_parts.append(chunk.text)
            elif chunk.type == "tool_call_delta" and chunk.tool_call is not None:
                d = chunk.tool_call
                slot = tool_acc.setdefault(d.index, {"id": "", "name": "", "arguments": ""})
                if d.id:
                    slot["id"] = d.id
                if d.name:
                    slot["name"] = d.name
                if d.arguments_delta:
                    slot["arguments"] += d.arguments_delta
            elif chunk.type == "usage" and chunk.usage is not None:
                usage = chunk.usage if usage is None else merge_usage(usage, chunk.usage)
        tool_calls = tuple(
            ToolCall(id=v["id"] or f"call_{i}", name=v["name"], arguments=v["arguments"])
            for i, v in sorted(tool_acc.items())
            if v["name"]
        )
        return ChatResult(content="".join(content_parts), tool_calls=tool_calls, usage=usage)


PROVIDERS: dict[str, type[BaseChatProvider]] = {}


def register_provider(name: str):
    def decorator(cls: type[BaseChatProvider]) -> type[BaseChatProvider]:
        cls.name = name  # type: ignore[misc]
        PROVIDERS[name] = cls
        return cls

    return decorator


def get_provider_class(name: str) -> type[BaseChatProvider]:
    if name not in PROVIDERS:
        raise KeyError(f"Unknown provider: {name}. Registered: {list(PROVIDERS)}")
    return PROVIDERS[name]
