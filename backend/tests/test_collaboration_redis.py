"""Shared Redis collaboration hub contracts (I1 Phase 6)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from app.core.collaboration import (
    ClientIdentityConflict,
    CollaborationActor,
    CollaborationUnavailable,
    LeaseMismatch,
    LockUnavailable,
    ReadOnlyCollaborationHub,
    WorkflowCollaborationHub,
    build_collaboration_hub,
)
from app.core.collaboration_redis import (
    RedisCollaborationHub,
    lock_key,
    presence_key,
    revision_key,
)


@dataclass
class MutableClock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeRedis:
    """Minimal hash/string Redis stand-in for collaboration hub tests."""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.published: list[tuple[str, str]] = []
        self.ttls: dict[str, int] = {}

    async def hset(self, name: str, key: str, value: str) -> int:
        bucket = self.hashes.setdefault(name, {})
        created = 0 if key in bucket else 1
        bucket[key] = value
        return created

    async def hget(self, name: str, key: str) -> str | None:
        return self.hashes.get(name, {}).get(key)

    async def hgetall(self, name: str) -> dict[str, str]:
        return dict(self.hashes.get(name, {}))

    async def hdel(self, name: str, key: str) -> int:
        bucket = self.hashes.get(name)
        if bucket is None or key not in bucket:
            return 0
        del bucket[key]
        if not bucket:
            self.hashes.pop(name, None)
        return 1

    async def get(self, name: str) -> str | None:
        return self.strings.get(name)

    async def set(self, name: str, value: str) -> bool:
        self.strings[name] = value
        return True

    async def delete(self, name: str) -> int:
        removed = 0
        if name in self.strings:
            del self.strings[name]
            removed = 1
        if name in self.hashes:
            del self.hashes[name]
            removed = 1
        self.ttls.pop(name, None)
        return removed

    async def incr(self, name: str) -> int:
        value = int(self.strings.get(name, "0")) + 1
        self.strings[name] = str(value)
        return value

    async def expire(self, name: str, seconds: int) -> bool:
        self.ttls[name] = seconds
        return True

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1

    async def eval(self, script: str, numkeys: int, *args: object) -> int:
        assert numkeys == 1
        name = str(args[0])
        raw = self.strings.get(name)
        current = json.loads(raw) if raw is not None else None
        if "agentcanvas-lock-acquire" in script:
            now_epoch = float(str(args[1]))
            body = str(args[2])
            ttl = int(str(args[3]))
            client_id = str(args[4])
            actor_key = str(args[5])
            supplied_lease = str(args[6])
            takeover = str(args[7]) == "1"
            if current is not None and float(current.get("expires_at_epoch", 0)) <= now_epoch:
                current = None
            if current is None:
                self.strings[name] = body
                self.ttls[name] = ttl
                return 1
            owns = current.get("client_id") == client_id and current.get("actor_key") == actor_key
            if owns:
                if supplied_lease and current.get("lease_id") != supplied_lease:
                    return -2
                renewed = json.loads(body)
                renewed["lease_id"] = current["lease_id"]
                renewed["acquired_at"] = current["acquired_at"]
                self.strings[name] = json.dumps(renewed, ensure_ascii=False)
                self.ttls[name] = ttl
                return 1
            if not takeover:
                return -1
            self.strings[name] = body
            self.ttls[name] = ttl
            return 1
        if "agentcanvas-lock-renew" in script:
            now_epoch = float(str(args[1]))
            expected_lease = str(args[2])
            client_id = str(args[3])
            actor_key = str(args[4])
            expires_at = str(args[5])
            expires_at_epoch = float(str(args[6]))
            ttl = int(str(args[7]))
            if current is None or float(current.get("expires_at_epoch", 0)) <= now_epoch:
                return 0
            if (
                current.get("lease_id") != expected_lease
                or current.get("client_id") != client_id
                or current.get("actor_key") != actor_key
            ):
                return 0
            current["expires_at"] = expires_at
            current["expires_at_epoch"] = expires_at_epoch
            self.strings[name] = json.dumps(current, ensure_ascii=False)
            self.ttls[name] = ttl
            return 1
        if "agentcanvas-lock-release" in script:
            now_epoch = float(str(args[1]))
            expected_lease = str(args[2])
            client_id = str(args[3])
            actor_key = str(args[4])
            if current is None:
                return 0
            if float(current.get("expires_at_epoch", 0)) <= now_epoch:
                await self.delete(name)
                return 2
            if (
                current.get("lease_id") != expected_lease
                or current.get("client_id") != client_id
                or current.get("actor_key") != actor_key
            ):
                return 0
            await self.delete(name)
            return 1
        raise AssertionError("unknown collaboration Lua contract")


class LockMutationGateRedis(FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.arm_release = False
        self.release_started = asyncio.Event()
        self.continue_release = asyncio.Event()

    async def delete(self, name: str) -> int:
        if self.arm_release and name.startswith("agentcanvas:collab:lock:"):
            self.arm_release = False
            self.release_started.set()
            await self.continue_release.wait()
        return await super().delete(name)

    async def eval(self, script: str, numkeys: int, *args: object) -> int:
        if self.arm_release and "agentcanvas-lock-release" in script:
            self.arm_release = False
            self.release_started.set()
            await self.continue_release.wait()
        return await super().eval(script, numkeys, *args)


class BrokenRedis(FakeRedis):
    async def hset(self, name: str, key: str, value: str) -> int:  # type: ignore[override]
        raise ConnectionError("redis down")


ALICE = CollaborationActor("user:alice", "alice@example.com")
BOB = CollaborationActor("user:bob", "bob@example.com")


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(datetime(2026, 8, 10, 12, 0, tzinfo=UTC))


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def hub(redis: FakeRedis, clock: MutableClock) -> RedisCollaborationHub:
    return RedisCollaborationHub(
        redis,
        presence_ttl_seconds=30,
        lock_ttl_seconds=20,
        clock=clock,
    )


async def test_build_hub_uses_memory_without_redis_url() -> None:
    hub = await build_collaboration_hub(
        presence_ttl_seconds=30,
        lock_ttl_seconds=30,
        redis_url="",
    )
    assert isinstance(hub, WorkflowCollaborationHub)
    assert hub.backend_name == "memory"
    assert hub.read_only is False


async def test_build_hub_uses_injected_redis_client(redis: FakeRedis) -> None:
    hub = await build_collaboration_hub(
        presence_ttl_seconds=30,
        lock_ttl_seconds=30,
        redis_client=redis,
    )
    assert isinstance(hub, RedisCollaborationHub)
    assert hub.backend_name == "redis"


async def test_redis_presence_lock_takeover_and_shared_state(
    hub: RedisCollaborationHub, redis: FakeRedis, clock: MutableClock
) -> None:
    snapshot = await hub.heartbeat("workflow", "alice-tab", ALICE)
    assert snapshot.revision == 1
    assert snapshot.backend == "redis"
    assert [(item.subject, item.is_self) for item in snapshot.participants] == [
        ("alice@example.com", True)
    ]
    assert presence_key("workflow") in redis.hashes

    bob_view = await hub.snapshot("workflow", "bob-tab", BOB)
    assert bob_view.participants[0].is_self is False
    with pytest.raises(ClientIdentityConflict):
        await hub.heartbeat("workflow", "alice-tab", BOB)

    alice = await hub.acquire_lock("workflow", "alice-tab", ALICE)
    assert alice.own_lease_id
    assert alice.lock is not None and alice.lock.owned_by_self
    assert lock_key("workflow") in redis.strings

    bob = await hub.snapshot("workflow", "bob-tab", BOB)
    assert bob.own_lease_id is None
    assert bob.lock is not None and not bob.lock.owned_by_self
    with pytest.raises(LockUnavailable):
        await hub.acquire_lock("workflow", "bob-tab", BOB)

    taken = await hub.acquire_lock("workflow", "bob-tab", BOB, takeover=True)
    assert taken.own_lease_id
    assert taken.own_lease_id != alice.own_lease_id
    with pytest.raises(LeaseMismatch):
        await hub.release_lock("workflow", "alice-tab", ALICE, alice.own_lease_id or "")

    # A second hub on the same Redis must observe Bob's lock.
    peer = RedisCollaborationHub(
        redis,
        presence_ttl_seconds=30,
        lock_ttl_seconds=20,
        clock=clock,
    )
    peer_view = await peer.snapshot("workflow", "observer", ALICE)
    assert peer_view.lock is not None
    assert peer_view.lock.subject == "bob@example.com"
    assert peer_view.revision == taken.revision


async def test_redis_matching_heartbeat_renews_lock(
    hub: RedisCollaborationHub, clock: MutableClock
) -> None:
    acquired = await hub.acquire_lock("workflow", "alice-tab", ALICE)
    lease_id = acquired.own_lease_id or ""
    original_expiry = acquired.lock.expires_at if acquired.lock else clock.now

    clock.advance(10)
    stale = await hub.heartbeat("workflow", "alice-tab", ALICE, lease_id="stale-token")
    assert stale.lock is not None and stale.lock.expires_at == original_expiry

    renewed = await hub.heartbeat("workflow", "alice-tab", ALICE, lease_id=lease_id)
    assert renewed.lock is not None
    assert renewed.lock.expires_at == clock.now + timedelta(seconds=20)


async def test_stale_release_cannot_delete_concurrent_takeover(clock: MutableClock) -> None:
    redis = LockMutationGateRedis()
    old_hub = RedisCollaborationHub(
        redis, presence_ttl_seconds=30, lock_ttl_seconds=20, clock=clock
    )
    new_hub = RedisCollaborationHub(
        redis, presence_ttl_seconds=30, lock_ttl_seconds=20, clock=clock
    )
    acquired = await old_hub.acquire_lock("workflow", "alice-tab", ALICE)
    old_lease = acquired.own_lease_id or ""

    redis.arm_release = True
    stale_release = asyncio.create_task(
        old_hub.release_lock("workflow", "alice-tab", ALICE, old_lease)
    )
    await asyncio.wait_for(redis.release_started.wait(), timeout=1.0)
    takeover = await new_hub.acquire_lock("workflow", "bob-tab", BOB, takeover=True)
    redis.continue_release.set()
    result = (await asyncio.gather(stale_release, return_exceptions=True))[0]

    assert isinstance(result, LeaseMismatch)
    final = await new_hub.snapshot("workflow", "bob-tab", BOB)
    assert final.own_lease_id == takeover.own_lease_id
    assert final.lock is not None and final.lock.subject == BOB.subject


async def test_redis_expiry_leave_and_subscribers(
    hub: RedisCollaborationHub, clock: MutableClock
) -> None:
    queue, initial = await hub.subscribe("workflow", "observer", BOB)
    assert initial.revision == 0

    acquired = await hub.acquire_lock("workflow", "alice-tab", ALICE)
    assert await queue.get() == acquired.revision
    left = await hub.leave("workflow", "alice-tab", ALICE)
    assert await queue.get() == left.revision
    assert left.lock is None and left.participants == ()

    reacquired = await hub.acquire_lock("workflow", "alice-tab", ALICE)
    assert await queue.get() == reacquired.revision
    clock.advance(31)
    assert await hub.expire("workflow") is True
    assert await queue.get() == reacquired.revision + 1
    expired = await hub.snapshot("workflow", "observer", BOB)
    assert expired.lock is None and expired.participants == ()

    hub.unsubscribe("workflow", queue)


async def test_redis_backend_failure_raises_unavailable(clock: MutableClock) -> None:
    hub = RedisCollaborationHub(
        BrokenRedis(),
        presence_ttl_seconds=30,
        lock_ttl_seconds=20,
        clock=clock,
    )
    with pytest.raises(CollaborationUnavailable):
        await hub.heartbeat("workflow", "alice-tab", ALICE)


async def test_read_only_hub_fail_closed() -> None:
    hub = ReadOnlyCollaborationHub(reason="shared collaboration backend unavailable")
    snap = await hub.snapshot("workflow", "alice-tab", ALICE)
    assert snap.read_only is True
    assert snap.backend == "unavailable"
    assert snap.participants == ()
    with pytest.raises(CollaborationUnavailable):
        await hub.heartbeat("workflow", "alice-tab", ALICE)
    with pytest.raises(CollaborationUnavailable):
        await hub.acquire_lock("workflow", "alice-tab", ALICE)
    left = await hub.leave("workflow", "alice-tab", ALICE)
    assert left.read_only is True


async def test_two_hubs_share_revision_counter(redis: FakeRedis, clock: MutableClock) -> None:
    left = RedisCollaborationHub(redis, presence_ttl_seconds=30, lock_ttl_seconds=20, clock=clock)
    right = RedisCollaborationHub(redis, presence_ttl_seconds=30, lock_ttl_seconds=20, clock=clock)
    first = await left.heartbeat("wf", "a", ALICE)
    second = await right.heartbeat("wf", "b", BOB)
    assert second.revision > first.revision
    assert revision_key("wf") in redis.strings
    shared = await left.snapshot("wf", "a", ALICE)
    assert {p.client_id for p in shared.participants} == {"a", "b"}
