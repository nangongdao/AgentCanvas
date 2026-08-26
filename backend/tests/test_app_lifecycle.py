"""Application lifespan cleanup regressions."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_lifespan_shutdown_runs_when_application_context_raises(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        log_level="WARNING",
    )
    app = create_app(settings)
    container = None

    try:
        with pytest.raises(RuntimeError, match="lifespan failure"):
            async with app.router.lifespan_context(app):
                container = app.state.container
                await container.execution_engine.start(
                    "demo-linear", {"user_query": "shutdown regression"}
                )
                raise RuntimeError("lifespan failure")

        assert container is not None
        assert container.execution_engine._shutting_down  # noqa: SLF001
        assert container.execution_engine.active_executions() == []

        database = tmp_path / "app.db"
        moved = tmp_path / "app-moved.db"
        database.rename(moved)
        moved.rename(database)
    finally:
        if container is not None and not container.execution_engine._shutting_down:  # noqa: SLF001
            await container.shutdown()


@pytest.mark.asyncio
async def test_concurrent_starts_do_not_deadlock_the_database_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        execution_max_concurrent=32,
        execution_request_timeout_seconds=15,
        rate_limit_default_requests=1_000,
        rate_limit_execution_requests=1_000,
        log_level="WARNING",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        engine = app.state.container.execution_engine
        original_start = engine.start
        barrier = asyncio.Event()
        reached = 0

        async def synchronized_start(*args, **kwargs):
            nonlocal reached
            reached += 1
            if reached >= 15:
                barrier.set()
            await barrier.wait()
            return await original_start(*args, **kwargs)

        monkeypatch.setattr(engine, "start", synchronized_start)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            responses = await asyncio.wait_for(
                asyncio.gather(
                    *(
                        client.post(
                            "/api/workflows/demo-linear/run",
                            headers={"Idempotency-Key": f"pool-deadlock-{index}"},
                            json={"inputs": {"user_query": "pool regression"}},
                        )
                        for index in range(16)
                    )
                ),
                timeout=20,
            )

        assert [response.status_code for response in responses] == [201] * 16
