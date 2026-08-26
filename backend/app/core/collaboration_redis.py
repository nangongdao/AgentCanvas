"""Shared Redis collaboration hub for multi-API presence and soft locks.

Durable optimistic workflow versions remain the final conflict guard. Redis only
holds ephemeral presence/lock state with opaque lease tokens. Mutations use
compare-and-set style checks so a stale writer cannot silently keep ownership.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.collaboration import (
    ChangeQueue,
    ClientIdentityConflict,
    Clock,
    CollaborationActor,
    CollaborationConflict,
    CollaborationSnapshot,
    CollaborationUnavailable,
    LeaseMismatch,
    LockUnavailable,
    LockView,
    PresenceView,
)

logger = logging.getLogger(__name__)

PRESENCE_PREFIX = "agentcanvas:collab:presence:"
LOCK_PREFIX = "agentcanvas:collab:lock:"
REVISION_PREFIX = "agentcanvas:collab:revision:"
CHANNEL_PREFIX = "agentcanvas:collab:notify:"

_LOCK_ACQUIRE_SCRIPT = """-- agentcanvas-lock-acquire
local raw = redis.call('GET', KEYS[1])
local now_epoch = tonumber(ARGV[1])
local replacement = ARGV[2]
local ttl = tonumber(ARGV[3])
local client_id = ARGV[4]
local actor_key = ARGV[5]
local supplied_lease = ARGV[6]
local takeover = ARGV[7] == '1'
local current = nil
if raw then
  local ok, decoded = pcall(cjson.decode, raw)
  if ok and tonumber(decoded.expires_at_epoch or 0) > now_epoch then
    current = decoded
  end
end
if not current then
  redis.call('SET', KEYS[1], replacement, 'EX', ttl)
  return 1
end
local owns = current.client_id == client_id and current.actor_key == actor_key
if owns then
  if supplied_lease ~= '' and current.lease_id ~= supplied_lease then
    return -2
  end
  local renewed = cjson.decode(replacement)
  renewed.lease_id = current.lease_id
  renewed.acquired_at = current.acquired_at
  redis.call('SET', KEYS[1], cjson.encode(renewed), 'EX', ttl)
  return 1
end
if not takeover then
  return -1
end
redis.call('SET', KEYS[1], replacement, 'EX', ttl)
return 1
"""

_LOCK_RENEW_SCRIPT = """-- agentcanvas-lock-renew
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local ok, current = pcall(cjson.decode, raw)
if not ok or tonumber(current.expires_at_epoch or 0) <= tonumber(ARGV[1]) then return 0 end
if current.lease_id ~= ARGV[2] or current.client_id ~= ARGV[3] or current.actor_key ~= ARGV[4] then
  return 0
end
current.expires_at = ARGV[5]
current.expires_at_epoch = tonumber(ARGV[6])
redis.call('SET', KEYS[1], cjson.encode(current), 'EX', tonumber(ARGV[7]))
return 1
"""

_LOCK_RELEASE_SCRIPT = """-- agentcanvas-lock-release
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local ok, current = pcall(cjson.decode, raw)
if not ok then return 0 end
if tonumber(current.expires_at_epoch or 0) <= tonumber(ARGV[1]) then
  redis.call('DEL', KEYS[1])
  return 2
end
if current.lease_id ~= ARGV[2] or current.client_id ~= ARGV[3] or current.actor_key ~= ARGV[4] then
  return 0
end
redis.call('DEL', KEYS[1])
return 1
"""

CloseClient = Callable[[Any], Awaitable[None]]


def presence_key(workflow_id: str) -> str:
    return f"{PRESENCE_PREFIX}{workflow_id}"


def lock_key(workflow_id: str) -> str:
    return f"{LOCK_PREFIX}{workflow_id}"


def revision_key(workflow_id: str) -> str:
    return f"{REVISION_PREFIX}{workflow_id}"


def notify_channel(workflow_id: str) -> str:
    return f"{CHANNEL_PREFIX}{workflow_id}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


@dataclass
class _LocalRoom:
    subscribers: set[ChangeQueue] = field(default_factory=set)


class RedisCollaborationHub:
    """Coordinate presence and soft locks through a shared Redis backend."""

    backend_name = "redis"
    read_only = False

    def __init__(
        self,
        client: Any,
        *,
        presence_ttl_seconds: float = 30,
        lock_ttl_seconds: float = 30,
        clock: Clock = _utcnow,
        owns_client: bool = False,
        close_client: CloseClient | None = None,
    ) -> None:
        if presence_ttl_seconds <= 0 or lock_ttl_seconds <= 0:
            raise ValueError("collaboration TTLs must be positive")
        self.client = client
        self._presence_ttl = timedelta(seconds=presence_ttl_seconds)
        self._lock_ttl = timedelta(seconds=lock_ttl_seconds)
        self._presence_ttl_seconds = max(1, int(presence_ttl_seconds))
        self._lock_ttl_seconds = max(1, int(lock_ttl_seconds))
        self._clock = clock
        self._owns_client = owns_client
        self._close_client = close_client
        self._rooms: dict[str, _LocalRoom] = {}
        self._mutex = asyncio.Lock()
        self._closed = False
        self._pubsub_task: asyncio.Task[None] | None = None
        self._start_pubsub_listener()

    def _start_pubsub_listener(self) -> None:
        subscribe = getattr(self.client, "subscribe", None)
        if not callable(subscribe):
            return
        self._pubsub_task = asyncio.create_task(
            self._pubsub_loop(),
            name="collaboration-redis-pubsub",
        )

    async def _pubsub_loop(self) -> None:
        """Best-effort cross-process notify; local mutations still wake queues."""
        try:
            pubsub = self.client.pubsub()
            await pubsub.psubscribe(f"{CHANNEL_PREFIX}*")
            while not self._closed:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if not message:
                    await asyncio.sleep(0.05)
                    continue
                channel = str(message.get("channel") or "")
                if not channel.startswith(CHANNEL_PREFIX):
                    continue
                workflow_id = channel[len(CHANNEL_PREFIX) :]
                data = message.get("data")
                try:
                    revision = int(data)
                except (TypeError, ValueError):
                    continue
                async with self._mutex:
                    room = self._rooms.get(workflow_id)
                    if room is None:
                        continue
                    for queue in tuple(room.subscribers):
                        with suppress(asyncio.QueueFull):
                            queue.put_nowait(revision)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — pubsub is best-effort fan-out
            logger.debug("collaboration redis pubsub listener stopped", exc_info=True)

    async def heartbeat(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
        *,
        lease_id: str | None = None,
    ) -> CollaborationSnapshot:
        self._ensure_open()
        now = self._now()
        try:
            await self._touch_presence(workflow_id, client_id, actor, now)
            if lease_id is not None:
                await self._renew_owned_lock(workflow_id, client_id, actor, lease_id, now)
            revision = await self._bump_revision(workflow_id)
            await self._notify(workflow_id, revision)
            return await self._load_snapshot(workflow_id, client_id, actor, revision=revision)
        except CollaborationConflict:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CollaborationUnavailable(
                "shared collaboration backend unavailable; editing is read-only"
            ) from exc

    async def acquire_lock(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
        *,
        takeover: bool = False,
        lease_id: str | None = None,
    ) -> CollaborationSnapshot:
        self._ensure_open()
        now = self._now()
        try:
            await self._touch_presence(workflow_id, client_id, actor, now)
            expires_at = now + self._lock_ttl
            body = self._lock_body(
                {
                    "lease_id": secrets.token_urlsafe(32),
                    "client_id": client_id,
                    "actor_key": actor.key,
                    "subject": actor.subject,
                    "acquired_at": now.isoformat(),
                },
                expires_at,
            )
            result = int(
                await self.client.eval(
                    _LOCK_ACQUIRE_SCRIPT,
                    1,
                    lock_key(workflow_id),
                    now.timestamp(),
                    json.dumps(body, ensure_ascii=False),
                    self._lock_ttl_seconds,
                    client_id,
                    actor.key,
                    lease_id or "",
                    "1" if takeover else "0",
                )
            )
            if result == -2:
                raise LeaseMismatch("workflow lock lease is stale")
            if result != 1:
                raise LockUnavailable("workflow is being edited by another client")
            revision = await self._bump_revision(workflow_id)
            await self._notify(workflow_id, revision)
            return await self._load_snapshot(workflow_id, client_id, actor, revision=revision)
        except CollaborationConflict:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CollaborationUnavailable(
                "shared collaboration backend unavailable; editing is read-only"
            ) from exc

    async def release_lock(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
        lease_id: str,
    ) -> CollaborationSnapshot:
        self._ensure_open()
        now = self._now()
        try:
            released = await self._release_lock(
                workflow_id,
                client_id=client_id,
                actor_key=actor.key,
                lease_id=lease_id,
                now=now,
            )
            if released != 1:
                raise LeaseMismatch("workflow lock lease is stale or not owned by this client")
            revision = await self._bump_revision(workflow_id)
            await self._notify(workflow_id, revision)
            return await self._load_snapshot(workflow_id, client_id, actor, revision=revision)
        except CollaborationConflict:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CollaborationUnavailable(
                "shared collaboration backend unavailable; editing is read-only"
            ) from exc

    async def leave(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
    ) -> CollaborationSnapshot:
        self._ensure_open()
        now = self._now()
        try:
            await self._assert_client_actor(workflow_id, client_id, actor, now)
            removed = False
            existing = await self.client.hget(presence_key(workflow_id), client_id)
            if existing is not None:
                payload = json.loads(existing)
                if payload.get("actor_key") == actor.key:
                    await self.client.hdel(presence_key(workflow_id), client_id)
                    removed = True
            current = await self._load_lock(workflow_id)
            if (
                current is not None
                and current["client_id"] == client_id
                and current["actor_key"] == actor.key
            ):
                released = await self._release_lock(
                    workflow_id,
                    client_id=client_id,
                    actor_key=actor.key,
                    lease_id=current["lease_id"],
                    now=now,
                )
                removed = released == 1 or removed
            revision = await self._current_revision(workflow_id)
            if removed:
                revision = await self._bump_revision(workflow_id)
                await self._notify(workflow_id, revision)
            return await self._load_snapshot(workflow_id, client_id, actor, revision=revision)
        except CollaborationConflict:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CollaborationUnavailable(
                "shared collaboration backend unavailable; editing is read-only"
            ) from exc

    async def snapshot(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
    ) -> CollaborationSnapshot:
        self._ensure_open()
        try:
            changed = await self._expire(workflow_id)
            revision = await self._current_revision(workflow_id)
            if changed:
                revision = await self._bump_revision(workflow_id)
                await self._notify(workflow_id, revision)
            return await self._load_snapshot(workflow_id, client_id, actor, revision=revision)
        except Exception as exc:  # noqa: BLE001
            raise CollaborationUnavailable(
                "shared collaboration backend unavailable; editing is read-only"
            ) from exc

    async def subscribe(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
    ) -> tuple[ChangeQueue, CollaborationSnapshot]:
        self._ensure_open()
        queue: ChangeQueue = asyncio.Queue(maxsize=1)
        async with self._mutex:
            room = self._rooms.setdefault(workflow_id, _LocalRoom())
            room.subscribers.add(queue)
        snapshot = await self.snapshot(workflow_id, client_id, actor)
        return queue, snapshot

    def unsubscribe(self, workflow_id: str, queue: ChangeQueue) -> None:
        room = self._rooms.get(workflow_id)
        if room is None:
            return
        room.subscribers.discard(queue)
        if not room.subscribers:
            self._rooms.pop(workflow_id, None)

    async def expire(self, workflow_id: str) -> bool:
        self._ensure_open()
        try:
            changed = await self._expire(workflow_id)
            if not changed:
                return False
            revision = await self._bump_revision(workflow_id)
            await self._notify(workflow_id, revision)
            return True
        except Exception as exc:  # noqa: BLE001
            raise CollaborationUnavailable(
                "shared collaboration backend unavailable; editing is read-only"
            ) from exc

    async def close(self) -> None:
        self._closed = True
        if self._pubsub_task is not None:
            self._pubsub_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._pubsub_task
            self._pubsub_task = None
        async with self._mutex:
            for room in self._rooms.values():
                for queue in room.subscribers:
                    with suppress(asyncio.QueueFull):
                        queue.put_nowait(None)
            self._rooms.clear()
        if self._owns_client and self._close_client is not None:
            await self._close_client(self.client)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("collaboration hub is closed")

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("collaboration clock must return a timezone-aware datetime")
        return now

    async def _touch_presence(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
        now: datetime,
    ) -> None:
        await self._assert_client_actor(workflow_id, client_id, actor, now)
        payload = {
            "actor_key": actor.key,
            "subject": actor.subject,
            "last_seen_at": now.isoformat(),
            "expires_at": (now + self._presence_ttl).isoformat(),
        }
        key = presence_key(workflow_id)
        await self.client.hset(key, client_id, json.dumps(payload, ensure_ascii=False))
        expire = getattr(self.client, "expire", None)
        if callable(expire):
            await expire(key, self._presence_ttl_seconds * 4)

    async def _assert_client_actor(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
        now: datetime,
    ) -> None:
        raw = await self.client.hget(presence_key(workflow_id), client_id)
        if raw is not None:
            payload = json.loads(raw)
            expires_at = _parse_dt(str(payload.get("expires_at") or ""))
            if expires_at is not None and expires_at > now and payload.get("actor_key") != actor.key:
                raise ClientIdentityConflict("client ID belongs to another authenticated actor")
        current = await self._load_lock(workflow_id)
        if (
            current is not None
            and current["expires_at"] > now
            and current["client_id"] == client_id
            and current["actor_key"] != actor.key
        ):
            raise ClientIdentityConflict("client ID belongs to another authenticated actor")

    async def _renew_owned_lock(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
        lease_id: str,
        now: datetime,
    ) -> None:
        expires_at = now + self._lock_ttl
        await self.client.eval(
            _LOCK_RENEW_SCRIPT,
            1,
            lock_key(workflow_id),
            now.timestamp(),
            lease_id,
            client_id,
            actor.key,
            expires_at.isoformat(),
            expires_at.timestamp(),
            self._lock_ttl_seconds,
        )

    @staticmethod
    def _lock_body(payload: dict[str, Any], expires_at: datetime) -> dict[str, Any]:
        acquired_at = payload.get("acquired_at")
        if isinstance(acquired_at, datetime):
            acquired_iso = acquired_at.isoformat()
        else:
            acquired_iso = str(acquired_at or expires_at.isoformat())
        return {
            "lease_id": str(payload.get("lease_id") or ""),
            "client_id": str(payload.get("client_id") or ""),
            "actor_key": str(payload.get("actor_key") or ""),
            "subject": str(payload.get("subject") or ""),
            "acquired_at": acquired_iso,
            "expires_at": expires_at.isoformat(),
            "expires_at_epoch": expires_at.timestamp(),
        }

    async def _release_lock(
        self,
        workflow_id: str,
        *,
        client_id: str,
        actor_key: str,
        lease_id: str,
        now: datetime,
    ) -> int:
        return int(
            await self.client.eval(
                _LOCK_RELEASE_SCRIPT,
                1,
                lock_key(workflow_id),
                now.timestamp(),
                lease_id,
                client_id,
                actor_key,
            )
        )

    async def _load_lock(self, workflow_id: str) -> dict[str, Any] | None:
        raw = await self.client.get(lock_key(workflow_id))
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        expires_at = _parse_dt(str(payload.get("expires_at") or ""))
        acquired_at = _parse_dt(str(payload.get("acquired_at") or ""))
        if expires_at is None or acquired_at is None:
            return None
        return {
            "lease_id": str(payload.get("lease_id") or ""),
            "client_id": str(payload.get("client_id") or ""),
            "actor_key": str(payload.get("actor_key") or ""),
            "subject": str(payload.get("subject") or ""),
            "acquired_at": acquired_at,
            "expires_at": expires_at,
        }

    async def _expire(self, workflow_id: str) -> bool:
        now = self._now()
        changed = False
        raw_map = await self.client.hgetall(presence_key(workflow_id)) or {}
        for client_id, raw in list(raw_map.items()):
            try:
                payload = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                await self.client.hdel(presence_key(workflow_id), client_id)
                changed = True
                continue
            expires_at = _parse_dt(str(payload.get("expires_at") or ""))
            if expires_at is None or expires_at <= now:
                await self.client.hdel(presence_key(workflow_id), client_id)
                changed = True
        current = await self._load_lock(workflow_id)
        if current is not None and current["expires_at"] <= now:
            released = await self._release_lock(
                workflow_id,
                client_id=current["client_id"],
                actor_key=current["actor_key"],
                lease_id=current["lease_id"],
                now=now,
            )
            changed = released == 2 or changed
        return changed

    async def _current_revision(self, workflow_id: str) -> int:
        raw = await self.client.get(revision_key(workflow_id))
        try:
            return int(raw or 0)
        except (TypeError, ValueError):
            return 0

    async def _bump_revision(self, workflow_id: str) -> int:
        value = await self.client.incr(revision_key(workflow_id))
        return int(value)

    async def _notify(self, workflow_id: str, revision: int) -> None:
        async with self._mutex:
            room = self._rooms.get(workflow_id)
            if room is not None:
                for queue in tuple(room.subscribers):
                    with suppress(asyncio.QueueFull):
                        queue.put_nowait(revision)
        publish = getattr(self.client, "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish(notify_channel(workflow_id), str(revision))

    async def _load_snapshot(
        self,
        workflow_id: str,
        client_id: str,
        actor: CollaborationActor,
        *,
        revision: int,
    ) -> CollaborationSnapshot:
        now = self._now()
        raw_map = await self.client.hgetall(presence_key(workflow_id)) or {}
        participants: list[PresenceView] = []
        for member_id, raw in sorted(raw_map.items(), key=lambda item: str(item[0])):
            try:
                payload = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            expires_at = _parse_dt(str(payload.get("expires_at") or ""))
            last_seen_at = _parse_dt(str(payload.get("last_seen_at") or ""))
            if expires_at is None or last_seen_at is None or expires_at <= now:
                continue
            actor_key = str(payload.get("actor_key") or "")
            participants.append(
                PresenceView(
                    client_id=str(member_id),
                    subject=str(payload.get("subject") or ""),
                    last_seen_at=last_seen_at,
                    expires_at=expires_at,
                    is_self=(str(member_id) == client_id and actor_key == actor.key),
                )
            )
        current = await self._load_lock(workflow_id)
        lock_view = None
        own_lease_id = None
        if current is not None and current["expires_at"] > now:
            owns_lock = current["client_id"] == client_id and current["actor_key"] == actor.key
            lock_view = LockView(
                client_id=current["client_id"],
                subject=current["subject"],
                acquired_at=current["acquired_at"],
                expires_at=current["expires_at"],
                owned_by_self=owns_lock,
            )
            if owns_lock:
                own_lease_id = current["lease_id"]
        return CollaborationSnapshot(
            workflow_id=workflow_id,
            revision=revision,
            participants=tuple(participants),
            lock=lock_view,
            own_lease_id=own_lease_id,
            backend=self.backend_name,
            read_only=self.read_only,
        )


__all__ = [
    "CHANNEL_PREFIX",
    "LOCK_PREFIX",
    "PRESENCE_PREFIX",
    "REVISION_PREFIX",
    "RedisCollaborationHub",
    "lock_key",
    "notify_channel",
    "presence_key",
    "revision_key",
]
