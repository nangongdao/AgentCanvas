"""Durable periodic online-source scheduling and fencing (C4-3)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.repositories import OnlineSourceRepo
from app.engine.online_source_scheduler import OnlineSourceScheduler
from app.main import create_app
from app.rag.online_sources import FetchedPage
from app.services.online_source_sync import OnlineSourceSyncService


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        local_embedding_dimensions=64,
        rate_limit_default_requests=1000,
        rate_limit_ingest_requests=1000,
        rate_limit_upload_requests=1000,
        online_source_sync_poll_seconds=3600,
        online_source_sync_batch_size=10,
        online_source_sync_lease_seconds=60,
    )


def test_two_workers_claim_one_due_online_source_exactly_once(tmp_path, monkeypatch) -> None:
    import app.services.online_source_sync as sync_module

    fetches = 0

    async def controlled_crawl(url, **_kwargs):  # noqa: ANN001
        nonlocal fetches
        fetches += 1
        await asyncio.sleep(0.05)
        return [
            FetchedPage(
                url=url,
                text="scheduled source body",
                sha256="a" * 64,
                status_code=200,
                content_type="text/plain",
            )
        ]

    monkeypatch.setattr(sync_module, "crawl_source", controlled_crawl)
    with TestClient(create_app(_settings(tmp_path))) as client:
        container = client.app.state.container  # type: ignore[attr-defined]
        assert client.portal is not None
        kb = client.post("/api/knowledge-bases", json={"name": "Scheduled KB"}).json()
        source = client.post(
            f"/api/knowledge-bases/{kb['id']}/online-sources",
            json={
                "url": "https://public-edge.test/scheduled",
                "sync_interval_minutes": 60,
            },
        ).json()

        async def compete() -> tuple[int, int]:
            first = OnlineSourceScheduler(
                container.session_factory,
                container.online_source_sync,
                owner_id="online-worker-a",
                poll_seconds=3600,
                batch_size=10,
            )
            second = OnlineSourceScheduler(
                container.session_factory,
                container.online_source_sync,
                owner_id="online-worker-b",
                poll_seconds=3600,
                batch_size=10,
            )
            left, right = await asyncio.gather(first.sync_once(), second.sync_once())
            return left, right

        assert sum(client.portal.call(compete)) == 1
        assert fetches == 1
        row = next(
            item
            for item in client.get(
                f"/api/knowledge-bases/{kb['id']}/online-sources"
            ).json()
            if item["id"] == source["id"]
        )
        assert row["status"] == "ready"
        assert row["last_synced_at"] is not None
        assert row["next_sync_at"] > row["last_synced_at"]


def test_stale_online_source_generation_cannot_finish_after_reclaim(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        container = client.app.state.container  # type: ignore[attr-defined]
        assert client.portal is not None
        kb = client.post("/api/knowledge-bases", json={"name": "Fenced KB"}).json()
        source = client.post(
            f"/api/knowledge-bases/{kb['id']}/online-sources",
            json={
                "url": "https://public-edge.test/fenced",
                "sync_interval_minutes": 60,
            },
        ).json()

        async def exercise_fence() -> tuple[int, int, bool, bool]:
            started = datetime.fromisoformat(source["next_sync_at"]).astimezone(UTC)
            async with container.session_factory() as session:
                repo = OnlineSourceRepo(session)
                first = await repo.claim(
                    source["id"],
                    now=started,
                    stale_before=started - timedelta(seconds=60),
                    force=False,
                )
                await session.commit()
                assert first is not None
                first_generation = first.sync_generation

            reclaimed_at = started + timedelta(seconds=61)
            async with container.session_factory() as session:
                repo = OnlineSourceRepo(session)
                second = await repo.claim(
                    source["id"],
                    now=reclaimed_at,
                    stale_before=reclaimed_at - timedelta(seconds=60),
                    force=False,
                )
                await session.commit()
                assert second is not None
                second_generation = second.sync_generation

            async with container.session_factory() as session:
                repo = OnlineSourceRepo(session)
                stale_finished = await repo.finish_success(
                    source["id"],
                    generation=first_generation,
                    completed_at=reclaimed_at,
                    document_id="stale-document",
                    content_sha256="a" * 64,
                )
                await session.commit()

            async with container.session_factory() as session:
                repo = OnlineSourceRepo(session)
                current_finished = await repo.finish_success(
                    source["id"],
                    generation=second_generation,
                    completed_at=reclaimed_at,
                    document_id=None,
                    content_sha256=None,
                )
                await session.commit()
            return first_generation, second_generation, stale_finished, current_finished

        first_generation, second_generation, stale_finished, current_finished = (
            client.portal.call(exercise_fence)
        )
        assert second_generation == first_generation + 1
        assert stale_finished is False
        assert current_finished is True


def test_online_source_configuration_is_rejected_when_claim_wins_race(
    tmp_path, monkeypatch
) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        container = client.app.state.container  # type: ignore[attr-defined]
        assert client.portal is not None
        kb = client.post("/api/knowledge-bases", json={"name": "Busy source KB"}).json()
        source = client.post(
            f"/api/knowledge-bases/{kb['id']}/online-sources",
            json={
                "url": "https://public-edge.test/busy",
                "max_pages": 1,
                "sync_interval_minutes": 60,
            },
        ).json()

        original_configure = OnlineSourceRepo.configure

        async def claim_then_configure(repo, row, **kwargs):  # noqa: ANN001
            now = datetime.now(UTC)
            async with container.session_factory() as session:
                claimed = await OnlineSourceRepo(session).claim(
                    source["id"],
                    now=now,
                    stale_before=now - timedelta(seconds=60),
                    force=True,
                )
                assert claimed is not None
                await session.commit()
            return await original_configure(repo, row, **kwargs)

        monkeypatch.setattr(OnlineSourceRepo, "configure", claim_then_configure)
        response = client.put(
            f"/api/knowledge-bases/{kb['id']}/online-sources/{source['id']}",
            json={"max_pages": 2, "sync_interval_minutes": 15},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "online source sync is running"
        current = next(
            row
            for row in client.get(
                f"/api/knowledge-bases/{kb['id']}/online-sources"
            ).json()
            if row["id"] == source["id"]
        )
        assert current["status"] == "syncing"
        assert current["max_pages"] == 1
        assert current["sync_interval_minutes"] == 60


def test_online_source_scheduler_timestamps_each_claim_when_batch_reaches_it(
    tmp_path, monkeypatch
) -> None:
    import app.engine.online_source_scheduler as scheduler_module

    scan_at = datetime(2030, 8, 19, 1, 0, tzinfo=UTC)
    claim_times = [
        scan_at + timedelta(seconds=10),
        scan_at + timedelta(seconds=70),
    ]

    class SteppedDateTime:
        values = iter([scan_at, *claim_times])

        @classmethod
        def now(cls, _tz):  # noqa: ANN001
            return next(cls.values)

    with TestClient(create_app(_settings(tmp_path))) as client:
        container = client.app.state.container  # type: ignore[attr-defined]
        assert client.portal is not None
        kb = client.post("/api/knowledge-bases", json={"name": "Batch clock KB"}).json()
        for suffix in ("a", "b"):
            client.post(
                f"/api/knowledge-bases/{kb['id']}/online-sources",
                json={
                    "url": f"https://public-edge.test/batch-{suffix}",
                    "sync_interval_minutes": 60,
                },
            )

        monkeypatch.setattr(scheduler_module, "datetime", SteppedDateTime)
        seen: list[datetime] = []

        class RecordingSyncService:
            lease_seconds = 60

            async def sync_source(self, _source_id, *, now, **_kwargs):  # noqa: ANN001
                seen.append(now)

        async def run_batch() -> int:
            scheduler = OnlineSourceScheduler(
                container.session_factory,
                cast(OnlineSourceSyncService, RecordingSyncService()),
                owner_id="clock-worker",
                poll_seconds=3600,
                batch_size=10,
            )
            return await scheduler.sync_once()

        assert client.portal.call(run_batch) == 2
        assert seen == claim_times


def test_cancelled_online_source_sync_cleans_temporary_document_and_requeues(
    tmp_path, monkeypatch
) -> None:
    import app.services.online_source_sync as sync_module

    async def one_page(url, **_kwargs):  # noqa: ANN001
        return [
            FetchedPage(
                url=url,
                text="cancelled source body",
                sha256="c" * 64,
                status_code=200,
                content_type="text/plain",
            )
        ]

    monkeypatch.setattr(sync_module, "crawl_source", one_page)
    with TestClient(create_app(_settings(tmp_path))) as client:
        container = client.app.state.container  # type: ignore[attr-defined]
        assert client.portal is not None
        kb = client.post("/api/knowledge-bases", json={"name": "Cancelled source KB"}).json()
        source = client.post(
            f"/api/knowledge-bases/{kb['id']}/online-sources",
            json={
                "url": "https://public-edge.test/cancelled",
                "sync_interval_minutes": 60,
            },
        ).json()

        async def cancel_during_ingest() -> None:
            ingest_started = asyncio.Event()
            never_finish = asyncio.Event()

            async def blocked_ingest(*_args, **_kwargs):  # noqa: ANN001
                ingest_started.set()
                await never_finish.wait()

            monkeypatch.setattr(container.rag_service, "ingest_document", blocked_ingest)
            task = asyncio.create_task(container.online_source_sync.sync_source(source["id"]))
            await ingest_started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        client.portal.call(cancel_during_ingest)
        current = next(
            row
            for row in client.get(
                f"/api/knowledge-bases/{kb['id']}/online-sources"
            ).json()
            if row["id"] == source["id"]
        )
        assert current["status"] == "failed"
        assert current["sync_started_at"] is None
        next_sync_at = datetime.fromisoformat(current["next_sync_at"])
        if next_sync_at.tzinfo is None:
            next_sync_at = next_sync_at.replace(tzinfo=UTC)
        assert next_sync_at <= datetime.now(UTC)
        assert client.get(f"/api/knowledge-bases/{kb['id']}/documents").json()["items"] == []


def test_cancellation_after_committed_swap_keeps_published_document(
    tmp_path, monkeypatch
) -> None:
    import app.services.online_source_sync as sync_module

    async def one_page(url, **_kwargs):  # noqa: ANN001
        return [
            FetchedPage(
                url=url,
                text="committed source body",
                sha256="d" * 64,
                status_code=200,
                content_type="text/plain",
            )
        ]

    monkeypatch.setattr(sync_module, "crawl_source", one_page)
    with TestClient(create_app(_settings(tmp_path))) as client:
        container = client.app.state.container  # type: ignore[attr-defined]
        assert client.portal is not None
        kb = client.post("/api/knowledge-bases", json={"name": "Committed source KB"}).json()
        source = client.post(
            f"/api/knowledge-bases/{kb['id']}/online-sources",
            json={"url": "https://public-edge.test/committed"},
        ).json()

        async def cancel_after_commit() -> None:
            original_finish = container.online_source_sync._finish_success  # noqa: SLF001

            async def commit_then_cancel(*args, **kwargs):  # noqa: ANN001
                row = await original_finish(*args, **kwargs)
                claimed_at = datetime.now(UTC)
                async with container.session_factory() as session:
                    next_generation = await OnlineSourceRepo(session).claim(
                        source["id"],
                        now=claimed_at,
                        stale_before=claimed_at - timedelta(seconds=60),
                        force=True,
                    )
                    assert next_generation is not None
                    await session.commit()
                task = asyncio.current_task()
                assert task is not None
                task.cancel()
                await asyncio.sleep(0)
                return row

            monkeypatch.setattr(
                container.online_source_sync,
                "_finish_success",
                commit_then_cancel,
            )
            with pytest.raises(asyncio.CancelledError):
                await container.online_source_sync.sync_source(source["id"])

        client.portal.call(cancel_after_commit)
        current = next(
            row
            for row in client.get(
                f"/api/knowledge-bases/{kb['id']}/online-sources"
            ).json()
            if row["id"] == source["id"]
        )
        assert current["status"] == "syncing"
        documents = client.get(f"/api/knowledge-bases/{kb['id']}/documents").json()["items"]
        assert [(row["id"], row["status"]) for row in documents] == [
            (current["document_id"], "ready")
        ]
