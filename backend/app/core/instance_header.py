"""Expose the serving API replica identity for operations and fault drills."""

from __future__ import annotations

from typing import Any


class InstanceHeaderMiddleware:
    def __init__(self, app: Any, *, instance_id: str) -> None:
        self.app = app
        self.value = instance_id.encode("ascii", errors="replace")

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        async def send_with_instance(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-agentcanvas-instance", self.value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_instance)


__all__ = ["InstanceHeaderMiddleware"]
