"""Real Redis Streams integration coverage for the distributed event path."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.core.resilience import (
    CircuitOpenError,
    ResilienceConfig,
    ResilienceRegistry,
    RetryBudgetExceeded,
    is_transient_error,
)
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import Workflow
from app.db.repositories import ExecutionEventRelayRepo, ExecutionRepo
from app.engine.event_relay import EventRelay
from app.engine.event_stream import (
    GLOBAL_RELAY_CURSOR,
    ExecutionEventStream,
    StreamEvent,
    close_redis_client,
    connect_event_stream_client,
    execution_stream_key,
)
from app.engine.events import EventBus
from app.engine.sse_tail import iter_execution_sse
from app.memory import RedisMemoryStore, build_memory_store

pytestmark = pytest.mark.redis


@pytest.mark.skipif(not os.getenv("REDIS_TEST_URL"), reason="REDIS_TEST_URL is not configured")
async def test_real_redis_relay_and_duplicate_sse_delivery(tmp_path) -> None:
    redis_url = os.environ["REDIS_TEST_URL"]
    redis = await connect_event_stream_client(redis_url)
    assert redis is not None
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    stream = ExecutionEventStream(redis, maxlen=100)
    relay = EventRelay(
        session_factory,
        redis_client=redis,
        event_stream=stream,
        batch_size=10,
    )
    stream_keys: list[str] = []
    memory: RedisMemoryStore | None = None
    try:
        server_info = await redis.info(section="server")
        redis_version = str(server_info["redis_version"])
        assert int(redis_version.split(".", 1)[0]) >= 5

        built_memory = await build_memory_store(
            Settings(data_dir=tmp_path, redis_url=redis_url),
            session_factory,
        )
        assert isinstance(built_memory, RedisMemoryStore)
        memory = built_memory
        await memory.reset("real-redis-integration")
        await memory.append("real-redis-integration", "user", "remember streams")
        assert [item.content for item in await memory.load("real-redis-integration")] == [
            "remember streams"
        ]

        async with session_factory() as session:
            session.add(
                Workflow(
                    id="wf-real-redis-relay",
                    name="Real Redis relay",
                    dsl_json={
                        "version": "1.0",
                        "name": "Real Redis relay",
                        "nodes": [],
                        "edges": [],
                    },
                )
            )
            await session.flush()
            relay_execution = await ExecutionRepo(session).create("wf-real-redis-relay", {})
            await ExecutionRepo(session).append_events(
                [
                    {
                        "execution_id": relay_execution.id,
                        "seq": 1,
                        "event_type": "workflow_started",
                        "node_id": None,
                        "payload_json": {},
                        "ts": datetime.now(UTC),
                    },
                    {
                        "execution_id": relay_execution.id,
                        "seq": 2,
                        "event_type": "workflow_finished",
                        "node_id": None,
                        "payload_json": {"ok": True},
                        "ts": datetime.now(UTC),
                    },
                ]
            )
            await session.commit()
            relay_execution_id = relay_execution.id

        relay_key = execution_stream_key(relay_execution_id)
        stream_keys.append(relay_key)
        assert await relay.publish_pending() == 2
        relayed = await redis.xrange(relay_key)
        assert [int(fields["seq"]) for _entry_id, fields in relayed] == [1, 2]
        async with session_factory() as session:
            cursor = await ExecutionEventRelayRepo(session).get_or_create(GLOBAL_RELAY_CURSOR)
            assert cursor.last_event_id >= 2

        async with session_factory() as session:
            session.add(
                Workflow(
                    id="wf-real-redis-dedup",
                    name="Real Redis dedup",
                    dsl_json={
                        "version": "1.0",
                        "name": "Real Redis dedup",
                        "nodes": [],
                        "edges": [],
                    },
                )
            )
            await session.flush()
            dedup_execution = await ExecutionRepo(session).create("wf-real-redis-dedup", {})
            await session.commit()
            dedup_execution_id = dedup_execution.id

        dedup_key = execution_stream_key(dedup_execution_id)
        stream_keys.append(dedup_key)
        duplicate = StreamEvent(
            execution_id=dedup_execution_id,
            seq=1,
            event_type="workflow_started",
            node_id=None,
            ts=datetime.now(UTC).isoformat(),
            payload={},
            event_row_id=0,
        )
        await stream.publish(duplicate)
        await stream.publish(duplicate)
        await stream.publish(
            StreamEvent(
                execution_id=dedup_execution_id,
                seq=2,
                event_type="workflow_finished",
                node_id=None,
                ts=datetime.now(UTC).isoformat(),
                payload={"ok": True},
                event_row_id=0,
            )
        )

        async def connected() -> bool:
            return False

        delivered = [
            message
            async for message in iter_execution_sse(
                execution_id=dedup_execution_id,
                after=0,
                bus=EventBus(),
                session_factory=session_factory,
                request_is_disconnected=connected,
                heartbeat_seconds=1,
                event_stream=stream,
                pg_poll_seconds=0.1,
            )
            if message.get("event")
        ]
        assert [int(message["id"]) for message in delivered] == [1, 2]
        assert [message["event"] for message in delivered] == [
            "workflow_started",
            "workflow_finished",
        ]
    finally:
        if memory is not None:
            await memory.reset("real-redis-integration")
            await memory.aclose()
        if stream_keys:
            await redis.delete(*stream_keys)
        await relay.stop()
        await close_redis_client(redis)
        await engine.dispose()


@pytest.mark.skipif(not os.getenv("REDIS_TEST_URL"), reason="REDIS_TEST_URL is not configured")
async def test_real_redis_resilience_state_is_shared_between_registries() -> None:
    redis_url = os.environ["REDIS_TEST_URL"]
    redis = await connect_event_stream_client(redis_url)
    assert redis is not None
    key = "mcp:real-redis:shared-resilience"
    state_key = ResilienceRegistry._state_key(key)  # noqa: SLF001
    budget_key = ResilienceRegistry._budget_key(key)  # noqa: SLF001
    first = ResilienceRegistry(redis_url=redis_url)
    second = ResilienceRegistry(redis_url=redis_url)
    config = ResilienceConfig(
        max_attempts=1,
        failure_threshold=1,
        reset_timeout_seconds=30,
        retry_budget=2,
        backoff_seconds=0,
        max_backoff_seconds=0,
    )
    try:
        await redis.delete(state_key, budget_key)

        async def fail() -> None:
            raise TimeoutError("shared outage")

        with pytest.raises(TimeoutError):
            await first.execute(key, fail, config=config, retryable=is_transient_error)
        with pytest.raises(CircuitOpenError):
            await second.execute(key, fail, config=config, retryable=is_transient_error)
        snapshots = await second.snapshots_async()
        assert snapshots[0]["key"] == key
        assert snapshots[0]["state"] == "open"
    finally:
        await first.close()
        await second.close()
        await redis.delete(state_key, budget_key)
        await close_redis_client(redis)


@pytest.mark.skipif(not os.getenv("REDIS_TEST_URL"), reason="REDIS_TEST_URL is not configured")
async def test_real_redis_resilience_coordinates_half_open_probe_and_retry_budget() -> None:
    redis_url = os.environ["REDIS_TEST_URL"]
    redis = await connect_event_stream_client(redis_url)
    assert redis is not None
    first = ResilienceRegistry(redis_url=redis_url)
    second = ResilienceRegistry(redis_url=redis_url)
    probe_key = "provider:real-redis:half-open"
    budget_key = "provider:real-redis:retry-budget"
    stale_key = "provider:real-redis:stale-success"
    orphan_key = "provider:real-redis:orphaned-probe"
    redis_keys = (
        ResilienceRegistry._state_key(probe_key),  # noqa: SLF001
        ResilienceRegistry._budget_key(probe_key),  # noqa: SLF001
        ResilienceRegistry._state_key(budget_key),  # noqa: SLF001
        ResilienceRegistry._budget_key(budget_key),  # noqa: SLF001
        ResilienceRegistry._state_key(stale_key),  # noqa: SLF001
        ResilienceRegistry._budget_key(stale_key),  # noqa: SLF001
        ResilienceRegistry._state_key(orphan_key),  # noqa: SLF001
        ResilienceRegistry._budget_key(orphan_key),  # noqa: SLF001
    )
    probe_config = ResilienceConfig(
        max_attempts=1,
        failure_threshold=1,
        reset_timeout_seconds=0.05,
        retry_budget=1,
        retry_window_seconds=0.05,
        backoff_seconds=0,
        max_backoff_seconds=0,
    )
    budget_config = ResilienceConfig(
        max_attempts=2,
        failure_threshold=20,
        reset_timeout_seconds=30,
        retry_budget=1,
        retry_window_seconds=30,
        backoff_seconds=0,
        max_backoff_seconds=0,
    )
    orphan_config = ResilienceConfig(
        max_attempts=1,
        failure_threshold=1,
        reset_timeout_seconds=0.05,
        half_open_probe_timeout_seconds=0.05,
        retry_budget=1,
        retry_window_seconds=1,
        backoff_seconds=0,
        max_backoff_seconds=0,
    )
    try:
        await redis.delete(*redis_keys)

        async def fail() -> None:
            raise TimeoutError("shared outage")

        with pytest.raises(TimeoutError):
            await first.execute(
                probe_key,
                fail,
                config=probe_config,
                retryable=is_transient_error,
            )

        # The open state must outlive idle-key cleanup. Otherwise every replica
        # treats the first post-cooldown call as a fresh closed circuit.
        await asyncio.sleep(2.1)
        probe_started = asyncio.Event()
        release_probe = asyncio.Event()

        async def recover() -> str:
            probe_started.set()
            await release_probe.wait()
            return "healthy"

        probe = asyncio.create_task(
            first.execute(
                probe_key,
                recover,
                config=probe_config,
                retryable=is_transient_error,
            )
        )
        await asyncio.wait_for(probe_started.wait(), timeout=1)
        try:
            with pytest.raises(CircuitOpenError):
                await second.execute(
                    probe_key,
                    lambda: asyncio.sleep(0, result="duplicate"),
                    config=probe_config,
                    retryable=is_transient_error,
                )
        finally:
            release_probe.set()
        assert await probe == "healthy"

        first_calls = 0

        async def first_budget_failure() -> None:
            nonlocal first_calls
            first_calls += 1
            raise TimeoutError("first retry consumer")

        with pytest.raises(TimeoutError):
            await first.execute(
                budget_key,
                first_budget_failure,
                config=budget_config,
                retryable=is_transient_error,
            )
        assert first_calls == 2

        second_calls = 0

        async def second_budget_failure() -> None:
            nonlocal second_calls
            second_calls += 1
            raise TimeoutError("second retry consumer")

        with pytest.raises(RetryBudgetExceeded):
            await second.execute(
                budget_key,
                second_budget_failure,
                config=budget_config,
                retryable=is_transient_error,
            )
        assert second_calls == 1

        stale_started = asyncio.Event()
        release_stale = asyncio.Event()

        async def delayed_success() -> str:
            stale_started.set()
            await release_stale.wait()
            return "late"

        stale_call = asyncio.create_task(
            first.execute(
                stale_key,
                delayed_success,
                config=probe_config,
                retryable=is_transient_error,
            )
        )
        await asyncio.wait_for(stale_started.wait(), timeout=1)
        with pytest.raises(TimeoutError):
            await second.execute(
                stale_key,
                fail,
                config=probe_config,
                retryable=is_transient_error,
            )
        release_stale.set()
        assert await stale_call == "late"
        with pytest.raises(CircuitOpenError):
            await first.execute(
                stale_key,
                lambda: asyncio.sleep(0, result="must stay blocked"),
                config=probe_config,
                retryable=is_transient_error,
            )

        with pytest.raises(TimeoutError):
            await first.execute(
                orphan_key,
                fail,
                config=orphan_config,
                retryable=is_transient_error,
            )
        await asyncio.sleep(0.08)
        orphan_started = asyncio.Event()
        release_orphan = asyncio.Event()

        async def abandoned_probe() -> str:
            orphan_started.set()
            await release_orphan.wait()
            return "stale probe"

        orphan_call = asyncio.create_task(
            first.execute(
                orphan_key,
                abandoned_probe,
                config=orphan_config,
                retryable=is_transient_error,
            )
        )
        await asyncio.wait_for(orphan_started.wait(), timeout=1)
        await asyncio.sleep(0.08)
        try:
            assert (
                await second.execute(
                    orphan_key,
                    lambda: asyncio.sleep(0, result="replacement probe"),
                    config=orphan_config,
                    retryable=is_transient_error,
                )
                == "replacement probe"
            )
        finally:
            release_orphan.set()
        assert await orphan_call == "stale probe"
        orphan_snapshot = await first.snapshot_async(orphan_key)
        assert orphan_snapshot is not None
        assert orphan_snapshot["state"] == "closed"
    finally:
        await first.close()
        await second.close()
        await redis.delete(*redis_keys)
        await close_redis_client(redis)
