"""Public-HTTP smoke test for the production Compose stack.

Run after ``docker compose ... up --wait``. The script intentionally uses no
application imports so it exercises the same network/API boundary as an
operator or browser.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any


class SmokeFailure(RuntimeError):
    """Raised when a public container contract is not satisfied."""


class Client:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        content_type: str = "application/json",
        timeout: float = 30,
    ) -> tuple[Any, dict[str, str]]:
        body: bytes | None
        if payload is None:
            body = None
        elif isinstance(payload, bytes):
            body = payload
        else:
            body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": content_type,
            },
        )
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SmokeFailure(f"{method} {path} -> {exc.code}: {detail}") from exc
        with response:
            raw = response.read()
            headers = dict(response.headers.items())
        if not raw:
            return None, headers
        return json.loads(raw), headers

    def stream(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        on_open: Callable[[], None] | None = None,
        on_headers: Callable[[dict[str, str]], None] | None = None,
    ) -> list[dict[str, Any]]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Connection": "close",
            },
        )
        events: list[dict[str, Any]] = []
        try:
            with self.opener.open(request, timeout=45) as response:
                if on_headers is not None:
                    on_headers(dict(response.headers.items()))
                if on_open is not None:
                    on_open()
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line.startswith("data:"):
                        events.append(json.loads(line[5:].strip()))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SmokeFailure(f"{method} {path} -> {exc.code}: {detail}") from exc
        return events


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def _wait_for(
    client: Client,
    path: str,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout: float = 30,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        payload, _ = client.request("GET", path)
        if isinstance(payload, dict):
            last = payload
            if predicate(payload):
                return payload
        time.sleep(0.25)
    raise SmokeFailure(f"timed out waiting for {path}; last={last}")


def _human_workflow(name: str) -> dict[str, Any]:
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
                    "id": "approval",
                    "type": "human",
                    "position": {"x": 300, "y": 0},
                    "config": {
                        "title": "Release approval",
                        "instruction": "Approve the release.",
                    },
                },
                {"id": "end", "type": "end", "position": {"x": 600, "y": 0}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "approval"},
                {"id": "e2", "source": "approval", "target": "end"},
            ],
        },
    }


def run(base_url: str, token: str) -> None:
    client = Client(base_url, token)
    ready, _ = client.request("GET", "/readyz")
    _require(isinstance(ready, dict) and ready.get("status") == "ready", "readyz failed")

    login, _ = client.request("POST", "/api/auth/login", {"token": token})
    _require(isinstance(login, dict) and login.get("role") == "admin", "admin login failed")

    workflows, _ = client.request("GET", "/api/workflows?search=Demo&limit=20")
    _require(isinstance(workflows, dict) and len(workflows.get("items", [])) >= 3, "seeds missing")
    execution, _ = client.request(
        "POST", "/api/workflows/demo-linear/run", {"inputs": {"user_query": "container smoke"}}
    )
    _require(isinstance(execution, dict), "seed execution did not start")
    events = client.stream("GET", f"/api/executions/{execution['id']}/events")
    _require(any(event.get("event_type") == "workflow_finished" for event in events), "SSE terminal missing")

    mcp, _ = client.request("POST", "/api/mcp/servers/demo-calculator/test")
    _require(
        isinstance(mcp, dict)
        and any(tool.get("name") == "eval_expr" for tool in mcp.get("tools", [])),
        "Calculator MCP discovery failed",
    )

    suffix = uuid.uuid4().hex[:10]
    kb, _ = client.request(
        "POST",
        "/api/knowledge-bases",
        {"name": f"Container smoke {suffix}", "chunk_size": 256, "chunk_overlap": 32},
    )
    _require(isinstance(kb, dict), "knowledge base creation failed")
    boundary = f"agentcanvas-{suffix}"
    document_text = b"Container recovery uses checksummed backups and readiness gates."
    multipart = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="smoke.md"\r\n'
        "Content-Type: text/markdown\r\n\r\n"
    ).encode() + document_text + f"\r\n--{boundary}--\r\n".encode()
    document, _ = client.request(
        "POST",
        f"/api/knowledge-bases/{kb['id']}/documents",
        multipart,
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    _require(isinstance(document, dict), "document upload failed")
    client.request(
        "POST", f"/api/knowledge-bases/{kb['id']}/documents/{document['id']}/ingest"
    )
    ingest = _wait_for(
        client,
        f"/api/knowledge-bases/{kb['id']}/documents/{document['id']}/ingest",
        lambda body: body.get("document", {}).get("status") in {"ready", "failed"},
    )
    _require(ingest.get("document", {}).get("status") == "ready", "RAG ingest failed")
    retrieval, _ = client.request(
        "POST",
        f"/api/knowledge-bases/{kb['id']}/retrieve",
        {"query": "checksummed backups", "top_k": 3, "score_threshold": 0},
    )
    _require(
        isinstance(retrieval, dict) and bool(retrieval.get("hits")),
        "RAG retrieval failed",
    )

    human, _ = client.request("POST", "/api/workflows", _human_workflow(f"Human {suffix}"))
    _require(isinstance(human, dict), "human workflow creation failed")
    waiting, _ = client.request("POST", f"/api/workflows/{human['id']}/run", {"inputs": {}})
    _require(isinstance(waiting, dict), "human execution did not start")
    _wait_for(
        client,
        f"/api/executions/{waiting['id']}",
        lambda body: body.get("status") == "waiting_approval",
    )
    client.request(
        "POST",
        f"/api/executions/{waiting['id']}/resume",
        {"decision": {"approved": True}},
    )
    resumed = _wait_for(
        client,
        f"/api/executions/{waiting['id']}",
        lambda body: body.get("status") in {"succeeded", "failed"},
    )
    _require(resumed.get("status") == "succeeded", "human resume failed")

    chat, _ = client.request(
        "POST",
        "/api/chat/sessions",
        {"title": f"Smoke {suffix}", "workflow_id": "demo-linear"},
    )
    _require(isinstance(chat, dict), "chat session creation failed")
    chat_events = client.stream(
        "POST", f"/api/chat/sessions/{chat['id']}/send", {"message": "hello"}
    )
    _require(bool(chat_events), "Chat SSE stream was empty")
    messages, _ = client.request(
        "GET", f"/api/chat/sessions/{chat['id']}/messages?order=asc&limit=20"
    )
    _require(isinstance(messages, dict) and len(messages.get("items", [])) >= 2, "Chat persistence failed")

    print("container smoke passed: auth, seeds/SSE, MCP, RAG, human resume, Chat")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    run(args.base_url, args.token)


if __name__ == "__main__":
    main()
