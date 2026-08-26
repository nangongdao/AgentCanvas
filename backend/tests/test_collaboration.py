"""Deterministic contracts for transient workflow collaboration state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from app.core.collaboration import (
    ClientIdentityConflict,
    CollaborationActor,
    LeaseMismatch,
    LockUnavailable,
    WorkflowCollaborationHub,
)


@dataclass
class MutableClock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(datetime(2026, 8, 7, 12, 0, tzinfo=UTC))


@pytest.fixture
def hub(clock: MutableClock) -> WorkflowCollaborationHub:
    return WorkflowCollaborationHub(
        presence_ttl_seconds=30,
        lock_ttl_seconds=20,
        clock=clock,
    )


ALICE = CollaborationActor("user:alice", "alice@example.com")
BOB = CollaborationActor("user:bob", "bob@example.com")


async def test_presence_heartbeat_is_actor_bound_and_expires(
    hub: WorkflowCollaborationHub, clock: MutableClock
) -> None:
    snapshot = await hub.heartbeat("workflow", "alice-tab", ALICE)
    assert snapshot.revision == 1
    assert [(item.subject, item.is_self) for item in snapshot.participants] == [
        ("alice@example.com", True)
    ]

    bob_view = await hub.snapshot("workflow", "bob-tab", BOB)
    assert bob_view.participants[0].is_self is False
    with pytest.raises(ClientIdentityConflict):
        await hub.heartbeat("workflow", "alice-tab", BOB)

    clock.advance(31)
    expired = await hub.snapshot("workflow", "bob-tab", BOB)
    assert expired.participants == ()
    assert expired.revision == 2


async def test_lock_requires_explicit_takeover_and_hides_other_lease(
    hub: WorkflowCollaborationHub,
) -> None:
    alice = await hub.acquire_lock("workflow", "alice-tab", ALICE)
    assert alice.own_lease_id
    assert alice.lock is not None and alice.lock.owned_by_self

    bob = await hub.snapshot("workflow", "bob-tab", BOB)
    assert bob.own_lease_id is None
    assert bob.lock is not None and not bob.lock.owned_by_self
    with pytest.raises(LockUnavailable):
        await hub.acquire_lock("workflow", "bob-tab", BOB)

    taken = await hub.acquire_lock("workflow", "bob-tab", BOB, takeover=True)
    assert taken.own_lease_id
    assert taken.own_lease_id != alice.own_lease_id
    assert taken.lock is not None and taken.lock.subject == "bob@example.com"
    with pytest.raises(LeaseMismatch):
        await hub.release_lock("workflow", "alice-tab", ALICE, alice.own_lease_id or "")


async def test_matching_heartbeat_renews_lock_but_stale_token_does_not(
    hub: WorkflowCollaborationHub, clock: MutableClock
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


async def test_repeated_acquire_for_same_actor_and_client_is_idempotent(
    hub: WorkflowCollaborationHub,
) -> None:
    first = await hub.acquire_lock("workflow", "alice-tab", ALICE)
    repeated = await hub.acquire_lock("workflow", "alice-tab", ALICE)
    assert repeated.own_lease_id == first.own_lease_id

    with pytest.raises(LeaseMismatch):
        await hub.acquire_lock("workflow", "alice-tab", ALICE, lease_id="x" * 43)


async def test_expiry_and_leave_release_lock_and_notify_subscribers(
    hub: WorkflowCollaborationHub, clock: MutableClock
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


async def test_close_wakes_stream_subscribers(
    hub: WorkflowCollaborationHub,
) -> None:
    queue, _ = await hub.subscribe("workflow", "observer", BOB)
    await hub.close()
    assert await queue.get() is None
    with pytest.raises(RuntimeError, match="closed"):
        await hub.snapshot("workflow", "observer", BOB)
