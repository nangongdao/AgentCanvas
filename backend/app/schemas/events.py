"""Execution event protocol for SSE and persistence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal


class EventType(StrEnum):
    WORKFLOW_STARTED = "workflow_started"
    NODE_STARTED = "node_started"
    NODE_STREAMING = "node_streaming"
    NODE_FINISHED = "node_finished"
    NODE_FAILED = "node_failed"
    EDGE_TAKEN = "edge_taken"
    WORKFLOW_INTERRUPTED = "workflow_interrupted"
    WORKFLOW_FINISHED = "workflow_finished"
    WORKFLOW_FAILED = "workflow_failed"
    WORKFLOW_CANCELLED = "workflow_cancelled"


TERMINAL_EVENTS = frozenset(
    {
        EventType.WORKFLOW_FINISHED,
        EventType.WORKFLOW_FAILED,
        EventType.WORKFLOW_CANCELLED,
    }
)


@dataclass(frozen=True)
class ExecutionEvent:
    execution_id: str
    event_type: EventType
    schema_version: Literal["1.0"] = "1.0"
    seq: int = 0
    node_id: str | None = None
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["event_type"] = self.event_type.value
        return data

    def sse_data(self) -> dict[str, Any]:
        return self.to_dict()
