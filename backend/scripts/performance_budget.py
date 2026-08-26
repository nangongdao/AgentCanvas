"""Repeatable U4 API/runtime performance budgets on an isolated application."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import insert, select

from app.core.config import Settings
from app.db.models import Execution, ExecutionEventRow, Workflow, WorkflowVersion
from app.main import create_app

CONCURRENCY_LEVELS = (1, 10, 50)
EXECUTION_CREATE_P95_BUDGET_MS = {"1": 500, "10": 1_500, "50": 15_000}


def percentile(samples: Sequence[float], fraction: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999) - 1))]


async def timed_wave(
    count: int,
    operation: Callable[[int], Awaitable[httpx.Response]],
    *,
    expected_status: int = 200,
) -> tuple[list[float], list[httpx.Response]]:
    async def timed(index: int) -> tuple[float, httpx.Response]:
        started = time.perf_counter()
        response = await operation(index)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if response.status_code != expected_status:
            raise RuntimeError(
                f"operation returned {response.status_code}, expected {expected_status}: "
                f"{response.text[:500]}"
            )
        return elapsed_ms, response

    tasks = [asyncio.create_task(timed(index)) for index in range(count)]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return [result[0] for result in results], [result[1] for result in results]


async def wait_for_executions(
    client: httpx.AsyncClient, execution_ids: Sequence[str]
) -> None:
    # The mock provider streams ~70 tokens at 30ms each (~2.1s per execution),
    # and high-concurrency waves drain through the single-process claim loop.
    # Scale the terminal-state window with the wave size so the *measurement*
    # of creation latency is not cut short by an unrelated drain deadline —
    # the product gate remains the creation-response p95 itself (C6-4).
    timeout_s = max(30.0, len(execution_ids) * 1.5)
    pending = set(execution_ids)
    deadline = time.monotonic() + timeout_s
    while pending and time.monotonic() < deadline:
        for execution_id in list(pending):
            response = await client.get(f"/api/executions/{execution_id}")
            response.raise_for_status()
            if response.json()["status"] in {"succeeded", "failed", "cancelled"}:
                pending.remove(execution_id)
        if pending:
            # Avoid turning the terminal-state observer into a second SQLite
            # workload while a high-concurrency wave is draining.
            await asyncio.sleep(0.1)
    if pending:
        raise RuntimeError(f"executions did not reach terminal state: {sorted(pending)}")


async def wait_for_runtime_idle(app: Any) -> None:
    engine = app.state.container.execution_engine
    deadline = time.monotonic() + 30
    while engine.active_executions() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    if engine.active_executions():
        raise RuntimeError("execution tasks did not exit after terminal persistence")
    await app.state.container.event_bus.flush()


def execution_start_operation(
    client: httpx.AsyncClient, concurrency: int
) -> Callable[[int], Awaitable[httpx.Response]]:
    async def operation(index: int) -> httpx.Response:
        return await client.post(
            "/api/workflows/demo-linear/run",
            headers={
                "Idempotency-Key": f"perf-{concurrency}-{index}-{time.time_ns()}"
            },
            json={"inputs": {"user_query": "performance budget"}},
        )

    return operation


async def benchmark_execution_create(
    client: httpx.AsyncClient,
    *,
    concurrency_levels: Sequence[int] = CONCURRENCY_LEVELS,
    waves: int = 1,
    wait_for_terminal: bool = True,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for concurrency in concurrency_levels:
        samples: list[float] = []
        wave_p95_ms: list[float] = []
        for _wave in range(waves):
            wave_samples, responses = await timed_wave(
                concurrency,
                execution_start_operation(client, concurrency),
                expected_status=201,
            )
            if wait_for_terminal:
                await wait_for_executions(
                    client, [response.json()["id"] for response in responses]
                )
            samples.extend(wave_samples)
            wave_p95_ms.append(round(percentile(wave_samples, 0.95), 3))
        result: dict[str, Any] = {
            "samples": len(samples),
            "p50_ms": round(percentile(samples, 0.50), 3),
            "p95_ms": round(percentile(samples, 0.95), 3),
            "max_ms": round(max(samples), 3),
        }
        if waves > 1:
            result["wave_p95_ms"] = wave_p95_ms
        results[str(concurrency)] = result
        print(
            f"execution create c={concurrency} p95={results[str(concurrency)]['p95_ms']}ms",
            flush=True,
        )
    return results


async def seed_workflow_volume(app: Any, count: int = 10_000) -> None:
    container = app.state.container
    now = datetime.now(UTC)
    dsl = {
        "version": "1.0",
        "name": "Pagination benchmark",
        "variables": [],
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}},
            {"id": "end", "type": "end", "position": {"x": 200, "y": 0}},
        ],
        "edges": [{"id": "edge", "source": "start", "target": "end"}],
    }
    rows = [
        {
            "id": f"perf-page-{index:05d}",
            "name": f"Performance workflow {index:05d}",
            "description": "pagination benchmark",
            "dsl_json": dsl,
            "version": 1,
            "is_archived": False,
            "created_at": now + timedelta(microseconds=index),
            "updated_at": now + timedelta(microseconds=index),
        }
        for index in range(count)
    ]
    async with container.session_factory() as session:
        await session.execute(insert(Workflow), rows)
        await session.commit()


async def benchmark_pagination(client: httpx.AsyncClient) -> dict[str, Any]:
    samples: list[float] = []
    cursor: str | None = None
    for _ in range(25):
        params: dict[str, str | int] = {
            "limit": 200,
            "sort": "updated_at",
            "order": "desc",
        }
        if cursor:
            params["cursor"] = cursor
        started = time.perf_counter()
        response = await client.get("/api/workflows", params=params)
        samples.append((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        payload = response.json()
        if len(payload["items"]) != 200:
            raise RuntimeError("10k pagination benchmark returned an incomplete page")
        cursor = payload["next_cursor"]
    return {
        "records": 10_000,
        "page_size": 200,
        "samples": len(samples),
        "p50_ms": round(percentile(samples, 0.50), 3),
        "p95_ms": round(percentile(samples, 0.95), 3),
        "max_ms": round(max(samples), 3),
    }


async def seed_replay(app: Any) -> str:
    execution_id = "perf-sse-replay"
    now = datetime.now(UTC)
    events = [
        {
            "execution_id": execution_id,
            "seq": seq,
            "event_type": (
                "workflow_started"
                if seq == 1
                else "workflow_finished"
                if seq == 1000
                else "node_streaming"
            ),
            "node_id": None if seq in {1, 1000} else "agent",
            "payload_json": {"delta": "x"} if 1 < seq < 1000 else {},
            "ts": now + timedelta(microseconds=seq),
        }
        for seq in range(1, 1001)
    ]
    async with app.state.container.session_factory() as session:
        version_id = await session.scalar(
            select(WorkflowVersion.id)
            .where(WorkflowVersion.workflow_id == "demo-linear")
            .order_by(WorkflowVersion.number.desc())
        )
        if version_id is None:
            raise RuntimeError("performance seed requires a demo-linear workflow version")
        await session.execute(
            insert(Execution),
            [
                {
                    "id": execution_id,
                    "workflow_id": "demo-linear",
                    "workflow_version_id": version_id,
                    "status": "succeeded",
                    "input_json": {},
                    "output_json": {},
                    "error": None,
                    "thread_id": execution_id,
                    "session_id": None,
                    "started_at": now,
                    "finished_at": now,
                }
            ],
        )
        await session.execute(insert(ExecutionEventRow), events)
        await session.commit()
    return execution_id


async def benchmark_sse_replay(client: httpx.AsyncClient, execution_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    response = await client.get(f"/api/executions/{execution_id}/events")
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    ids = [int(line[4:]) for line in response.text.splitlines() if line.startswith("id: ")]
    terminal_count = response.text.count("event: workflow_finished")
    return {
        "events": len(ids),
        "elapsed_ms": round(elapsed_ms, 3),
        "contiguous": ids == list(range(1, 1001)),
        "duplicate_terminal": terminal_count != 1,
    }


async def benchmark_mcp(client: httpx.AsyncClient) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for concurrency in CONCURRENCY_LEVELS:
        samples, _ = await timed_wave(
            concurrency,
            lambda _index: client.get(
                "/api/mcp/servers/demo-calculator/tools",
                params={"refresh": "true"},
            ),
        )
        results[str(concurrency)] = {
            "p95_ms": round(percentile(samples, 0.95), 3),
            "max_ms": round(max(samples), 3),
        }
    return results


async def prepare_rag(client: httpx.AsyncClient) -> str:
    kb_response = await client.post(
        "/api/knowledge-bases",
        json={
            "name": "Performance knowledge",
            "description": "Isolated RAG benchmark",
            "embedding_model_id": "default-embedding",
            "chunk_size": 300,
            "chunk_overlap": 30,
        },
    )
    kb_response.raise_for_status()
    kb_id = kb_response.json()["id"]
    upload = await client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        files={
            "file": (
                "performance.md",
                b"AgentCanvas uses canary rollout health checks. " * 100,
                "text/markdown",
            )
        },
    )
    upload.raise_for_status()
    document_id = upload.json()["id"]
    ingest = await client.post(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}/ingest"
    )
    if ingest.status_code != 202:
        raise RuntimeError(f"RAG ingest start failed: {ingest.text}")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = await client.get(
            f"/api/knowledge-bases/{kb_id}/documents/{document_id}/ingest"
        )
        status.raise_for_status()
        payload = status.json()
        if payload["job_status"] == "succeeded":
            return kb_id
        if payload["job_status"] in {"failed", "cancelled"}:
            raise RuntimeError(f"RAG ingest failed: {payload}")
        await asyncio.sleep(0.05)
    raise RuntimeError("RAG ingest timed out")


async def benchmark_rag(client: httpx.AsyncClient, kb_id: str) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for concurrency in CONCURRENCY_LEVELS:
        samples, _ = await timed_wave(
            concurrency,
            lambda _index: client.post(
                f"/api/knowledge-bases/{kb_id}/retrieve",
                json={"query": "canary rollout health checks", "top_k": 3},
            ),
        )
        results[str(concurrency)] = {
            "p95_ms": round(percentile(samples, 0.95), 3),
            "max_ms": round(max(samples), 3),
        }
    return results


def assert_execution_budgets(report: dict[str, Any]) -> None:
    failures: list[str] = []
    for concurrency, result in report["execution_create"].items():
        limit = EXECUTION_CREATE_P95_BUDGET_MS[concurrency]
        if result["p95_ms"] >= limit:
            failures.append(
                f"execution create c={concurrency} p95 {result['p95_ms']}ms >= {limit}ms"
            )
    if failures:
        raise RuntimeError("performance budget failed:\n" + "\n".join(failures))


def assert_budgets(report: dict[str, Any]) -> None:
    assert_execution_budgets(report)
    failures: list[str] = []
    if report["pagination_10k"]["p95_ms"] >= 300:
        failures.append(f"10k pagination p95 {report['pagination_10k']['p95_ms']}ms >= 300ms")
    replay = report["sse_replay_1000"]
    if replay["events"] != 1000 or not replay["contiguous"] or replay["duplicate_terminal"]:
        failures.append(f"SSE replay integrity failed: {replay}")
    if failures:
        raise RuntimeError("performance budget failed:\n" + "\n".join(failures))


async def run(
    full: bool,
    *,
    execution_only: bool = False,
    execution_concurrency: int = 10,
    execution_waves: int = 1,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="agentcanvas-performance-") as data_dir:
        settings = Settings(
            data_dir=Path(data_dir),
            environment="test",
            auth_mode="disabled",
            secret_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            rate_limit_default_requests=50_000,
            rate_limit_execution_requests=10_000,
            rate_limit_retrieval_requests=10_000,
            rate_limit_mcp_requests=10_000,
            execution_max_concurrent=64,
            model_max_concurrent=64,
            model_rate_limit_calls=10_000,
            retrieval_max_concurrent=64,
            mcp_max_concurrent=64,
            log_level="WARNING",
        )
        app = create_app(settings)
        print("starting isolated benchmark application", flush=True)
        async with app.router.lifespan_context(app):
            print("isolated benchmark application ready", flush=True)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                levels = (execution_concurrency,) if execution_only else CONCURRENCY_LEVELS
                execution = await benchmark_execution_create(
                    client,
                    concurrency_levels=levels,
                    waves=execution_waves,
                )
                await wait_for_runtime_idle(app)
                if execution_only:
                    execution_report = {
                        "mode": "execution-only",
                        "execution_create": execution,
                    }
                    assert_execution_budgets(execution_report)
                    return execution_report
                print("seeding 10,000 workflows", flush=True)
                await seed_workflow_volume(app)
                pagination = await benchmark_pagination(client)
                print(f"pagination p95={pagination['p95_ms']}ms", flush=True)
                replay_id = await seed_replay(app)
                replay = await benchmark_sse_replay(client, replay_id)
                print(f"SSE replay elapsed={replay['elapsed_ms']}ms", flush=True)
                report: dict[str, Any] = {
                    "mode": "full" if full else "smoke",
                    "execution_create": execution,
                    "pagination_10k": pagination,
                    "sse_replay_1000": replay,
                }
                if full:
                    report["mcp_discovery"] = await benchmark_mcp(client)
                    kb_id = await prepare_rag(client)
                    report["rag_retrieval"] = await benchmark_rag(client, kb_id)
                assert_budgets(report)
                return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="also benchmark MCP and RAG")
    parser.add_argument(
        "--execution-only",
        action="store_true",
        help="run only one execution-create concurrency level",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        choices=CONCURRENCY_LEVELS,
        default=10,
        help="execution-create concurrency for --execution-only",
    )
    parser.add_argument(
        "--waves",
        type=int,
        default=1,
        help="number of execution-create waves (diagnostic mode only)",
    )
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    args = parser.parse_args()
    if args.full and args.execution_only:
        parser.error("--full and --execution-only cannot be combined")
    if args.waves < 1:
        parser.error("--waves must be at least 1")
    if not args.execution_only and args.waves != 1:
        parser.error("--waves requires --execution-only")
    report = asyncio.run(
        run(
            args.full,
            execution_only=args.execution_only,
            execution_concurrency=args.concurrency,
            execution_waves=args.waves,
        )
    )
    serialized = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    if args.execution_only:
        limit = EXECUTION_CREATE_P95_BUDGET_MS[str(args.concurrency)]
        print(f"performance budget passed: execution p95 c{args.concurrency} <{limit}ms")
    else:
        print(
            "performance budget passed: execution p95 c1/c10/c50 <500/1500/15000ms, "
            "pagination p95 <300ms, SSE replay contiguous"
        )


if __name__ == "__main__":
    main()
