"""Single-instance request rate limiting for FastAPI."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RateLimitConfig:
    max_requests: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.max_requests < 1 or self.window_seconds < 1:
            raise ValueError("rate-limit values must be positive")


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    """A named request bucket selected by path pattern and optional methods."""

    name: str
    pattern: re.Pattern[str]
    config: RateLimitConfig
    methods: frozenset[str] | None = None

    @classmethod
    def regex(
        cls,
        name: str,
        pattern: str,
        config: RateLimitConfig,
        *,
        methods: Collection[str] | None = None,
    ) -> RateLimitRule:
        return cls(
            name=name,
            pattern=re.compile(pattern),
            config=config,
            methods=(frozenset(method.upper() for method in methods) if methods else None),
        )

    @classmethod
    def prefix(
        cls,
        name: str,
        prefix: str,
        config: RateLimitConfig,
        *,
        methods: Collection[str] | None = None,
    ) -> RateLimitRule:
        pattern = rf"^{re.escape(prefix.rstrip('/'))}(?:/|$)"
        return cls.regex(name, pattern, config, methods=methods)

    def matches(self, request: Request) -> bool:
        return (self.methods is None or request.method in self.methods) and self.pattern.search(
            request.url.path
        ) is not None


@dataclass(frozen=True, slots=True)
class _RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after: int


@dataclass(slots=True)
class _Window:
    timestamps: deque[float]
    window_seconds: int


class _SlidingWindowCounter:
    """Concurrency-safe sliding windows keyed by client and route family."""

    __slots__ = ("_windows", "_lock", "_last_cleanup", "_max_buckets")

    def __init__(self, *, max_buckets: int) -> None:
        if max_buckets < 1:
            raise ValueError("max_buckets must be positive")
        self._windows: OrderedDict[str, _Window] = OrderedDict()
        self._lock = asyncio.Lock()
        self._last_cleanup = time.monotonic()
        self._max_buckets = max_buckets

    async def check(
        self, key: str, *, max_requests: int, window_seconds: int
    ) -> _RateLimitDecision:
        now = time.monotonic()
        cutoff = now - window_seconds
        async with self._lock:
            self._cleanup(now)
            state = self._windows.get(key)
            if state is None:
                if len(self._windows) >= self._max_buckets:
                    self._windows.popitem(last=False)
                state = _Window(deque(), window_seconds)
                self._windows[key] = state
            else:
                state.window_seconds = window_seconds
                self._windows.move_to_end(key)
            timestamps = state.timestamps
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if len(timestamps) >= max_requests:
                retry_after = max(
                    1,
                    math.ceil(timestamps[0] + window_seconds - now),
                )
                return _RateLimitDecision(False, 0, retry_after)
            timestamps.append(now)
            return _RateLimitDecision(
                True,
                max_requests - len(timestamps),
                0,
            )

    def _cleanup(self, now: float) -> None:
        if now - self._last_cleanup < 60:
            return
        for key, state in list(self._windows.items()):
            cutoff = now - state.window_seconds
            while state.timestamps and state.timestamps[0] <= cutoff:
                state.timestamps.popleft()
            if not state.timestamps:
                del self._windows[key]
        self._last_cleanup = now


_REDIS_SLIDING_WINDOW_SCRIPT = """
local now = tonumber(ARGV[1])
local cutoff = now - tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, cutoff)
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
  local first = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  local retry = tonumber(first[2]) + tonumber(ARGV[2]) - now
  return {0, 0, math.max(1, math.ceil(retry))}
end
redis.call('ZADD', KEYS[1], now, ARGV[4])
redis.call('EXPIRE', KEYS[1], math.ceil(tonumber(ARGV[2]) / 1000) + 1)
return {1, limit - count - 1, 0}
"""


class RateLimitBackendUnavailable(RuntimeError):
    """Raised when a configured shared rate-limit backend cannot be reached."""


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Limit each client by the first matching configured route rule.

    All dynamic child paths under a prefix intentionally share one bucket, so
    changing a resource ID cannot bypass the configured limit. A configured
    Redis client makes the bucket shared across horizontal API processes;
    development and SQLite mode retain the bounded local counter.
    """

    _EXEMPT_PATHS = frozenset({"/", "/favicon.ico", "/healthz", "/livez", "/readyz"})

    def __init__(
        self,
        app: ASGIApp,
        configs: dict[str, RateLimitConfig] | None = None,
        rules: Sequence[RateLimitRule] = (),
        default_config: RateLimitConfig | None = None,
        max_buckets: int = 10_000,
        redis_client: Any | None = None,
    ) -> None:
        super().__init__(app)
        legacy_configs = configs or {}
        self._default_config = default_config or legacy_configs.get(
            "default", RateLimitConfig(200, 60)
        )
        legacy_rules = [
            RateLimitRule.prefix(prefix, prefix, config)
            for prefix, config in sorted(
                legacy_configs.items(), key=lambda item: len(item[0]), reverse=True
            )
            if prefix != "default"
        ]
        self._rules = (*rules, *legacy_rules)
        self._counter = _SlidingWindowCounter(max_buckets=max_buckets)
        self._redis = redis_client

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if request.method == "OPTIONS" or path in self._EXEMPT_PATHS:
            return await call_next(request)

        bucket, config = self._match_config(request)
        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{bucket}"
        try:
            decision = (
                await self._redis_check(
                    key,
                    max_requests=config.max_requests,
                    window_seconds=config.window_seconds,
                )
                if self._redis is not None
                else await self._counter.check(
                    key,
                    max_requests=config.max_requests,
                    window_seconds=config.window_seconds,
                )
            )
        except Exception:  # noqa: BLE001 - fail closed for shared limits
            if self._redis is None:
                raise
            logger.error("shared rate-limit backend unavailable", exc_info=True)
            return JSONResponse(
                status_code=503,
                content={"detail": "Rate-limit backend unavailable"},
                headers={"Retry-After": "1"},
            )
        headers = {
            "X-RateLimit-Limit": str(config.max_requests),
            "X-RateLimit-Remaining": str(decision.remaining),
        }
        if not decision.allowed:
            logger.warning("rate limit exceeded for %s in bucket %s", client_ip, bucket)
            headers["Retry-After"] = str(decision.retry_after)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests",
                    "retry_after": decision.retry_after,
                },
                headers=headers,
            )

        response = await call_next(request)
        response.headers.update(headers)
        return response

    async def _redis_check(
        self,
        key: str,
        *,
        max_requests: int,
        window_seconds: int,
    ) -> _RateLimitDecision:
        redis = self._redis
        if redis is None:
            raise RateLimitBackendUnavailable("Redis rate-limit client is not configured")
        now_ms = time.time_ns() // 1_000_000
        result = await redis.eval(
            _REDIS_SLIDING_WINDOW_SCRIPT,
            1,
            f"agentcanvas:rate-limit:{key}",
            now_ms,
            window_seconds * 1000,
            max_requests,
            f"{now_ms}:{uuid4().hex}",
        )
        if not isinstance(result, (list, tuple)) or len(result) != 3:
            raise RateLimitBackendUnavailable("invalid Redis rate-limit response")
        return _RateLimitDecision(bool(int(result[0])), int(result[1]), int(result[2]))

    def _match_config(self, request: Request) -> tuple[str, RateLimitConfig]:
        for rule in self._rules:
            if rule.matches(request):
                return rule.name, rule.config
        return "default", self._default_config
