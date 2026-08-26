"""Public-boundary smoke for the horizontal API/worker topology."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.container_smoke import Client, SmokeFailure, _human_workflow, _require, _wait_for


class _StreamClient(Protocol):
    def stream(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        on_open: Callable[[], None] | None = None,
        on_headers: Callable[[dict[str, str]], None] | None = None,
    ) -> list[dict[str, Any]]: ...


def _instance(headers: dict[str, str]) -> str:
    for name, value in headers.items():
        if name.lower() == "x-agentcanvas-instance":
            return value
    raise SmokeFailure("response is missing X-AgentCanvas-Instance")


def _ready_instance(base_url: str) -> str:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/readyz",
        headers={"Connection": "close"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read())
        _require(payload.get("status") == "ready", f"replica not ready: {payload}")
        return str(payload["instance_id"])


def _collect_api_replicas(base_url: str, *, expected: int = 2) -> set[str]:
    instances: set[str] = set()
    deadline = time.monotonic() + 20
    while len(instances) < expected and time.monotonic() < deadline:
        instances.add(_ready_instance(base_url))
        time.sleep(0.1)
    _require(len(instances) >= expected, f"expected {expected} API replicas, saw {instances}")
    return instances


def _safe_workflow(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "dsl": {
            "version": "1.0",
            "name": name,
            "variables": [],
            "settings": {
                "max_loop_iterations": 20,
                "timeout_seconds": 30,
                "recursion_limit": 50,
            },
            "nodes": [
                {"id": "start", "type": "start", "position": {"x": 0, "y": 0}},
                {
                    "id": "end",
                    "type": "end",
                    "position": {"x": 300, "y": 0},
                    "config": {"output_template": {"ok": True}},
                },
            ],
            "edges": [{"id": "e1", "source": "start", "target": "end"}],
        },
    }


def _slow_safe_workflow(name: str) -> dict[str, Any]:
    """Build a replay-safe execution long enough for container fault injection."""
    agent_ids = [f"agent-{index}" for index in range(1, 5)]
    node_ids = ["start", *agent_ids, "end"]
    return {
        "name": name,
        "dsl": {
            "version": "1.0",
            "name": name,
            "variables": [],
            "settings": {
                "max_loop_iterations": 20,
                "timeout_seconds": 90,
                "recursion_limit": 50,
            },
            "nodes": [
                {"id": "start", "type": "start", "position": {"x": 0, "y": 0}},
                *[
                    {
                        "id": node_id,
                        "type": "agent",
                        "position": {"x": index * 240, "y": 0},
                        "config": {
                            "model_config_id": "default",
                            "tools": [],
                            "memory": {"enabled": False, "window": 10},
                        },
                    }
                    for index, node_id in enumerate(agent_ids, start=1)
                ],
                {"id": "end", "type": "end", "position": {"x": 1200, "y": 0}},
            ],
            "edges": [
                {
                    "id": f"slow-edge-{index}",
                    "source": source,
                    "target": target,
                }
                for index, (source, target) in enumerate(
                    zip(node_ids[:-1], node_ids[1:], strict=True), start=1
                )
            ],
        },
    }


def _slow_ambiguous_workflow(name: str) -> dict[str, Any]:
    """Build a long workflow whose memory write makes crash replay unsafe."""
    workflow = _slow_safe_workflow(name)
    nodes = workflow["dsl"]["nodes"]
    first_agent = next(node for node in nodes if node["type"] == "agent")
    first_agent["config"]["memory"] = {"enabled": True, "window": 10}
    return workflow


def _create_execution(client: Client, *, prefix: str) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:10]
    workflow, create_headers = client.request(
        "POST", "/api/workflows", _safe_workflow(f"{prefix} {suffix}")
    )
    _require(isinstance(workflow, dict), "safe workflow creation failed")
    execution, _headers = client.request(
        "POST", f"/api/workflows/{workflow['id']}/run", {"inputs": {}}
    )
    _require(isinstance(execution, dict), "safe execution enqueue failed")
    return str(execution["id"]), _instance(create_headers)


def _create_slow_execution(
    client: Client,
    *,
    prefix: str,
    side_effecting: bool = False,
) -> str:
    suffix = uuid.uuid4().hex[:10]
    name = f"{prefix} {suffix}"
    workflow_payload = (
        _slow_ambiguous_workflow(name) if side_effecting else _slow_safe_workflow(name)
    )
    workflow, _headers = client.request(
        "POST", "/api/workflows", workflow_payload
    )
    _require(isinstance(workflow, dict), "slow workflow creation failed")
    execution, _headers = client.request(
        "POST",
        f"/api/workflows/{workflow['id']}/run",
        {"inputs": {"user_query": "distributed fault injection"}},
    )
    _require(isinstance(execution, dict), "slow execution enqueue failed")
    return str(execution["id"])


def _validate_event_stream(
    events: list[dict[str, Any]],
    *,
    expected_terminal: str = "workflow_finished",
) -> dict[str, Any]:
    _require(bool(events), "SSE stream returned no events")
    sequences = [int(event["seq"]) for event in events]
    _require(sequences == sorted(set(sequences)), f"SSE sequence is not unique: {sequences}")
    terminal = [
        event
        for event in events
        if event.get("event_type")
        in {"workflow_finished", "workflow_failed", "workflow_cancelled"}
    ]
    _require(len(terminal) == 1, f"expected one terminal SSE event, saw {terminal}")
    _require(
        terminal[0].get("event_type") == expected_terminal,
        f"slow execution terminal mismatch: {terminal[0]}",
    )
    return {"event_count": len(events), "terminal_seq": sequences[-1]}


def _wait_cross_api(client: Client, execution_id: str, origin: str) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    last: dict[str, Any] = {}
    cross_instance = False
    while time.monotonic() < deadline:
        payload, headers = client.request("GET", f"/api/executions/{execution_id}")
        if isinstance(payload, dict):
            last = payload
        cross_instance = cross_instance or _instance(headers) != origin
        if last.get("status") in {"succeeded", "failed", "cancelled"} and cross_instance:
            return last
        time.sleep(0.15)
    raise SmokeFailure(
        f"execution did not terminate through a peer API; origin={origin} last={last}"
    )


def _collect_sse_replicas(
    client: _StreamClient,
    execution_id: str,
    expected_instances: set[str],
    *,
    timeout: float = 20,
) -> set[str]:
    instances: set[str] = set()
    deadline = time.monotonic() + timeout
    while not expected_instances.issubset(instances) and time.monotonic() < deadline:
        stream_instance = ""

        def capture(headers: dict[str, str]) -> None:
            nonlocal stream_instance
            stream_instance = _instance(headers)

        events = client.stream(
            "GET",
            f"/api/executions/{execution_id}/events",
            on_headers=capture,
        )
        _validate_event_stream(events)
        instances.add(stream_instance)
        time.sleep(0.1)
    missing = expected_instances - instances
    _require(not missing, f"SSE did not reach API replicas {sorted(missing)}; saw {sorted(instances)}")
    return instances


def topology(base_url: str, token: str) -> dict[str, Any]:
    replicas = _collect_api_replicas(base_url)
    client = Client(base_url, token)
    execution_id, origin = _create_execution(client, prefix="Distributed smoke")
    terminal = _wait_cross_api(client, execution_id, origin)
    _require(terminal.get("status") == "succeeded", f"safe execution failed: {terminal}")
    sse_replicas = _collect_sse_replicas(client, execution_id, replicas)
    return {
        "api_instances": sorted(replicas),
        "sse_instances": sorted(sse_replicas),
        "execution_id": execution_id,
    }


def enqueue(base_url: str, token: str, state_path: Path) -> None:
    client = Client(base_url, token)
    execution_id, _origin = _create_execution(client, prefix="Queued while workers down")
    time.sleep(1)
    queued, _ = client.request("GET", f"/api/executions/{execution_id}")
    _require(isinstance(queued, dict) and queued.get("status") == "queued", str(queued))
    state_path.write_text(json.dumps({"execution_id": execution_id}), encoding="utf-8")


def wait_queued(base_url: str, token: str, state_path: Path) -> None:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    client = Client(base_url, token)
    terminal = _wait_for(
        client,
        f"/api/executions/{state['execution_id']}",
        lambda body: body.get("status") in {"succeeded", "failed"},
    )
    _require(terminal.get("status") == "succeeded", f"restarted worker failed: {terminal}")


def prepare_slow(base_url: str, token: str, state_path: Path) -> None:
    client = Client(base_url, token)
    execution_id = _create_slow_execution(client, prefix="Fault injection")
    running = _wait_for(
        client,
        f"/api/executions/{execution_id}",
        lambda body: body.get("status") == "running",
    )
    _require(running.get("status") == "running", str(running))
    state_path.write_text(json.dumps({"execution_id": execution_id}), encoding="utf-8")


def prepare_ambiguous(base_url: str, token: str, state_path: Path) -> None:
    client = Client(base_url, token)
    execution_id = _create_slow_execution(
        client,
        prefix="Ambiguous side effect",
        side_effecting=True,
    )
    running = _wait_for(
        client,
        f"/api/executions/{execution_id}",
        lambda body: body.get("status") == "running",
    )
    _require(running.get("status") == "running", str(running))
    state_path.write_text(json.dumps({"execution_id": execution_id}), encoding="utf-8")


def wait_stream(base_url: str, token: str, state_path: Path) -> dict[str, Any]:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    execution_id = str(state["execution_id"])
    events = Client(base_url, token).stream("GET", f"/api/executions/{execution_id}/events")
    return {"execution_id": execution_id, **_validate_event_stream(events)}


def wait_dead_letter(base_url: str, token: str, state_path: Path) -> dict[str, Any]:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    execution_id = str(state["execution_id"])
    client = Client(base_url, token)
    failed = _wait_for(
        client,
        f"/api/executions/{execution_id}",
        lambda body: body.get("status") == "failed",
        timeout=60,
    )
    _require(
        "cannot be replayed" in str(failed.get("error")),
        f"unexpected dead letter: {failed}",
    )
    events = client.stream("GET", f"/api/executions/{execution_id}/events")
    return {
        "execution_id": execution_id,
        **_validate_event_stream(events, expected_terminal="workflow_failed"),
    }


def stream_during_redis_loss(base_url: str, token: str, state_path: Path) -> dict[str, Any]:
    client = Client(base_url, token)
    execution_id = _create_slow_execution(client, prefix="Redis live fallback")

    def mark_open() -> None:
        state_path.write_text(json.dumps({"execution_id": execution_id}), encoding="utf-8")

    events = client.stream(
        "GET",
        f"/api/executions/{execution_id}/events",
        on_open=mark_open,
    )
    return {"execution_id": execution_id, **_validate_event_stream(events)}


def prepare_human(base_url: str, token: str, state_path: Path) -> None:
    client = Client(base_url, token)
    suffix = uuid.uuid4().hex[:10]
    workflow, _ = client.request(
        "POST", "/api/workflows", _human_workflow(f"Restart approval {suffix}")
    )
    execution, _ = client.request("POST", f"/api/workflows/{workflow['id']}/run", {"inputs": {}})
    waiting = _wait_for(
        client,
        f"/api/executions/{execution['id']}",
        lambda body: body.get("status") == "waiting_approval",
    )
    _require(waiting.get("status") == "waiting_approval", str(waiting))
    state_path.write_text(json.dumps({"execution_id": execution["id"]}), encoding="utf-8")


def resume_human(base_url: str, token: str, state_path: Path) -> None:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    client = Client(base_url, token)
    execution_id = state["execution_id"]
    persisted, _ = client.request("GET", f"/api/executions/{execution_id}")
    _require(persisted.get("status") == "waiting_approval", str(persisted))
    client.request(
        "POST",
        f"/api/executions/{execution_id}/resume",
        {"decision": {"approved": True}},
    )
    terminal = _wait_for(
        client,
        f"/api/executions/{execution_id}",
        lambda body: body.get("status") in {"succeeded", "failed"},
    )
    _require(terminal.get("status") == "succeeded", f"resume failed: {terminal}")


def _upload_document(
    client: Client,
    kb_id: str,
    *,
    filename: str,
    content: str,
) -> dict[str, Any]:
    boundary = f"agentcanvas-{uuid.uuid4().hex}"
    multipart = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: text/markdown\r\n\r\n"
    ).encode() + content.encode() + f"\r\n--{boundary}--\r\n".encode()
    document, _ = client.request(
        "POST",
        f"/api/knowledge-bases/{kb_id}/documents",
        multipart,
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    _require(isinstance(document, dict), f"document upload failed: {filename}")
    client.request(
        "POST",
        f"/api/knowledge-bases/{kb_id}/documents/{document['id']}/ingest",
    )
    ingest = _wait_for(
        client,
        f"/api/knowledge-bases/{kb_id}/documents/{document['id']}/ingest",
        lambda body: body.get("document", {}).get("status") in {"ready", "failed"},
    )
    _require(
        ingest.get("document", {}).get("status") == "ready",
        f"document ingest failed: {filename}: {ingest}",
    )
    return document


def _validate_retrieval(
    payload: Any,
    *,
    expected_document_id: str,
    expected_text: str,
) -> dict[str, Any]:
    _require(isinstance(payload, dict), f"invalid retrieval response: {payload}")
    hits = payload.get("hits")
    _require(isinstance(hits, list) and bool(hits), "restored retrieval returned no hits")
    top = hits[0]
    _require(isinstance(top, dict), f"invalid retrieval hit: {top}")
    _require(
        top.get("document_id") == expected_document_id,
        f"restored retrieval ranking changed: expected {expected_document_id}, saw {top}",
    )
    _require(expected_text in str(top.get("text", "")), f"restored hit content changed: {top}")
    return top


def prepare_rag(base_url: str, token: str, state_path: Path) -> None:
    client = Client(base_url, token)
    suffix = uuid.uuid4().hex[:10]
    kb, _ = client.request(
        "POST",
        "/api/knowledge-bases",
        {"name": f"Restore ranking {suffix}", "chunk_size": 256, "chunk_overlap": 32},
    )
    _require(isinstance(kb, dict), "restore knowledge base creation failed")
    expected_text = f"quiesced sapphire checksum {suffix} preserves approval checkpoints"
    target = _upload_document(
        client,
        str(kb["id"]),
        filename="restore-target.md",
        content=expected_text,
    )
    _upload_document(
        client,
        str(kb["id"]),
        filename="restore-decoy.md",
        content="weather forecasts use pressure temperature humidity and wind speed",
    )
    query = f"quiesced sapphire checksum {suffix} approval checkpoints"
    retrieval, _ = client.request(
        "POST",
        f"/api/knowledge-bases/{kb['id']}/retrieve",
        {"query": query, "top_k": 2, "score_threshold": 0},
    )
    _validate_retrieval(
        retrieval,
        expected_document_id=str(target["id"]),
        expected_text=expected_text,
    )
    state_path.write_text(
        json.dumps(
            {
                "kb_id": kb["id"],
                "query": query,
                "expected_document_id": target["id"],
                "expected_text": expected_text,
            }
        ),
        encoding="utf-8",
    )


def verify_rag(base_url: str, token: str, state_path: Path) -> dict[str, Any]:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    retrieval, _ = Client(base_url, token).request(
        "POST",
        f"/api/knowledge-bases/{state['kb_id']}/retrieve",
        {"query": state["query"], "top_k": 2, "score_threshold": 0},
    )
    top = _validate_retrieval(
        retrieval,
        expected_document_id=str(state["expected_document_id"]),
        expected_text=str(state["expected_text"]),
    )
    return {
        "knowledge_base_id": state["kb_id"],
        "top_document_id": top["document_id"],
        "top_score": top["score"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "topology",
            "enqueue",
            "wait-queued",
            "prepare-slow",
            "prepare-ambiguous",
            "wait-stream",
            "wait-dead-letter",
            "stream-redis-loss",
            "prepare-human",
            "resume-human",
            "prepare-rag",
            "verify-rag",
        ),
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--token", required=True)
    parser.add_argument("--state", type=Path, default=Path("distributed-smoke-state.json"))
    args = parser.parse_args()
    if args.command == "topology":
        print(json.dumps(topology(args.base_url, args.token), sort_keys=True))
    elif args.command == "enqueue":
        enqueue(args.base_url, args.token, args.state)
    elif args.command == "wait-queued":
        wait_queued(args.base_url, args.token, args.state)
    elif args.command == "prepare-slow":
        prepare_slow(args.base_url, args.token, args.state)
    elif args.command == "prepare-ambiguous":
        prepare_ambiguous(args.base_url, args.token, args.state)
    elif args.command == "wait-stream":
        print(json.dumps(wait_stream(args.base_url, args.token, args.state), sort_keys=True))
    elif args.command == "wait-dead-letter":
        print(json.dumps(wait_dead_letter(args.base_url, args.token, args.state), sort_keys=True))
    elif args.command == "stream-redis-loss":
        print(
            json.dumps(
                stream_during_redis_loss(args.base_url, args.token, args.state),
                sort_keys=True,
            )
        )
    elif args.command == "prepare-human":
        prepare_human(args.base_url, args.token, args.state)
    elif args.command == "resume-human":
        resume_human(args.base_url, args.token, args.state)
    elif args.command == "prepare-rag":
        prepare_rag(args.base_url, args.token, args.state)
    else:
        print(json.dumps(verify_rag(args.base_url, args.token, args.state), sort_keys=True))


if __name__ == "__main__":
    main()
