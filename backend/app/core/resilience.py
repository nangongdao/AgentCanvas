"""Resilience primitives for outbound integrations.

The registry deliberately sits outside provider and MCP implementations. It
owns the failure policy while callers decide which exceptions are safe to
retry. This keeps provider-specific protocol code focused on transport and
keeps fault injection deterministic in tests without adding a production
control endpoint. Horizontal deployments can persist the state machine in
Redis while local development remains process-local.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import uuid4

T = TypeVar("T")
Clock = Callable[[], float]
Retryable = Callable[[Exception], bool]

_REDIS_RESILIENCE_PREFIX = "agentcanvas:resilience:"
_REDIS_BEFORE_SCRIPT = """
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
local server_time = redis.call('TIME')
local now = tonumber(server_time[1]) * 1000 + math.floor(tonumber(server_time[2]) / 1000)
local reset = tonumber(ARGV[1])
local token = ARGV[2]
local probe_lease = tonumber(ARGV[6])
local idle_ttl = math.ceil(math.max(reset, tonumber(ARGV[5]), probe_lease) / 1000) + 1
redis.call('HSET', KEYS[1], 'reset_timeout_ms', reset, 'failure_threshold', ARGV[3], 'retry_budget_limit', ARGV[4], 'retry_window_ms', ARGV[5], 'half_open_lease_ms', probe_lease, 'idle_ttl_seconds', idle_ttl)
if state == 'open' then
  redis.call('PERSIST', KEYS[1])
  local opened = tonumber(redis.call('HGET', KEYS[1], 'opened_at') or '0')
  if now - opened < reset then
    return {0, 0, opened + reset - now}
  end
  local held = redis.call('HGET', KEYS[1], 'half_open_token') or ''
  local probe_started = tonumber(redis.call('HGET', KEYS[1], 'half_open_started_at') or '0')
  if held ~= '' and now - probe_started < probe_lease then
    return {0, 1, 0}
  end
  redis.call('HSET', KEYS[1], 'state', 'half_open', 'half_open_token', token, 'half_open_started_at', now)
  return {1, 1, 0}
end
if state == 'half_open' then
  redis.call('PERSIST', KEYS[1])
  local held = redis.call('HGET', KEYS[1], 'half_open_token') or ''
  local probe_started = tonumber(redis.call('HGET', KEYS[1], 'half_open_started_at') or '0')
  if held ~= '' and held ~= token and now - probe_started < probe_lease then
    return {0, 1, 0}
  end
  redis.call('HSET', KEYS[1], 'half_open_token', token, 'half_open_started_at', now)
  return {1, 1, 0}
end
redis.call('EXPIRE', KEYS[1], idle_ttl)
return {1, 0, 0}
"""
_REDIS_SUCCESS_SCRIPT = """
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
local held = redis.call('HGET', KEYS[1], 'half_open_token') or ''
local token = ARGV[1]
redis.call('HINCRBY', KEYS[1], 'total_successes', 1)
if state == 'open' then
  return 0
end
if state == 'half_open' and (token == '' or held ~= token) then
  return 0
end
if state == 'closed' and token ~= '' then
  return 0
end
redis.call('HSET', KEYS[1], 'state', 'closed', 'consecutive_failures', 0, 'opened_at', 0, 'half_open_token', '', 'half_open_started_at', 0)
local idle_ttl = tonumber(redis.call('HGET', KEYS[1], 'idle_ttl_seconds') or '60')
redis.call('EXPIRE', KEYS[1], idle_ttl)
return 1
"""
_REDIS_FAILURE_SCRIPT = """
local server_time = redis.call('TIME')
local now = tonumber(server_time[1]) * 1000 + math.floor(tonumber(server_time[2]) / 1000)
redis.call('HINCRBY', KEYS[1], 'total_failures', 1)
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
local held = redis.call('HGET', KEYS[1], 'half_open_token') or ''
local token = ARGV[2]
if state == 'open' then
  return 0
end
if state == 'half_open' and (token == '' or held ~= token) then
  return 0
end
if state == 'closed' and token ~= '' then
  return 0
end
local failures = redis.call('HINCRBY', KEYS[1], 'consecutive_failures', 1)
local open = state == 'half_open' or failures >= tonumber(ARGV[1])
if open then
  redis.call('HSET', KEYS[1], 'state', 'open', 'opened_at', now, 'half_open_token', '', 'half_open_started_at', 0)
  redis.call('PERSIST', KEYS[1])
  return 1
end
redis.call('HSET', KEYS[1], 'state', 'closed', 'half_open_token', '', 'half_open_started_at', 0)
local idle_ttl = tonumber(redis.call('HGET', KEYS[1], 'idle_ttl_seconds') or '60')
redis.call('EXPIRE', KEYS[1], idle_ttl)
return 1
"""
_REDIS_CANCEL_SCRIPT = """
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
local held = redis.call('HGET', KEYS[1], 'half_open_token') or ''
if state == 'half_open' and ARGV[1] ~= '' and held == ARGV[1] then
  redis.call('HSET', KEYS[1], 'half_open_token', '', 'half_open_started_at', 0)
  redis.call('PERSIST', KEYS[1])
end
return 1
"""
_REDIS_BUDGET_SCRIPT = """
local server_time = redis.call('TIME')
local now = tonumber(server_time[1]) * 1000 + math.floor(tonumber(server_time[2]) / 1000)
local cutoff = now - tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, cutoff)
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
  return 0
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('EXPIRE', KEYS[1], math.ceil(tonumber(ARGV[1]) / 1000) + 1)
return 1
"""


class CircuitOpenError(RuntimeError):
    """Raised when a resource is temporarily unavailable behind an open circuit."""

    def __init__(self, key: str) -> None:
        super().__init__(f"resilience circuit is open for '{key}'")
        self.key = key


class RetryBudgetExceeded(RuntimeError):
    """Raised when a resource has exhausted its retry budget."""

    def __init__(self, key: str) -> None:
        super().__init__(f"resilience retry budget exhausted for '{key}'")
        self.key = key


class ResilienceBackendUnavailable(RuntimeError):
    """Raised when configured shared resilience state cannot be reached."""


class InjectedResilienceFault(RuntimeError):
    """Synthetic transient fault used by deterministic resilience tests."""


@dataclass(frozen=True, slots=True)
class ResilienceConfig:
    """Retry and circuit defaults for one class of outbound resource."""

    max_attempts: int = 2
    failure_threshold: int = 3
    reset_timeout_seconds: float = 30.0
    half_open_probe_timeout_seconds: float = 60.0
    retry_budget: int = 32
    retry_window_seconds: float = 60.0
    backoff_seconds: float = 0.05
    max_backoff_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        if self.reset_timeout_seconds <= 0:
            raise ValueError("reset_timeout_seconds must be positive")
        if self.half_open_probe_timeout_seconds <= 0:
            raise ValueError("half_open_probe_timeout_seconds must be positive")
        if self.retry_budget < 0:
            raise ValueError("retry_budget must be non-negative")
        if self.retry_window_seconds <= 0:
            raise ValueError("retry_window_seconds must be positive")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")
        if self.max_backoff_seconds < self.backoff_seconds:
            raise ValueError("max_backoff_seconds must not be below backoff_seconds")


@dataclass(frozen=True, slots=True)
class ResilienceSnapshot:
    """Serializable health state for one resource key."""

    key: str
    state: str
    consecutive_failures: int
    total_failures: int
    total_successes: int
    retry_budget_used: int
    retry_budget_limit: int
    cooldown_remaining_seconds: float
    injected_failures_remaining: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "state": self.state,
            "consecutive_failures": self.consecutive_failures,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
            "retry_budget_used": self.retry_budget_used,
            "retry_budget_limit": self.retry_budget_limit,
            "cooldown_remaining_seconds": self.cooldown_remaining_seconds,
            "injected_failures_remaining": self.injected_failures_remaining,
        }


class _CircuitBreaker:
    def __init__(self, config: ResilienceConfig, *, clock: Clock) -> None:
        self.config = config
        self._clock = clock
        self._lock = asyncio.Lock()
        self._state = "closed"
        self._consecutive_failures = 0
        self._total_failures = 0
        self._total_successes = 0
        self._opened_at: float | None = None
        self._half_open_in_flight = False

    async def before_call(self, key: str) -> None:
        now = self._clock()
        async with self._lock:
            if self._state == "open":
                opened_at = self._opened_at if self._opened_at is not None else now
                if now - opened_at < self.config.reset_timeout_seconds:
                    raise CircuitOpenError(key)
                self._state = "half_open"
                self._half_open_in_flight = False
            if self._state == "half_open":
                if self._half_open_in_flight:
                    raise CircuitOpenError(key)
                self._half_open_in_flight = True

    async def record_success(self) -> None:
        async with self._lock:
            self._total_successes += 1
            self._consecutive_failures = 0
            self._state = "closed"
            self._opened_at = None
            self._half_open_in_flight = False

    async def record_failure(self) -> None:
        now = self._clock()
        async with self._lock:
            self._total_failures += 1
            self._consecutive_failures += 1
            self._half_open_in_flight = False
            if self._state == "half_open" or (
                self._state == "closed"
                and self._consecutive_failures >= self.config.failure_threshold
            ):
                self._state = "open"
                self._opened_at = now

    async def cancel_call(self) -> None:
        """Release a half-open probe when its task is cancelled."""
        async with self._lock:
            self._half_open_in_flight = False

    def snapshot(self, key: str, *, now: float) -> ResilienceSnapshot:
        cooldown = 0.0
        if self._state == "open" and self._opened_at is not None:
            cooldown = max(0.0, self.config.reset_timeout_seconds - (now - self._opened_at))
        state = self._state
        if state == "open" and cooldown == 0:
            state = "half_open"
        return ResilienceSnapshot(
            key=key,
            state=state,
            consecutive_failures=self._consecutive_failures,
            total_failures=self._total_failures,
            total_successes=self._total_successes,
            retry_budget_used=0,
            retry_budget_limit=self.config.retry_budget,
            cooldown_remaining_seconds=round(cooldown, 3),
            injected_failures_remaining=0,
        )


class _RetryBudget:
    def __init__(self, config: ResilienceConfig, *, clock: Clock) -> None:
        self.config = config
        self._clock = clock
        self._lock = asyncio.Lock()
        self._timestamps: deque[float] = deque()

    async def consume(self) -> bool:
        now = self._clock()
        cutoff = now - self.config.retry_window_seconds
        async with self._lock:
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.config.retry_budget:
                return False
            self._timestamps.append(now)
            return True

    def used(self, *, now: float) -> int:
        cutoff = now - self.config.retry_window_seconds
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()
        return len(self._timestamps)


class ResilienceRegistry:
    """Own circuit/retry state, optionally shared through Redis.

    SQLite and unit-test deployments keep the deterministic in-process state
    machine. Horizontal roles pass ``redis_url`` so all API/worker replicas
    coordinate circuit transitions and retry budgets atomically.
    """

    def __init__(
        self,
        *,
        clock: Clock = time.monotonic,
        redis_url: str = "",
        redis_client: Any | None = None,
    ) -> None:
        self._clock = clock
        self._entries: dict[str, tuple[_CircuitBreaker, _RetryBudget]] = {}
        self._faults: dict[str, int] = {}
        self._redis = redis_client
        self._owns_redis = False
        if self._redis is None and redis_url:
            from redis.asyncio import Redis

            self._redis = Redis.from_url(redis_url, decode_responses=True, protocol=2)
            self._owns_redis = True

    @property
    def shared(self) -> bool:
        return self._redis is not None

    async def close(self) -> None:
        if self._owns_redis and self._redis is not None:
            await self._redis.aclose()
            self._redis = None
            self._owns_redis = False

    def _entry(self, key: str, config: ResilienceConfig) -> tuple[_CircuitBreaker, _RetryBudget]:
        existing = self._entries.get(key)
        if existing is not None:
            return existing
        entry = (
            _CircuitBreaker(config, clock=self._clock),
            _RetryBudget(config, clock=self._clock),
        )
        self._entries[key] = entry
        return entry

    async def _eval_shared(self, script: str, key: str, *args: object) -> Any:
        redis = self._redis
        if redis is None:
            raise ResilienceBackendUnavailable("shared resilience backend unavailable")
        try:
            return await redis.eval(script, 1, key, *args)
        except Exception as exc:  # noqa: BLE001 - shared state is fail-closed
            raise ResilienceBackendUnavailable("shared resilience backend unavailable") from exc

    def inject_failures(self, key: str, count: int) -> None:
        """Inject exactly ``count`` failures before the real operation runs."""
        if count < 0:
            raise ValueError("fault count must be non-negative")
        self._faults[key] = count

    def clear_failures(self, key: str) -> None:
        self._faults.pop(key, None)

    def _consume_fault(self, key: str) -> bool:
        remaining = self._faults.get(key, 0)
        if remaining <= 0:
            return False
        self._faults[key] = remaining - 1
        return True

    async def _backoff(self, config: ResilienceConfig, retry_number: int) -> None:
        delay = min(config.max_backoff_seconds, config.backoff_seconds * (2 ** (retry_number - 1)))
        if delay:
            await asyncio.sleep(delay)

    async def execute(
        self,
        key: str,
        operation: Callable[[], Awaitable[T]],
        *,
        config: ResilienceConfig,
        retryable: Retryable,
        max_attempts: int | None = None,
    ) -> T:
        """Run an idempotent outbound operation under retry and circuit policy."""
        breaker, budget = self._entry(key, config)
        attempts = max_attempts if max_attempts is not None else config.max_attempts
        if attempts < 1:
            raise ValueError("max_attempts must be positive")
        for attempt in range(1, attempts + 1):
            token = await self._before_call(key, config, breaker)
            try:
                if self._consume_fault(key):
                    raise InjectedResilienceFault(f"injected resilience fault for '{key}'")
                result = await operation()
            except asyncio.CancelledError:
                await self._cancel_call(key, token, breaker)
                raise
            except Exception as exc:
                await self._record_failure(key, config, token, breaker)
                if attempt >= attempts or not retryable(exc):
                    raise
                if not await self._consume_budget(key, config, budget):
                    raise RetryBudgetExceeded(key) from exc
                await self._backoff(config, attempt)
            else:
                await self._record_success(key, token, breaker)
                return result
        raise AssertionError("resilience execute loop exited without a result")

    async def stream(
        self,
        key: str,
        operation: Callable[[], AsyncIterator[T]],
        *,
        config: ResilienceConfig,
        retryable: Retryable,
    ) -> AsyncIterator[T]:
        """Retry a stream only before its first item to avoid duplicate output."""
        breaker, budget = self._entry(key, config)
        emitted = False
        for attempt in range(1, config.max_attempts + 1):
            token = await self._before_call(key, config, breaker)
            try:
                if self._consume_fault(key):
                    raise InjectedResilienceFault(f"injected resilience fault for '{key}'")
                async for item in operation():
                    emitted = True
                    yield item
            except asyncio.CancelledError:
                await self._cancel_call(key, token, breaker)
                raise
            except Exception as exc:
                await self._record_failure(key, config, token, breaker)
                if emitted or attempt >= config.max_attempts or not retryable(exc):
                    raise
                if not await self._consume_budget(key, config, budget):
                    raise RetryBudgetExceeded(key) from exc
                await self._backoff(config, attempt)
            else:
                await self._record_success(key, token, breaker)
                return

    async def _before_call(
        self,
        key: str,
        config: ResilienceConfig,
        breaker: _CircuitBreaker,
    ) -> str | None:
        if self._redis is None:
            await breaker.before_call(key)
            return None
        token = uuid4().hex
        result = await self._eval_shared(
            _REDIS_BEFORE_SCRIPT,
            self._state_key(key),
            max(1, int(config.reset_timeout_seconds * 1000)),
            token,
            config.failure_threshold,
            config.retry_budget,
            max(1, int(config.retry_window_seconds * 1000)),
            max(1, int(config.half_open_probe_timeout_seconds * 1000)),
        )
        if not isinstance(result, (list, tuple)) or len(result) != 3:
            raise ResilienceBackendUnavailable("invalid shared resilience response")
        if not int(result[0]):
            raise CircuitOpenError(key)
        return token if int(result[1]) else ""

    async def _record_success(
        self,
        key: str,
        token: str | None,
        breaker: _CircuitBreaker,
    ) -> None:
        if self._redis is None:
            await breaker.record_success()
            return
        await self._eval_shared(_REDIS_SUCCESS_SCRIPT, self._state_key(key), token or "")

    async def _record_failure(
        self,
        key: str,
        config: ResilienceConfig,
        token: str | None,
        breaker: _CircuitBreaker,
    ) -> None:
        if self._redis is None:
            await breaker.record_failure()
            return
        await self._eval_shared(
            _REDIS_FAILURE_SCRIPT,
            self._state_key(key),
            config.failure_threshold,
            token or "",
        )

    async def _cancel_call(
        self,
        key: str,
        token: str | None,
        breaker: _CircuitBreaker,
    ) -> None:
        if self._redis is None:
            await breaker.cancel_call()
            return
        await self._eval_shared(_REDIS_CANCEL_SCRIPT, self._state_key(key), token or "")

    async def _consume_budget(
        self,
        key: str,
        config: ResilienceConfig,
        budget: _RetryBudget,
    ) -> bool:
        if self._redis is None:
            return await budget.consume()
        result = await self._eval_shared(
            _REDIS_BUDGET_SCRIPT,
            self._budget_key(key),
            max(1, int(config.retry_window_seconds * 1000)),
            config.retry_budget,
            uuid4().hex,
        )
        return bool(int(result))

    @staticmethod
    def _state_key(key: str) -> str:
        return f"{_REDIS_RESILIENCE_PREFIX}state:{key}"

    @staticmethod
    def _budget_key(key: str) -> str:
        return f"{_REDIS_RESILIENCE_PREFIX}budget:{key}"

    async def snapshots_async(self, *, prefix: str | None = None) -> list[dict[str, Any]]:
        """Return shared snapshots when Redis is configured."""
        if self._redis is None:
            return self.snapshots(prefix=prefix)
        try:
            server_time = await self._redis.time()
            now_ms = int(server_time[0]) * 1000 + int(server_time[1]) // 1000
            match = f"{_REDIS_RESILIENCE_PREFIX}state:{prefix or ''}*"
            result: list[dict[str, Any]] = []
            async for state_key in self._redis.scan_iter(match=match):
                raw_key = str(state_key)
                key = raw_key.removeprefix(f"{_REDIS_RESILIENCE_PREFIX}state:")
                fields = await self._redis.hgetall(raw_key)
                if not fields:
                    continue
                opened_at = int(fields.get("opened_at", 0) or 0)
                reset_ms = int(fields.get("reset_timeout_ms", 0) or 0)
                cooldown = max(0.0, (opened_at + reset_ms - now_ms) / 1000)
                if fields.get("state", "closed") == "open" and cooldown == 0:
                    state = "half_open"
                else:
                    state = str(fields.get("state", "closed"))
                budget_key = self._budget_key(key)
                window_ms = int(fields.get("retry_window_ms", 0) or 0)
                if window_ms:
                    await self._redis.zremrangebyscore(budget_key, 0, now_ms - window_ms)
                result.append(
                    {
                        "key": key,
                        "state": state,
                        "consecutive_failures": int(fields.get("consecutive_failures", 0) or 0),
                        "total_failures": int(fields.get("total_failures", 0) or 0),
                        "total_successes": int(fields.get("total_successes", 0) or 0),
                        "retry_budget_used": int(await self._redis.zcard(budget_key)),
                        "retry_budget_limit": int(fields.get("retry_budget_limit", 0) or 0),
                        "cooldown_remaining_seconds": round(cooldown, 3),
                        "injected_failures_remaining": 0,
                    }
                )
            return sorted(result, key=lambda item: item["key"])
        except Exception as exc:  # noqa: BLE001 - shared state is fail-closed
            raise ResilienceBackendUnavailable("shared resilience backend unavailable") from exc

    def snapshots(self, *, prefix: str | None = None) -> list[dict[str, Any]]:
        now = self._clock()
        result: list[dict[str, Any]] = []
        for key, (breaker, budget) in sorted(self._entries.items()):
            if prefix is not None and not key.startswith(prefix):
                continue
            snapshot = breaker.snapshot(key, now=now)
            result.append(
                {
                    **snapshot.to_dict(),
                    "retry_budget_used": budget.used(now=now),
                    "injected_failures_remaining": self._faults.get(key, 0),
                }
            )
        return result

    def snapshot(self, key: str) -> dict[str, Any] | None:
        for item in self.snapshots():
            if item["key"] == key:
                return item
        return None

    async def snapshot_async(self, key: str) -> dict[str, Any] | None:
        for item in await self.snapshots_async(prefix=key):
            if item["key"] == key:
                return item
        return None


_HTTP_5XX = re.compile(r"\bHTTP\s+5\d{2}\b", re.IGNORECASE)


def is_transient_error(exc: Exception) -> bool:
    """Classify transport failures while leaving provider policy-specific errors alone."""
    current: BaseException | None = exc
    seen: set[int] = set()
    for _ in range(4):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        if isinstance(current, (TimeoutError, OSError, ConnectionError)):
            return True
        status_code = getattr(getattr(current, "response", None), "status_code", None)
        if isinstance(status_code, int) and 500 <= status_code < 600:
            return True
        if isinstance(current, RuntimeError) and _HTTP_5XX.search(str(current)):
            return True
        if isinstance(current, InjectedResilienceFault):
            return True
        current = current.__cause__ or current.__context__
    return False


__all__ = [
    "CircuitOpenError",
    "InjectedResilienceFault",
    "ResilienceConfig",
    "ResilienceBackendUnavailable",
    "ResilienceRegistry",
    "ResilienceSnapshot",
    "RetryBudgetExceeded",
    "is_transient_error",
]
