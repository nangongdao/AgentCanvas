"""Configurable request-body and model-call safety policies.

The request middleware is a small pure-ASGI guard so it can count streamed
request bodies before FastAPI parses them.  The model-call budget/gate moved to
``app.core.model_budget`` and is re-exported here for callers that already
imported it from this module.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.model_budget import (
    COST_QUANTUM_USD,
    ModelCallBreach,
    ModelCallBudget,
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


@dataclass(frozen=True, slots=True)
class RequestPolicyConfig:
    """Limits applied to one request operation family."""

    max_body_bytes: int | None = None
    max_concurrent: int | None = None
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.max_body_bytes is not None and self.max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")
        if self.max_concurrent is not None and self.max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class RequestPolicyRule:
    """First-match path policy with optional method filtering."""

    name: str
    pattern: re.Pattern[str]
    config: RequestPolicyConfig
    methods: frozenset[str] | None = None

    @classmethod
    def regex(
        cls,
        name: str,
        pattern: str,
        config: RequestPolicyConfig,
        *,
        methods: Collection[str] | None = None,
    ) -> RequestPolicyRule:
        return cls(
            name=name,
            pattern=re.compile(pattern),
            config=config,
            methods=(frozenset(method.upper() for method in methods) if methods else None),
        )

    def matches(self, scope: Scope) -> bool:
        method = scope.get("method", "GET")
        path = scope.get("path", "")
        return (self.methods is None or method in self.methods) and self.pattern.search(path) is not None


class RequestPolicyMiddleware:
    """Apply body, concurrency, and timeout limits to HTTP operations."""

    _EXEMPT_PATHS = frozenset({"/", "/favicon.ico", "/healthz", "/livez", "/readyz"})

    def __init__(
        self,
        app: ASGIApp,
        *,
        default_config: RequestPolicyConfig | None = None,
        rules: Sequence[RequestPolicyRule] = (),
    ) -> None:
        self.app = app
        self.default_config = default_config or RequestPolicyConfig()
        self.rules = tuple(rules)
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if scope.get("method") == "OPTIONS" or path in self._EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        name, config = self._match(scope)
        content_length = _content_length(scope)
        if config.max_body_bytes is not None and content_length > config.max_body_bytes:
            await _send_json(
                send,
                413,
                {
                    "detail": "request body exceeds configured limit",
                    "max_bytes": config.max_body_bytes,
                },
            )
            return

        semaphore: asyncio.Semaphore | None = None
        if config.max_concurrent is not None:
            semaphore = self._semaphores.setdefault(name, asyncio.Semaphore(config.max_concurrent))
            if semaphore.locked():
                await _send_json(
                    send,
                    429,
                    {
                        "detail": "operation concurrency limit reached",
                        "operation": name,
                    },
                    headers=((b"retry-after", b"1"),),
                )
                return
            await semaphore.acquire()

        response_started = False

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        received_bytes = 0

        async def guarded_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message.get("type") == "http.request":
                received_bytes += len(message.get("body", b""))
                if (
                    config.max_body_bytes is not None
                    and received_bytes > config.max_body_bytes
                ):
                    raise _RequestBodyTooLarge(config.max_body_bytes)
            return message

        try:
            call = self.app(scope, guarded_receive, tracked_send)
            if config.timeout_seconds is None:
                await call
            else:
                await asyncio.wait_for(call, timeout=config.timeout_seconds)
        except _RequestBodyTooLarge as exc:
            if not response_started:
                await _send_json(
                    send,
                    413,
                    {"detail": "request body exceeds configured limit", "max_bytes": exc.limit},
                )
            else:
                raise
        except TimeoutError:
            if not response_started:
                await _send_json(send, 504, {"detail": "operation timed out"})
            else:
                raise
        finally:
            if semaphore is not None:
                semaphore.release()

    def _match(self, scope: Scope) -> tuple[str, RequestPolicyConfig]:
        for rule in self.rules:
            if rule.matches(scope):
                return rule.name, rule.config
        return "default", self.default_config


class _RequestBodyTooLarge(RuntimeError):
    def __init__(self, limit: int) -> None:
        super().__init__(str(limit))
        self.limit = limit


def _content_length(scope: Scope) -> int:
    for key, value in scope.get("headers", []):
        if key.lower() == b"content-length":
            try:
                return max(0, int(value))
            except ValueError:
                return 0
    return 0


async def _send_json(
    send: Send,
    status: int,
    payload: dict[str, Any],
    *,
    headers: Collection[tuple[bytes, bytes]] = (),
) -> None:
    body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    response_headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        *headers,
    ]
    await send({"type": "http.response.start", "status": status, "headers": response_headers})
    await send({"type": "http.response.body", "body": body})


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
    "RequestPolicyConfig",
    "RequestPolicyMiddleware",
    "RequestPolicyRule",
]
