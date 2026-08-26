"""Durable chat-session variable store shared by the API and the engine.

The engine consumes this through ``CompileContext.session_variable_store`` so
an agent node configured with ``session_writes`` can persist multi-turn memory
without the engine knowing about HTTP routes, and the chat send path can read
the same rows the runtime UI edits.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.repositories.chat import ChatSessionVariableRepo

logger = logging.getLogger(__name__)

_MAX_VARIABLES_PER_SESSION = 200
_MAX_VALUE_BYTES = 32 * 1024


class SessionVariableLimitExceeded(RuntimeError):
    """Raised when a session already holds the maximum number of variables."""


class ChatSessionVariableStore:
    """Owns its own DB sessions so the engine can call it from any task."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def snapshot(self, session_id: str) -> dict[str, Any]:
        """Return all variables for a session as a plain mapping."""
        async with self._session_factory() as db:
            return await ChatSessionVariableRepo(db).snapshot(session_id)

    async def write_many(self, session_id: str, values: dict[str, Any]) -> None:
        """Upsert several variables in one transaction.

        Values are bounded to keep a runaway node from filling the row store;
        a write failure must never fail the execution, so callers treat this
        as best-effort (matching the memory-store contract).
        """
        if not values:
            return
        async with self._session_factory() as db:
            repo = ChatSessionVariableRepo(db)
            existing = await repo.snapshot(session_id)
            if len(set(values) - set(existing)) + len(existing) > _MAX_VARIABLES_PER_SESSION:
                raise SessionVariableLimitExceeded(
                    f"session '{session_id}' exceeds {_MAX_VARIABLES_PER_SESSION} variables"
                )
            for name, value in values.items():
                serialized = _bounded(value)
                await repo.upsert(session_id=session_id, name=name, value=serialized)
            await db.commit()


def _bounded(value: Any) -> Any:
    """Reject oversized variable payloads before they reach the database."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        raise SessionVariableLimitExceeded("session variable is not JSON-serializable") from exc
    if len(encoded.encode("utf-8")) > _MAX_VALUE_BYTES:
        raise SessionVariableLimitExceeded(f"session variable exceeds {_MAX_VALUE_BYTES} bytes")
    return value
