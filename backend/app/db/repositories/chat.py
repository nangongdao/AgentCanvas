"""Repositories for P5 chat sessions and messages."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ChatMessageFeedback,
    ChatMessageRow,
    ChatSession,
    ChatSessionVariable,
)
from app.db.pagination import PageSlice, PageSpec, paginate_select


class ChatSessionRepo:
    """CRUD for chat sessions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        title: str,
        workflow_id: str | None = None,
        model_config_id: str | None = None,
        inputs: dict[str, Any] | None = None,
        session_id: str | None = None,
        app_id: str | None = None,
    ) -> ChatSession:
        row = ChatSession(
            id=session_id,
            title=title,
            workflow_id=workflow_id,
            model_config_id=model_config_id,
            inputs_json=inputs or {},
            app_id=app_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get(self, session_id: str) -> ChatSession | None:
        return await self.session.get(ChatSession, session_id)

    async def list_all(self, limit: int = 100) -> list[ChatSession]:
        result = await self.session.execute(
            select(ChatSession).order_by(ChatSession.updated_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def list_for_workflow(self, workflow_id: str, limit: int = 50) -> list[ChatSession]:
        result = await self.session.execute(
            select(ChatSession)
            .where(ChatSession.workflow_id == workflow_id)
            .order_by(ChatSession.updated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_page(
        self, spec: PageSpec, *, workflow_id: str | None = None
    ) -> PageSlice[ChatSession]:
        statement = select(ChatSession)
        if workflow_id is not None:
            statement = statement.where(ChatSession.workflow_id == workflow_id)
        return await paginate_select(
            self.session,
            statement,
            id_column=ChatSession.id,
            columns={
                "created_at": ChatSession.created_at,
                "updated_at": ChatSession.updated_at,
                "title": ChatSession.title,
                "id": ChatSession.id,
            },
            spec=spec,
            search_columns=(ChatSession.title, ChatSession.id),
        )

    async def touch(self, row: ChatSession) -> None:
        row.updated_at = datetime.now(UTC)
        await self.session.flush()

    async def delete(self, session_id: str) -> bool:
        row = await self.get(session_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True


class ChatMessageRepo:
    """CRUD for chat messages."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        node_ref: str | None = None,
        execution_id: str | None = None,
        citations: list[dict[str, Any]] | None = None,
    ) -> ChatMessageRow:
        row = ChatMessageRow(
            session_id=session_id,
            role=role,
            content=content,
            node_ref=node_ref,
            execution_id=execution_id,
            citations_json=citations or [],
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_session_page(
        self, session_id: str, spec: PageSpec
    ) -> PageSlice[ChatMessageRow]:
        return await paginate_select(
            self.session,
            select(ChatMessageRow).where(ChatMessageRow.session_id == session_id),
            id_column=ChatMessageRow.id,
            columns={
                "created_at": ChatMessageRow.created_at,
                "role": ChatMessageRow.role,
                "id": ChatMessageRow.id,
            },
            spec=spec,
            search_columns=(ChatMessageRow.role, ChatMessageRow.content, ChatMessageRow.id),
        )

    async def list_for_session(self, session_id: str, *, limit: int = 200) -> list[ChatMessageRow]:
        """Return the full recent transcript for a session (runtime replay)."""
        result = await self.session.execute(
            select(ChatMessageRow)
            .where(ChatMessageRow.session_id == session_id)
            .order_by(ChatMessageRow.created_at.asc(), ChatMessageRow.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_recent(self, session_id: str, role: str, since: datetime) -> int:
        """Count messages of one role since a moment (end-user rate limiting)."""
        result = await self.session.execute(
            select(func.count())
            .select_from(ChatMessageRow)
            .where(
                ChatMessageRow.session_id == session_id,
                ChatMessageRow.role == role,
                ChatMessageRow.created_at >= since,
            )
        )
        return int(result.scalar_one())

    async def get(self, message_id: str) -> ChatMessageRow | None:
        return await self.session.get(ChatMessageRow, message_id)


class ChatFeedbackRepo:
    """CRUD for end-user message feedback (C3-4)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_message(self, message_id: str) -> ChatMessageFeedback | None:
        result = await self.session.execute(
            select(ChatMessageFeedback).where(ChatMessageFeedback.message_id == message_id)
        )
        return result.scalar_one_or_none()

    async def ratings_for_messages(self, message_ids: Sequence[str]) -> dict[str, str]:
        """Batch-load the rating per message id for list responses."""
        if not message_ids:
            return {}
        result = await self.session.execute(
            select(ChatMessageFeedback.message_id, ChatMessageFeedback.rating).where(
                ChatMessageFeedback.message_id.in_(message_ids)
            )
        )
        return {message_id: rating for message_id, rating in result.all()}

    async def upsert(
        self,
        *,
        message: ChatMessageRow,
        rating: str,
        comment: str = "",
    ) -> ChatMessageFeedback:
        existing = await self.get_for_message(message.id)
        if existing is None:
            row = ChatMessageFeedback(
                message_id=message.id,
                session_id=message.session_id,
                rating=rating,
                comment=comment,
            )
            self.session.add(row)
            await self.session.flush()
            return row
        existing.rating = rating
        existing.comment = comment
        existing.updated_at = datetime.now(UTC)
        await self.session.flush()
        return existing

    async def delete(self, message_id: str) -> bool:
        row = await self.get_for_message(message_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

    async def mark_promoted(
        self, feedback: ChatMessageFeedback, dataset_version_id: str
    ) -> ChatMessageFeedback:
        feedback.promoted_dataset_version_id = dataset_version_id
        feedback.promoted_at = datetime.now(UTC)
        await self.session.flush()
        return feedback

    async def list_negative_unpromoted(self, limit: int = 100) -> list[ChatMessageFeedback]:
        """Feedback candidates eligible for dataset promotion (👎, not yet promoted)."""
        result = await self.session.execute(
            select(ChatMessageFeedback)
            .where(
                ChatMessageFeedback.rating == "negative",
                ChatMessageFeedback.promoted_dataset_version_id.is_(None),
            )
            .order_by(ChatMessageFeedback.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


class ChatSessionVariableRepo:
    """Per-session key/value store for multi-turn memory (C3-4)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, session_id: str, name: str) -> ChatSessionVariable | None:
        result = await self.session.execute(
            select(ChatSessionVariable).where(
                ChatSessionVariable.session_id == session_id,
                ChatSessionVariable.name == name,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_session(self, session_id: str) -> list[ChatSessionVariable]:
        result = await self.session.execute(
            select(ChatSessionVariable)
            .where(ChatSessionVariable.session_id == session_id)
            .order_by(ChatSessionVariable.name.asc())
        )
        return list(result.scalars().all())

    async def upsert(self, *, session_id: str, name: str, value: Any) -> ChatSessionVariable:
        existing = await self.get(session_id, name)
        if existing is None:
            row = ChatSessionVariable(
                session_id=session_id,
                name=name,
                value_json=value,
            )
            self.session.add(row)
            await self.session.flush()
            return row
        existing.value_json = value
        existing.updated_at = datetime.now(UTC)
        await self.session.flush()
        return existing

    async def delete(self, session_id: str, name: str) -> bool:
        row = await self.get(session_id, name)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

    async def snapshot(self, session_id: str) -> dict[str, Any]:
        """Return all variables as a plain dict for template rendering."""
        rows = await self.list_for_session(session_id)
        return {row.name: row.value_json for row in rows}


__all__ = [
    "ChatFeedbackRepo",
    "ChatMessageRepo",
    "ChatSessionRepo",
    "ChatSessionVariableRepo",
]
