"""Pure contracts for the public horizontal-topology smoke helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.engine.execution_scheduler import workflow_replay_block_reason
from app.schemas.dsl import WorkflowDSL
from scripts.distributed_smoke import (
    SmokeFailure,
    _collect_sse_replicas,
    _slow_ambiguous_workflow,
    _slow_safe_workflow,
    _validate_event_stream,
    _validate_retrieval,
)


def _events(*event_types: str) -> list[dict[str, object]]:
    return [
        {"seq": index, "event_type": event_type}
        for index, event_type in enumerate(event_types, start=1)
    ]


def test_slow_safe_smoke_workflow_is_replayable() -> None:
    workflow = _slow_safe_workflow("safe")
    dsl = WorkflowDSL.model_validate(workflow["dsl"])
    assert len([node for node in dsl.nodes if node.type == "agent"]) == 4
    assert workflow_replay_block_reason(dsl) is None


def test_slow_ambiguous_smoke_workflow_blocks_replay() -> None:
    workflow = _slow_ambiguous_workflow("ambiguous")
    dsl = WorkflowDSL.model_validate(workflow["dsl"])
    assert "cannot be replayed" in str(workflow_replay_block_reason(dsl))


def test_stream_validator_rejects_duplicate_sequences() -> None:
    events = _events("workflow_started", "workflow_started", "workflow_finished")
    events[1]["seq"] = events[0]["seq"]
    with pytest.raises(SmokeFailure, match="not unique"):
        _validate_event_stream(events)


def test_stream_validator_accepts_one_terminal_event() -> None:
    result = _validate_event_stream(_events("workflow_started", "workflow_finished"))
    assert result == {"event_count": 2, "terminal_seq": 2}


def test_stream_validator_accepts_expected_failure_terminal() -> None:
    result = _validate_event_stream(
        _events("workflow_started", "workflow_failed"),
        expected_terminal="workflow_failed",
    )
    assert result["terminal_seq"] == 2


def test_sse_smoke_requires_every_discovered_api_replica() -> None:
    class StreamClient:
        def __init__(self) -> None:
            self.instances = iter(("api-a", "api-b"))

        def stream(
            self,
            _method: str,
            _path: str,
            _payload: Any = None,
            *,
            on_open: Callable[[], None] | None = None,
            on_headers: Callable[[dict[str, str]], None] | None = None,
        ) -> list[dict[str, Any]]:
            assert on_open is None
            assert on_headers is not None
            on_headers({"X-AgentCanvas-Instance": next(self.instances)})
            return _events("workflow_started", "workflow_finished")

    assert _collect_sse_replicas(
        StreamClient(),
        "execution-1",
        {"api-a", "api-b"},
        timeout=1,
    ) == {"api-a", "api-b"}


def test_sse_smoke_rejects_subscription_from_only_one_replica() -> None:
    class StreamClient:
        def stream(
            self,
            _method: str,
            _path: str,
            _payload: Any = None,
            *,
            on_open: Callable[[], None] | None = None,
            on_headers: Callable[[dict[str, str]], None] | None = None,
        ) -> list[dict[str, Any]]:
            assert on_open is None
            assert on_headers is not None
            on_headers({"X-AgentCanvas-Instance": "api-a"})
            return _events("workflow_started", "workflow_finished")

    with pytest.raises(SmokeFailure, match="api-b"):
        _collect_sse_replicas(
            StreamClient(),
            "execution-1",
            {"api-a", "api-b"},
            timeout=0.01,
        )


def test_restore_retrieval_requires_the_expected_document_to_rank_first() -> None:
    payload = {
        "hits": [
            {"document_id": "target", "text": "quiesced sapphire checksum", "score": 1.0},
            {"document_id": "decoy", "text": "weather forecast", "score": 0.0},
        ]
    }
    top = _validate_retrieval(
        payload,
        expected_document_id="target",
        expected_text="sapphire checksum",
    )
    assert top["score"] == 1.0


def test_restore_retrieval_rejects_changed_ranking() -> None:
    payload = {
        "hits": [
            {"document_id": "decoy", "text": "weather forecast", "score": 0.9},
            {"document_id": "target", "text": "quiesced sapphire checksum", "score": 0.8},
        ]
    }
    with pytest.raises(SmokeFailure, match="ranking changed"):
        _validate_retrieval(
            payload,
            expected_document_id="target",
            expected_text="sapphire checksum",
        )
