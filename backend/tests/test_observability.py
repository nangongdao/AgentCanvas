"""U4 request correlation, redaction, telemetry, and scrape contracts."""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.logging import ContextFilter, JsonFormatter, bind_log_context, redact_value
from app.core.observability import Observability, RequestObservabilityMiddleware
from app.schemas.events import EventType, ExecutionEvent


def test_central_redaction_masks_structured_and_inline_credentials() -> None:
    value = {
        "api_key": "sk-live-secret",
        "nested": {"password": "hunter2", "safe": "visible"},
        "message": (
            "Authorization: Bearer abc.def.ghi "
            "redis://alice:password@example.test/0?token=raw-token"
        ),
    }

    redacted = redact_value(value)

    serialized = json.dumps(redacted)
    assert "sk-live-secret" not in serialized
    assert "hunter2" not in serialized
    assert "abc.def.ghi" not in serialized
    assert "raw-token" not in serialized
    assert "password@example" not in serialized
    assert redacted["nested"]["safe"] == "visible"


def test_json_logs_include_correlation_fields_without_secrets() -> None:
    formatter = JsonFormatter()
    context_filter = ContextFilter()
    with bind_log_context(
        request_id="req-1",
        execution_id="exec-1",
        workflow_id="workflow-1",
        node_id="node-1",
        provider="mock",
        error_code="provider_timeout",
    ):
        record = logging.LogRecord(
            "agentcanvas.test",
            logging.ERROR,
            __file__,
            1,
            "call failed with Bearer very.secret.token",
            (),
            None,
        )
        context_filter.filter(record)
        payload = json.loads(formatter.format(record))

    assert payload["request_id"] == "req-1"
    assert payload["execution_id"] == "exec-1"
    assert payload["workflow_id"] == "workflow-1"
    assert payload["node_id"] == "node-1"
    assert payload["provider"] == "mock"
    assert payload["error_code"] == "provider_timeout"
    assert "very.secret.token" not in payload["message"]


def test_request_id_metrics_and_trace_use_route_template() -> None:
    observability = Observability(environment="test")
    exporter = InMemorySpanExporter()
    observability.tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = FastAPI()
    app.add_middleware(RequestObservabilityMiddleware, observability=observability)

    @app.get("/items/{item_id}")
    async def get_item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    trace_id = "0af7651916cd43dd8448eb211c80319c"
    parent_span_id = "b7ad6b7169203331"
    try:
        with TestClient(app) as client:
            response = client.get(
                "/items/private-value",
                headers={
                    "X-Request-ID": "caller-request-1",
                    "traceparent": f"00-{trace_id}-{parent_span_id}-01",
                },
            )
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == "caller-request-1"

        metrics = observability.render_metrics()[0].decode()
        assert "agentcanvas_http_requests_total" in metrics
        assert 'route="/items/{item_id}"' in metrics
        assert 'le="0.5"' in metrics
        assert "private-value" not in metrics

        observability.tracer_provider.force_flush()
        server_span = next(span for span in exporter.get_finished_spans() if span.name.startswith("GET "))
        assert server_span.name == "GET /items/{item_id}"
        assert server_span.parent is not None
        assert server_span.parent.span_id == int(parent_span_id, 16)
        assert server_span.attributes is not None
        assert server_span.attributes["http.route"] == "/items/{item_id}"
    finally:
        observability.shutdown()


def test_execution_events_export_terminal_and_node_metrics() -> None:
    observability = Observability(environment="test")
    try:
        observability.observe_event(
            ExecutionEvent(
                execution_id="exec-1",
                event_type=EventType.WORKFLOW_STARTED,
                payload={"workflow_id": "workflow-1"},
            )
        )
        observability.observe_event(
            ExecutionEvent(
                execution_id="exec-1",
                event_type=EventType.NODE_FINISHED,
                node_id="node-1",
                payload={"elapsed_ms": 25},
            )
        )
        observability.observe_event(
            ExecutionEvent(
                execution_id="exec-1",
                event_type=EventType.WORKFLOW_FINISHED,
            )
        )

        metrics = observability.render_metrics()[0].decode()
        assert "agentcanvas_executions_total" in metrics
        assert 'status="succeeded"' in metrics
        assert "agentcanvas_execution_duration_seconds" in metrics
        assert "agentcanvas_node_duration_seconds" in metrics
        assert "workflow-1" not in metrics
        assert "node-1" not in metrics
    finally:
        observability.shutdown()
