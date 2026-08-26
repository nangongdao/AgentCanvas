"""Workflow presence and soft-lock coordination (process-local or shared).

I1 Phase 6 moves multi-API collaboration state to Redis. Without ``REDIS_URL``
the process-local hub remains the single-instance development backend. When
Redis is configured but unavailable, mutation paths fail closed so replicas
never diverge on split process-local locks.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]
ChangeQueue = asyncio.Queue[int | None]


class CollaborationConflict(Exception):
    """Base class for collaboration state conflicts."""


class ClientIdentityConflict(CollaborationConflict):
    """A client ID is already bound to another authenticated actor."""


class LockUnavailable(CollaborationConflict):
    """Another client currently owns the workflow soft lock."""


class LeaseMismatch(CollaborationConflict):
    """The supplied lease no longer owns the workflow soft lock."""


class CollaborationUnavailable(CollaborationConflict):
    """Shared collaboration backend is required but currently unusable."""


@dataclass(frozen=True)
class CollaborationActor:
    key: str
    subject: str


@dataclass(frozen=True)
class PresenceView:
    client_id: str
    subject: str
    last_seen_at: datetime
    expires_at: datetime
    is_self: bool


@dataclass(frozen=True)
class LockView:
    client_id: str
    subject: str
    acquired_at: datetime
    expires_at: datetime
    owned_by_self: bool


@dataclass(frozen=True)
class CollaborationSnapshot:
    workflow_id: str
    revision: int
    participants: tuple[PresenceView, ...]
    lock: LockView | None
    own_lease_id: str | None = None
    backend: str = "memory"
    read_only: bool = False


@dataclass
class _Presence:
    client_id: str
    actor: CollaborationActor
    last_seen_at: datetime
    expires_at: datetime


@dataclass
class _Lease:
    lease_id: str
    client_id: str
    actor: CollaborationActor
    acquired_at: datetime
    expires_at: datetime


@dataclass
class _Room:
    revision: int = 0
    participants: dict[str, _Presence] = field(default_factory=dict)
    lease: _Lease | None = None
    subscribers: set[ChangeQueue] = field(default_factory=set)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@runtime_checkable
class CollaborationHub(Protocol):
    """Shared surface used by collaboration HTTP/SSE routes."""

    backend_name: str
    read_only: bool

    async def heartbeat(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
        *,
        lease_id: str | None = None,
    ) -> CollaborationSnapshot: ...

    async def acquire_lock(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
        *,
        takeover: bool = False,
        lease_id: str | None = None,
    ) -> CollaborationSnapshot: ...

    async def release_lock(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
        lease_id: str,
    ) -> CollaborationSnapshot: ...

    async def leave(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
    ) -> CollaborationSnapshot: ...

    async def snapshot(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
    ) -> CollaborationSnapshot: ...

    async def subscribe(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
    ) -> tuple[ChangeQueue, CollaborationSnapshot]: ...

    def unsubscribe(self, workflow_id: str, queue: ChangeQueue) -> None: ...

    async def expire(self, workflow_id: str) -> bool: ...

    async def close(self) -> None: ...


class WorkflowCollaborationHub:
    """Coordinate transient collaboration state inside one application process."""

    backend_name = "memory"
    read_only = False

    def __init__(
        self,
        *,
        presence_ttl_seconds: float = 30,
        lock_ttl_seconds: float = 30,
        clock: Clock = _utcnow,
    ) -> None:
        if presence_ttl_seconds <= 0 or lock_ttl_seconds <= 0:
            raise ValueError("collaboration TTLs must be positive")
        self._presence_ttl = timedelta(seconds=presence_ttl_seconds)
        self._lock_ttl = timedelta(seconds=lock_ttl_seconds)
        self._clock = clock
        self._rooms: dict[str, _Room] = {}
        self._mutex = asyncio.Lock()
        self._closed = False

    async def heartbeat(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
        *,
        lease_id: str | None = None,
    ) -> CollaborationSnapshot:
        """Create/renew presence and renew an owned lock when its token matches."""
        async with self._mutex:
            room = self._room(workflow_id)
            now = self._now()
            self._expire_locked(room, now)
            self._assert_client_actor(room, client_id, actor)
            room.participants[client_id] = _Presence(
                client_id=client_id,
                actor=actor,
                last_seen_at=now,
                expires_at=now + self._presence_ttl,
            )
            if (
                lease_id is not None
                and room.lease is not None
                and room.lease.client_id == client_id
                and room.lease.actor.key == actor.key
                and secrets.compare_digest(room.lease.lease_id, lease_id)
            ):
                room.lease.expires_at = now + self._lock_ttl
            self._changed(room)
            return self._snapshot(workflow_id, room, client_id, actor)

    async def acquire_lock(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
        *,
        takeover: bool = False,
        lease_id: str | None = None,
    ) -> CollaborationSnapshot:
        """Acquire, renew, or explicitly take over a workflow soft lock."""
        async with self._mutex:
            room = self._room(workflow_id)
            now = self._now()
            self._expire_locked(room, now)
            self._assert_client_actor(room, client_id, actor)
            room.participants[client_id] = _Presence(
                client_id=client_id,
                actor=actor,
                last_seen_at=now,
                expires_at=now + self._presence_ttl,
            )
            current = room.lease
            if current is not None:
                owns_lock = current.client_id == client_id and current.actor.key == actor.key
                if owns_lock and lease_id is None:
                    current.expires_at = now + self._lock_ttl
                    self._changed(room)
                    return self._snapshot(workflow_id, room, client_id, actor)
                valid_lease = bool(
                    lease_id and owns_lock and secrets.compare_digest(current.lease_id, lease_id)
                )
                if valid_lease:
                    current.expires_at = now + self._lock_ttl
                    self._changed(room)
                    return self._snapshot(workflow_id, room, client_id, actor)
                if owns_lock:
                    raise LeaseMismatch("workflow lock lease is stale")
                if not takeover:
                    raise LockUnavailable("workflow is being edited by another client")
            room.lease = _Lease(
                lease_id=secrets.token_urlsafe(32),
                client_id=client_id,
                actor=actor,
                acquired_at=now,
                expires_at=now + self._lock_ttl,
            )
            self._changed(room)
            return self._snapshot(workflow_id, room, client_id, actor)

    async def release_lock(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
        lease_id: str,
    ) -> CollaborationSnapshot:
        async with self._mutex:
            room = self._room(workflow_id)
            self._expire_locked(room, self._now())
            current = room.lease
            owns_lock = (
                current is not None
                and current.client_id == client_id
                and current.actor.key == actor.key
                and secrets.compare_digest(current.lease_id, lease_id)
            )
            if not owns_lock:
                raise LeaseMismatch("workflow lock lease is stale or not owned by this client")
            room.lease = None
            self._changed(room)
            return self._snapshot(workflow_id, room, client_id, actor)

    async def leave(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
    ) -> CollaborationSnapshot:
        """Remove one actor-owned presence and release its lock immediately."""
        async with self._mutex:
            room = self._room(workflow_id)
            self._expire_locked(room, self._now())
            self._assert_client_actor(room, client_id, actor)
            removed = room.participants.pop(client_id, None) is not None
            if (
                room.lease is not None
                and room.lease.client_id == client_id
                and room.lease.actor.key == actor.key
            ):
                room.lease = None
                removed = True
            if removed:
                self._changed(room)
            return self._snapshot(workflow_id, room, client_id, actor)

    async def snapshot(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
    ) -> CollaborationSnapshot:
        async with self._mutex:
            room = self._room(workflow_id)
            if self._expire_locked(room, self._now()):
                self._changed(room)
            return self._snapshot(workflow_id, room, client_id, actor)

    async def subscribe(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
    ) -> tuple[ChangeQueue, CollaborationSnapshot]:
        """Atomically subscribe and capture state so stream startup has no gap."""
        async with self._mutex:
            room = self._room(workflow_id)
            if self._expire_locked(room, self._now()):
                self._changed(room)
            queue: ChangeQueue = asyncio.Queue(maxsize=1)
            room.subscribers.add(queue)
            return queue, self._snapshot(workflow_id, room, client_id, actor)

    def unsubscribe(self, workflow_id: str, queue: ChangeQueue) -> None:
        room = self._rooms.get(workflow_id)
        if room is None:
            return
        room.subscribers.discard(queue)
        if not room.participants and room.lease is None and not room.subscribers:
            self._rooms.pop(workflow_id, None)

    async def expire(self, workflow_id: str) -> bool:
        """Expire stale room state and notify live subscribers when it changed."""
        async with self._mutex:
            room = self._rooms.get(workflow_id)
            if room is None or not self._expire_locked(room, self._now()):
                return False
            self._changed(room)
            return True

    async def close(self) -> None:
        async with self._mutex:
            self._closed = True
            for room in self._rooms.values():
                for queue in room.subscribers:
                    with suppress(asyncio.QueueFull):
                        queue.put_nowait(None)
            self._rooms.clear()

    def _room(self, workflow_id: str) -> _Room:
        if self._closed:
            raise RuntimeError("collaboration hub is closed")
        return self._rooms.setdefault(workflow_id, _Room())

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("collaboration clock must return a timezone-aware datetime")
        return now

    @staticmethod
    def _assert_client_actor(room: _Room, client_id: str, actor: CollaborationActor) -> None:
        presence = room.participants.get(client_id)
        if presence is not None and presence.actor.key != actor.key:
            raise ClientIdentityConflict("client ID belongs to another authenticated actor")
        lease = room.lease
        if lease is not None and lease.client_id == client_id and lease.actor.key != actor.key:
            raise ClientIdentityConflict("client ID belongs to another authenticated actor")

    @staticmethod
    def _expire_locked(room: _Room, now: datetime) -> bool:
        expired_clients = [
            client_id
            for client_id, presence in room.participants.items()
            if presence.expires_at <= now
        ]
        for client_id in expired_clients:
            room.participants.pop(client_id, None)
        lease_expired = room.lease is not None and room.lease.expires_at <= now
        if lease_expired:
            room.lease = None
        return bool(expired_clients or lease_expired)

    @staticmethod
    def _changed(room: _Room) -> None:
        room.revision += 1
        for queue in tuple(room.subscribers):
            with suppress(asyncio.QueueFull):
                queue.put_nowait(room.revision)

    def _snapshot(
        self,
        workflow_id: str,
        room: _Room,
        client_id: str,
        actor: CollaborationActor,
    ) -> CollaborationSnapshot:
        participants = tuple(
            PresenceView(
                client_id=presence.client_id,
                subject=presence.actor.subject,
                last_seen_at=presence.last_seen_at,
                expires_at=presence.expires_at,
                is_self=(presence.client_id == client_id and presence.actor.key == actor.key),
            )
            for presence in sorted(room.participants.values(), key=lambda item: item.client_id)
        )
        lease = room.lease
        owns_lock = bool(
            lease is not None and lease.client_id == client_id and lease.actor.key == actor.key
        )
        lock_view = (
            LockView(
                client_id=lease.client_id,
                subject=lease.actor.subject,
                acquired_at=lease.acquired_at,
                expires_at=lease.expires_at,
                owned_by_self=owns_lock,
            )
            if lease is not None
            else None
        )
        return CollaborationSnapshot(
            workflow_id=workflow_id,
            revision=room.revision,
            participants=participants,
            lock=lock_view,
            own_lease_id=lease.lease_id if lease is not None and owns_lock else None,
            backend=self.backend_name,
            read_only=self.read_only,
        )


class ReadOnlyCollaborationHub:
    """Fail-closed collaboration surface when shared Redis is required but down."""

    backend_name = "unavailable"
    read_only = True

    def __init__(self, *, reason: str = "shared collaboration backend unavailable") -> None:
        self._reason = reason
        self._closed = False
        self._subscribers: dict[str, set[ChangeQueue]] = {}

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("collaboration hub is closed")

    def _deny(self) -> None:
        raise CollaborationUnavailable(self._reason)

    async def heartbeat(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
        *,
        lease_id: str | None = None,
    ) -> CollaborationSnapshot:
        _ = workflow_id, client_id, actor, lease_id
        self._ensure_open()
        self._deny()
        raise AssertionError("unreachable")

    async def acquire_lock(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
        *,
        takeover: bool = False,
        lease_id: str | None = None,
    ) -> CollaborationSnapshot:
        _ = workflow_id, client_id, actor, takeover, lease_id
        self._ensure_open()
        self._deny()
        raise AssertionError("unreachable")

    async def release_lock(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
        lease_id: str,
    ) -> CollaborationSnapshot:
        _ = workflow_id, client_id, actor, lease_id
        self._ensure_open()
        self._deny()
        raise AssertionError("unreachable")

    async def leave(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
    ) -> CollaborationSnapshot:
        _ = client_id, actor
        self._ensure_open()
        return await self.snapshot(workflow_id, client_id, actor)

    async def snapshot(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
    ) -> CollaborationSnapshot:
        _ = client_id, actor
        self._ensure_open()
        return CollaborationSnapshot(
            workflow_id=workflow_id,
            revision=0,
            participants=(),
            lock=None,
            own_lease_id=None,
            backend=self.backend_name,
            read_only=True,
        )

    async def subscribe(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
    ) -> tuple[ChangeQueue, CollaborationSnapshot]:
        self._ensure_open()
        queue: ChangeQueue = asyncio.Queue(maxsize=1)
        self._subscribers.setdefault(workflow_id, set()).add(queue)
        return queue, await self.snapshot(workflow_id, client_id, actor)

    def unsubscribe(self, workflow_id: str, queue: ChangeQueue) -> None:
        room = self._subscribers.get(workflow_id)
        if room is None:
            return
        room.discard(queue)
        if not room:
            self._subscribers.pop(workflow_id, None)

    async def expire(self, workflow_id: str) -> bool:
        _ = workflow_id
        self._ensure_open()
        return False

    async def close(self) -> None:
        self._closed = True
        for queues in self._subscribers.values():
            for queue in queues:
                with suppress(asyncio.QueueFull):
                    queue.put_nowait(None)
        self._subscribers.clear()


async def build_collaboration_hub(
    *,
    presence_ttl_seconds: float,
    lock_ttl_seconds: float,
    redis_url: str = "",
    redis_client: Any | None = None,
    clock: Clock = _utcnow,
) -> CollaborationHub:
    """Build the process-local or shared collaboration hub for this process."""
    if redis_client is not None:
        from app.core.collaboration_redis import RedisCollaborationHub

        return RedisCollaborationHub(
            redis_client,
            presence_ttl_seconds=presence_ttl_seconds,
            lock_ttl_seconds=lock_ttl_seconds,
            clock=clock,
            owns_client=False,
        )
    if not redis_url:
        return WorkflowCollaborationHub(
            presence_ttl_seconds=presence_ttl_seconds,
            lock_ttl_seconds=lock_ttl_seconds,
            clock=clock,
        )
    from app.engine.event_stream import close_redis_client, connect_event_stream_client

    client = await connect_event_stream_client(redis_url)
    if client is None:
        logger.warning(
            "REDIS_URL is set but collaboration Redis is unavailable; "
            "mutations fail closed until Redis recovers"
        )
        return ReadOnlyCollaborationHub(
            reason="shared collaboration backend unavailable; editing is read-only"
        )
    from app.core.collaboration_redis import RedisCollaborationHub

    hub = RedisCollaborationHub(
        client,
        presence_ttl_seconds=presence_ttl_seconds,
        lock_ttl_seconds=lock_ttl_seconds,
        clock=clock,
        owns_client=True,
        close_client=close_redis_client,
    )
    logger.info("using shared Redis collaboration hub")
    return hub


__all__ = [
    "ChangeQueue",
    "ClientIdentityConflict",
    "Clock",
    "CollaborationActor",
    "CollaborationConflict",
    "CollaborationHub",
    "CollaborationSnapshot",
    "CollaborationUnavailable",
    "LeaseMismatch",
    "LockUnavailable",
    "LockView",
    "PresenceView",
    "ReadOnlyCollaborationHub",
    "WorkflowCollaborationHub",
    "build_collaboration_hub",
]
