"""P5 memory store — conversation memory with Redis primary and SQLite fallback.

The AgentCanvas memory store keeps a per-session sliding-window of chat
exchanges so an Agent node can recall recent turns. Two backends are
supported; selection is decided at construction time by
``build_memory_store``:

* ``RedisMemoryStore`` — preferred when ``REDIS_URL`` is set and the server
  responds to ``PING``. Backed by a Redis list keyed per session id.
* ``SqliteMemoryStore`` — durable fallback when Redis is unavailable or
  not configured. Uses the application database (async SQLAlchemy) so it
  survives restarts and works in the default zero-dependency SQLite setup.

Both implementations expose the same async surface used by node executors:
``load(session_id, window)`` and ``append(session_id, role, content)``.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ChatMessageRow, ChatSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryEntry:
    """A single recalled memory item."""

    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class BaseMemoryStore(ABC):
    """Unified async memory interface."""

    backend_name: str = "base"

    @abstractmethod
    async def load(self, session_id: str, window: int = 10) -> list[MemoryEntry]:
        """Return the most recent ``window`` entries for a session."""

    @abstractmethod
    async def append(self, session_id: str, role: str, content: str) -> None:
        """Append one exchange to the session memory."""

    @abstractmethod
    async def reset(self, session_id: str) -> None:
        """Clear all memory for a session."""


class NullMemoryStore(BaseMemoryStore):
    """No-op store used when memory is disabled."""

    backend_name = "none"

    async def load(self, session_id: str, window: int = 10) -> list[MemoryEntry]:
        _ = session_id, window
        return []

    async def append(self, session_id: str, role: str, content: str) -> None:
        _ = session_id, role, content
        return None

    async def reset(self, session_id: str) -> None:
        _ = session_id
        return None


class SqliteMemoryStore(BaseMemoryStore):
    """Durable memory backed by the application database.

    Memory entries reuse the P5 ``chat_messages`` table: load/append operate
    on the messages of a long-lived ``ChatSession`` identified by
    ``session_id``. This keeps memory and chat history in one consistent
    store and works with the default SQLite deployment out of the box.
    """

    backend_name = "sqlite"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def _ensure_session(self, db: AsyncSession, session_id: str) -> ChatSession:
        row = await db.get(ChatSession, session_id)
        if row is not None:
            return row
        row = ChatSession(
            id=session_id,
            title=f"memory-{session_id[:8]}",
            workflow_id=None,
            model_config_id=None,
            inputs_json={},
        )
        db.add(row)
        await db.flush()
        return row

    async def load(self, session_id: str, window: int = 10) -> list[MemoryEntry]:
        from sqlalchemy import select

        async with self.session_factory() as db:
            rows = await db.execute(
                select(ChatMessageRow)
                .where(ChatMessageRow.session_id == session_id)
                .order_by(ChatMessageRow.created_at.desc())
                .limit(max(1, window))
            )
            items = list(rows.scalars().all())
        items.reverse()
        return [MemoryEntry(role=r.role, content=r.content) for r in items]

    async def append(self, session_id: str, role: str, content: str) -> None:
        async with self.session_factory() as db:
            await self._ensure_session(db, session_id)
            db.add(ChatMessageRow(session_id=session_id, role=role, content=content))
            await db.commit()

    async def reset(self, session_id: str) -> None:
        from sqlalchemy import delete

        async with self.session_factory() as db:
            await db.execute(
                delete(ChatMessageRow).where(ChatMessageRow.session_id == session_id)
            )
            await db.commit()


class RedisMemoryStore(BaseMemoryStore):
    """Volatile sliding-window memory backed by a Redis list."""

    backend_name = "redis"

    PREFIX = "agentcanvas:memory:"

    def __init__(self, client: Any) -> None:
        self.client = client

    async def load(self, session_id: str, window: int = 10) -> list[MemoryEntry]:
        raw = await self.client.lrange(self.PREFIX + session_id, -window, -1)
        entries: list[MemoryEntry] = []
        for item in raw:
            try:
                data = json.loads(item)
                entries.append(MemoryEntry(role=data["role"], content=data["content"]))
            except (ValueError, KeyError, TypeError):
                logger.debug("skip malformed memory entry for %s", session_id)
                continue
        return entries

    async def append(self, session_id: str, role: str, content: str) -> None:
        payload = json.dumps({"role": role, "content": content}, ensure_ascii=False)
        key = self.PREFIX + session_id
        await self.client.rpush(key, payload)
        await self.client.ltrim(key, -1000, -1)

    async def reset(self, session_id: str) -> None:
        await self.client.delete(self.PREFIX + session_id)

    async def aclose(self) -> None:
        close = getattr(self.client, "aclose", None) or getattr(self.client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


async def build_memory_store(settings: Any, session_factory: Any) -> BaseMemoryStore:
    """Construct the appropriate memory store from settings.

    Tries Redis first when ``REDIS_URL`` is configured; on any connection
    error or missing dependency it falls back to the SQLite store and logs
    the degradation. Returns ``NullMemoryStore`` only when memory is disabled.
    Otherwise the SQLite store is the default so memory always works.
    """
    redis_url = getattr(settings, "redis_url", "") or ""
    if not redis_url:
        logger.info("REDIS_URL not set; using SQLite memory store")
        return SqliteMemoryStore(session_factory)

    try:
        from redis.asyncio import Redis  # type: ignore[import-not-found]
    except ImportError as exc:
        logger.warning("redis package unavailable (%s); using SQLite memory store", exc)
        return SqliteMemoryStore(session_factory)

    # RESP2 keeps the Redis 5+ deployment floor compatible with redis-py 8.
    client = Redis.from_url(redis_url, decode_responses=True, protocol=2)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001 — degrade gracefully on any connection error
        logger.warning("redis ping failed (%s); falling back to SQLite memory store", exc)
        await _safe_close(client)
        return SqliteMemoryStore(session_factory)

    logger.info("connected to redis memory store at %s", _redact_url(redis_url))
    return RedisMemoryStore(client)


def _redact_url(url: str) -> str:
    """Mask credentials in a redis URL for logging."""
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    _, host = rest.split("@", 1)
    return f"{scheme}://***@{host}"


async def _safe_close(client: Any) -> None:
    close = getattr(client, "aclose", None) or getattr(client, "close", None)
    if close is None:
        return
    try:
        result = close()
        if hasattr(result, "__await__"):
            await result
    except Exception:  # noqa: BLE001 — best-effort cleanup
        logger.debug("ignoring error closing redis client during fallback", exc_info=True)


__all__ = [
    "BaseMemoryStore",
    "MemoryEntry",
    "NullMemoryStore",
    "RedisMemoryStore",
    "SqliteMemoryStore",
    "build_memory_store",
]
