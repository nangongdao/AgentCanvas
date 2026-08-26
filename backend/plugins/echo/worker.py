"""Reference plugin worker implementing the AgentCanvas 1.0 JSONL contract."""

from __future__ import annotations

import json
import sys


def main() -> None:
    line = sys.stdin.readline()
    request = json.loads(line)
    config = request.get("config") or {}
    message = str(config.get("message") or "")
    if config.get("include_input"):
        inputs = request.get("inputs") or {}
        user_query = inputs.get("user_query")
        if user_query is not None:
            message = f"{message}: {user_query}"
    response = {
        "protocol_version": "1.0",
        "request_id": request.get("request_id"),
        "ok": True,
        "output": {"text": message},
        "events": [{"kind": "echo.completed", "data": {"length": len(message)}}],
    }
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
