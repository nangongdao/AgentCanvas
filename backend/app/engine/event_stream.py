"""Redis Streams helpers for durable execution event tails (I1 Phase 5)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

STREAM_PREFIX = "agentcanvas:exec-events:"
GLOBAL_RELAY_CURSOR = "execution-events"
DEFAULT_STREAM_MAXLEN = 10_000


def execution_stream_key(execution_id: str) -> str:
    """Return the Redis Stream key for one execution."""
    return f"{STREAM_PREFIX}{execution_id}"


@dataclass(frozen=True)
class StreamEvent:
    """Normalized event envelope shared by Redis tails and SSE."""

    execution_id: str
    seq: int
    event_type: str
    node_id: str | None
    ts: str
    payload: dict[str, Any]
    event_row_id: int | None = None

    def sse_payload(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "seq": self.seq,
            "event_type": self.event_type,
            "node_id": self.node_id,
            "ts": self.ts,
            "payload": self.payload,
        }


def encode_stream_fields(
    *,
    execution_id: str,
    seq: int,
    event_type: str,
    node_id: str | None,
    ts: datetime | str,
    payload: dict[str, Any] | None,
    event_row_id: int,
) -> dict[str, str]:
    """Encode an execution_events row for Redis XADD."""
    ts_text = ts.isoformat() if isinstance(ts, datetime) else str(ts)
    return {
        "execution_id": execution_id,
        "seq": str(int(seq)),
        "event_type": event_type,
        "node_id": node_id or "",
        "ts": ts_text,
        "payload": json.dumps(payload or {}, ensure_ascii=False),
        "event_row_id": str(int(event_row_id)),
    }


def decode_stream_fields(fields: dict[str, Any]) -> StreamEvent | None:
    """Decode a Redis stream entry; skip malformed payloads."""
    try:
        payload_raw = fields.get("payload") or "{}"
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else dict(payload_raw)
        if not isinstance(payload, dict):
            payload = {}
        event_row_raw = fields.get("event_row_id")
        event_row_id = int(event_row_raw) if event_row_raw not in (None, "") else None
        node_id = fields.get("node_id") or None
        if node_id == "":
            node_id = None
        return StreamEvent(
            execution_id=str(fields["execution_id"]),
            seq=int(fields["seq"]),
            event_type=str(fields["event_type"]),
            node_id=str(node_id) if node_id is not None else None,
            ts=str(fields.get("ts") or ""),
            payload=payload,
            event_row_id=event_row_id,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        logger.debug("skip malformed execution stream entry", exc_info=True)
        return None


class ExecutionEventStream:
    """Thin Redis Streams adapter used by the relay and SSE tails."""

    def __init__(
        self,
        client: Any,
        *,
        maxlen: int = DEFAULT_STREAM_MAXLEN,
    ) -> None:
        self.client = client
        self.maxlen = max(1, maxlen)

    async def publish(self, event: StreamEvent | dict[str, Any]) -> str:
        if isinstance(event, StreamEvent):
            fields = encode_stream_fields(
                execution_id=event.execution_id,
                seq=event.seq,
                event_type=event.event_type,
                node_id=event.node_id,
                ts=event.ts,
                payload=event.payload,
                event_row_id=event.event_row_id or 0,
            )
            execution_id = event.execution_id
        else:
            fields = encode_stream_fields(**event)
            execution_id = str(event["execution_id"])
        return str(
            await self.client.xadd(
                execution_stream_key(execution_id),
                fields,
                maxlen=self.maxlen,
                approximate=True,
            )
        )

    async def read_after_seq(
        self,
        execution_id: str,
        after_seq: int,
        *,
        count: int = 100,
        block_ms: int | None = None,
    ) -> list[StreamEvent]:
        """Read stream entries and keep only those with seq > after_seq.

        Redis stream IDs are not execution sequence numbers, so consumers always
        filter by the durable ``seq`` field carried in each entry.
        """
        key = execution_stream_key(execution_id)
        kwargs: dict[str, Any] = {
            "streams": {key: "0-0"},
            "count": max(1, count),
        }
        if block_ms is not None:
            kwargs["block"] = max(0, int(block_ms))
        raw = await self.client.xread(**kwargs)
        return self._decode_filtered(raw, execution_id=execution_id, after_seq=after_seq)

    async def tail(
        self,
        execution_id: str,
        last_redis_id: str,
        *,
        count: int = 100,
        block_ms: int = 1_000,
    ) -> tuple[str, list[StreamEvent]]:
        """Block-read new entries after a Redis stream ID."""
        key = execution_stream_key(execution_id)
        raw = await self.client.xread(
            streams={key: last_redis_id or "$"},
            count=max(1, count),
            block=max(0, int(block_ms)),
        )
        events: list[StreamEvent] = []
        newest_id = last_redis_id or "$"
        if not raw:
            return newest_id, events
        for _stream_name, entries in raw:
            for entry_id, fields in entries:
                newest_id = str(entry_id)
                decoded = decode_stream_fields(fields)
                if decoded is None:
                    continue
                if decoded.execution_id != execution_id:
                    continue
                events.append(decoded)
        return newest_id, events

    def _decode_filtered(
        self,
        raw: Any,
        *,
        execution_id: str,
        after_seq: int,
    ) -> list[StreamEvent]:
        events: list[StreamEvent] = []
        if not raw:
            return events
        for _stream_name, entries in raw:
            for _entry_id, fields in entries:
                decoded = decode_stream_fields(fields)
                if decoded is None:
                    continue
                if decoded.execution_id != execution_id:
                    continue
                if decoded.seq <= after_seq:
                    continue
                events.append(decoded)
        events.sort(key=lambda item: item.seq)
        return events


async def connect_event_stream_client(redis_url: str) -> Any | None:
    """Create a Redis client for event streams, or None when unavailable."""
    if not redis_url:
        return None
    try:
        from redis.asyncio import Redis
    except ImportError as exc:
        logger.warning("redis package unavailable for event stream (%s)", exc)
        return None
    # RESP2 keeps the documented Redis 5+ deployment floor compatible with
    # redis-py 8, whose automatic negotiation otherwise sends Redis 6's HELLO.
    client = Redis.from_url(redis_url, decode_responses=True, protocol=2)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001 — degrade to PG polling
        logger.warning("redis event stream ping failed (%s); SSE will poll PostgreSQL", exc)
        close = getattr(client, "aclose", None) or getattr(client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result
        return None
    return client


async def close_redis_client(client: Any | None) -> None:
    if client is None:
        return
    close = getattr(client, "aclose", None) or getattr(client, "close", None)
    if close is None:
        return
    try:
        result = close()
        if hasattr(result, "__await__"):
            await result
    except Exception:  # noqa: BLE001 — best-effort cleanup
        logger.debug("ignoring error closing event-stream redis client", exc_info=True)


__all__ = [
    "DEFAULT_STREAM_MAXLEN",
    "GLOBAL_RELAY_CURSOR",
    "ExecutionEventStream",
    "STREAM_PREFIX",
    "StreamEvent",
    "close_redis_client",
    "connect_event_stream_client",
    "decode_stream_fields",
    "encode_stream_fields",
    "execution_stream_key",
]
