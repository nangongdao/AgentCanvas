"""Lease-fenced adapter for worker-owned LangGraph checkpoint writes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any, TypeVar

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.config import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.repositories import ExecutionQueueRepo
from app.engine.execution_lease import LeaseLost, WorkerLease

T = TypeVar("T")


class FencedCheckpointer(BaseCheckpointSaver[Any]):
    """Hold the durable queue-row lock across each delegated checkpoint write."""

    def __init__(
        self,
        delegate: BaseCheckpointSaver[Any],
        session_factory: async_sessionmaker[AsyncSession],
        lease: WorkerLease,
    ) -> None:
        super().__init__(serde=delegate.serde)
        self.delegate = delegate
        self.session_factory = session_factory
        self.lease = lease

    @property
    def config_specs(self) -> list[Any]:
        return list(self.delegate.config_specs)

    def get_next_version(self, current: Any | None, channel: None) -> Any:
        return self.delegate.get_next_version(current, channel)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await self._write(
            _thread_id(config),
            lambda: self.delegate.aget_tuple(config),
        )

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if config is None:
            raise ValueError("fenced checkpoint listing requires an execution config")
        thread_id = _thread_id(config)
        async with self.session_factory() as session:
            await ExecutionQueueRepo(session).lock_lease(self.lease.item_id)
            await self._assert_lease(session, thread_id)
            async for item in self.delegate.alist(
                config,
                filter=filter,
                before=before,
                limit=limit,
            ):
                yield item
            await session.commit()

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = _thread_id(config)
        return await self._write(
            thread_id,
            lambda: self.delegate.aput(config, checkpoint, metadata, new_versions),
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = _thread_id(config)
        await self._write(
            thread_id,
            lambda: self.delegate.aput_writes(config, writes, task_id, task_path),
        )

    async def adelete_thread(self, thread_id: str) -> None:
        await self._write(thread_id, lambda: self.delegate.adelete_thread(thread_id))

    async def _write(self, thread_id: str, operation: Callable[[], Awaitable[T]]) -> T:
        async with self.session_factory() as session:
            queue = ExecutionQueueRepo(session)
            # Keep the advisory fence held while the delegated saver commits on
            # its own connection. Scheduler reassignment takes the same lock.
            await queue.lock_lease(self.lease.item_id)
            await self._assert_lease(session, thread_id)
            result = await operation()
            await session.commit()
            return result

    async def _assert_lease(self, session: AsyncSession, thread_id: str) -> None:
        queue = ExecutionQueueRepo(session)
        valid = await queue.owns_lease(
            item_id=self.lease.item_id,
            owner_id=self.lease.owner_id,
            lease_generation=self.lease.generation,
            lock=True,
        )
        item = await queue.get(self.lease.item_id) if valid else None
        if item is None or item.execution_id != thread_id:
            await session.rollback()
            raise LeaseLost(
                f"execution {thread_id} lost lease {self.lease.item_id}:{self.lease.generation}"
            )


def _thread_id(config: RunnableConfig) -> str:
    configurable = config.get("configurable") or {}
    thread_id = str(configurable.get("thread_id") or "")
    if not thread_id:
        raise ValueError("checkpoint config requires thread_id")
    return thread_id


__all__ = ["FencedCheckpointer"]
