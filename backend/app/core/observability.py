"""OpenTelemetry tracing/metrics and request correlation middleware."""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import propagate
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from opentelemetry.trace import SpanKind, Status, StatusCode
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import __version__
from app.core.logging import bind_log_context
from app.schemas.events import TERMINAL_EVENTS, EventType, ExecutionEvent

logger = logging.getLogger(__name__)

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

_FAST_SECONDS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
_WORKFLOW_SECONDS = (0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)
_QUEUE_DEPTH = (0, 1, 5, 10, 25, 50, 100, 250, 500, 1000)


def _metric_views() -> list[View]:
    views = [
        View(
            instrument_name=name,
            aggregation=ExplicitBucketHistogramAggregation(boundaries=_FAST_SECONDS),
        )
        for name in (
            "agentcanvas.http.duration",
            "agentcanvas.node.duration",
            "agentcanvas.provider.duration",
            "agentcanvas.provider.ttft",
            "agentcanvas.mcp.duration",
            "agentcanvas.retrieval.duration",
        )
    ]
    views.extend(
        [
            View(
                instrument_name="agentcanvas.execution.duration",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=_WORKFLOW_SECONDS),
            ),
            View(
                instrument_name="agentcanvas.ingest.duration",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=_WORKFLOW_SECONDS),
            ),
            View(
                instrument_name="agentcanvas.event.queue_depth",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=_QUEUE_DEPTH),
            ),
        ]
    )
    return views


def _otlp_endpoint(base: str, signal: str) -> str:
    endpoint = base.rstrip("/")
    if endpoint.endswith(f"/v1/{signal}"):
        return endpoint
    return f"{endpoint}/v1/{signal}"


class Observability:
    """Application-owned telemetry providers with an isolated scrape registry."""

    def __init__(
        self,
        *,
        service_name: str = "agentcanvas-backend",
        service_version: str = __version__,
        environment: str = "development",
        otlp_endpoint: str = "",
        trace_sample_ratio: float = 1.0,
    ) -> None:
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.version": service_version,
                "deployment.environment.name": environment,
            }
        )
        self.registry = CollectorRegistry(auto_describe=True)
        prometheus_reader = PrometheusMetricReader(
            disable_target_info=False,
            scope_info_enabled=False,
            registry=self.registry,
        )
        readers: list[Any] = [prometheus_reader]
        if otlp_endpoint:
            readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=_otlp_endpoint(otlp_endpoint, "metrics")),
                    export_interval_millis=30_000,
                )
            )
        self.meter_provider = MeterProvider(
            resource=resource,
            metric_readers=readers,
            views=_metric_views(),
        )
        self.meter = self.meter_provider.get_meter("agentcanvas.backend")

        self.tracer_provider = TracerProvider(
            resource=resource,
            sampler=TraceIdRatioBased(trace_sample_ratio),
        )
        if otlp_endpoint:
            exporter = OTLPSpanExporter(endpoint=_otlp_endpoint(otlp_endpoint, "traces"))
            self.tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
        self.tracer = self.tracer_provider.get_tracer("agentcanvas.backend")

        self.http_requests = self.meter.create_counter(
            "agentcanvas.http.requests", unit="{request}", description="Completed HTTP requests"
        )
        self.http_duration = self.meter.create_histogram(
            "agentcanvas.http.duration", unit="s", description="HTTP request duration"
        )
        self.executions = self.meter.create_counter(
            "agentcanvas.executions", unit="{execution}", description="Terminal executions"
        )
        self.execution_duration = self.meter.create_histogram(
            "agentcanvas.execution.duration", unit="s", description="Workflow execution duration"
        )
        self.node_duration = self.meter.create_histogram(
            "agentcanvas.node.duration", unit="s", description="Node execution duration"
        )
        self.provider_calls = self.meter.create_counter(
            "agentcanvas.provider.calls", unit="{call}", description="Model provider calls"
        )
        self.provider_duration = self.meter.create_histogram(
            "agentcanvas.provider.duration", unit="s", description="Model provider call duration"
        )
        self.provider_ttft = self.meter.create_histogram(
            "agentcanvas.provider.ttft", unit="s", description="Model provider time to first token"
        )
        self.provider_tokens = self.meter.create_counter(
            "agentcanvas.provider.tokens", unit="{token}", description="Model tokens by direction"
        )
        self.mcp_calls = self.meter.create_counter(
            "agentcanvas.mcp.calls", unit="{call}", description="MCP tool calls"
        )
        self.mcp_duration = self.meter.create_histogram(
            "agentcanvas.mcp.duration", unit="s", description="MCP tool call duration"
        )
        self.ingest_documents = self.meter.create_counter(
            "agentcanvas.ingest.documents", unit="{document}", description="Terminal ingestion jobs"
        )
        self.ingest_duration = self.meter.create_histogram(
            "agentcanvas.ingest.duration", unit="s", description="Document ingestion duration"
        )
        self.ingest_bytes = self.meter.create_counter(
            "agentcanvas.ingest.bytes", unit="By", description="Successfully ingested bytes"
        )
        self.retrieval_requests = self.meter.create_counter(
            "agentcanvas.retrieval.requests", unit="{request}", description="Retrieval operations"
        )
        self.retrieval_duration = self.meter.create_histogram(
            "agentcanvas.retrieval.duration", unit="s", description="Retrieval duration"
        )
        self.sse_connections = self.meter.create_up_down_counter(
            "agentcanvas.sse.connections", unit="{connection}", description="Active SSE connections"
        )
        self.sse_replayed = self.meter.create_counter(
            "agentcanvas.sse.replayed_events", unit="{event}", description="Replayed SSE events"
        )
        self.event_queue_depth = self.meter.create_histogram(
            "agentcanvas.event.queue_depth", unit="{event}", description="Event persistence queue depth"
        )

        self._execution_started: dict[str, float] = {}
        self._execution_workflow: dict[str, str] = {}
        self._shutdown = False

    @contextmanager
    def span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Mapping[str, str | int | float | bool] | None = None,
        parent_context: Any = None,
    ) -> Iterator[Any]:
        with self.tracer.start_as_current_span(
            name,
            kind=kind,
            attributes=dict(attributes or {}),
            context=parent_context,
        ) as span:
            try:
                yield span
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise

    def observe_event(self, event: ExecutionEvent) -> None:
        """Turn the stable execution-event protocol into low-cardinality metrics."""
        if event.event_type == EventType.WORKFLOW_STARTED:
            self._execution_started[event.execution_id] = time.perf_counter()
            workflow_id = event.payload.get("workflow_id")
            if isinstance(workflow_id, str):
                self._execution_workflow[event.execution_id] = workflow_id
            return
        if event.event_type in {EventType.NODE_FINISHED, EventType.NODE_FAILED}:
            elapsed_ms = event.payload.get("elapsed_ms")
            if isinstance(elapsed_ms, (int, float)):
                self.node_duration.record(
                    max(0.0, float(elapsed_ms) / 1000),
                    {"status": "failed" if event.event_type == EventType.NODE_FAILED else "succeeded"},
                )
            return
        if event.event_type not in TERMINAL_EVENTS:
            return
        status = {
            EventType.WORKFLOW_FINISHED: "succeeded",
            EventType.WORKFLOW_FAILED: "failed",
            EventType.WORKFLOW_CANCELLED: "cancelled",
        }[event.event_type]
        self.executions.add(1, {"status": status})
        started = self._execution_started.pop(event.execution_id, None)
        self._execution_workflow.pop(event.execution_id, None)
        if started is not None:
            self.execution_duration.record(max(0.0, time.perf_counter() - started), {"status": status})

    def record_http(self, method: str, route: str, status: int, duration: float) -> None:
        attributes = {"method": method, "route": route, "status": str(status)}
        self.http_requests.add(1, attributes)
        self.http_duration.record(max(0.0, duration), attributes)

    def record_provider(
        self,
        provider: str,
        *,
        status: str,
        duration: float,
        ttft: float | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        attributes = {"provider": provider, "status": status}
        self.provider_calls.add(1, attributes)
        self.provider_duration.record(max(0.0, duration), attributes)
        if ttft is not None:
            self.provider_ttft.record(max(0.0, ttft), {"provider": provider})
        if prompt_tokens:
            self.provider_tokens.add(prompt_tokens, {"provider": provider, "direction": "prompt"})
        if completion_tokens:
            self.provider_tokens.add(
                completion_tokens, {"provider": provider, "direction": "completion"}
            )

    def record_mcp(self, *, status: str, duration: float) -> None:
        self.mcp_calls.add(1, {"status": status})
        self.mcp_duration.record(max(0.0, duration), {"status": status})

    def record_ingest(self, *, status: str, duration: float, size_bytes: int = 0) -> None:
        self.ingest_documents.add(1, {"status": status})
        self.ingest_duration.record(max(0.0, duration), {"status": status})
        if status == "succeeded" and size_bytes > 0:
            self.ingest_bytes.add(size_bytes)

    def record_retrieval(self, *, status: str, duration: float) -> None:
        self.retrieval_requests.add(1, {"status": status})
        self.retrieval_duration.record(max(0.0, duration), {"status": status})

    def observe_event_queue(self, depth: int) -> None:
        self.event_queue_depth.record(max(0, depth))

    def sse_opened(self) -> None:
        self.sse_connections.add(1)

    def sse_closed(self) -> None:
        self.sse_connections.add(-1)

    def sse_replay(self, count: int) -> None:
        if count > 0:
            self.sse_replayed.add(count)

    def render_metrics(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()


class RequestObservabilityMiddleware:
    """Pure-ASGI request tracing without introducing a cancellation task boundary."""

    def __init__(self, app: ASGIApp, *, observability: Observability) -> None:
        self.app = app
        self.observability = observability

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        incoming_id = headers.get("x-request-id", "")
        request_id = incoming_id if REQUEST_ID_PATTERN.fullmatch(incoming_id) else uuid.uuid4().hex
        method = str(scope.get("method", "GET"))
        status_code = 500
        started = time.perf_counter()
        parent_context = propagate.extract(headers)

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                response_headers = list(message.get("headers", []))
                response_headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = response_headers
            await send(message)

        with bind_log_context(request_id=request_id), self.observability.span(
            f"HTTP {method}",
            kind=SpanKind.SERVER,
            attributes={"http.request.method": method},
            parent_context=parent_context,
        ) as span:
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception:
                status_code = 500
                raise
            finally:
                route_obj = scope.get("route")
                route = str(getattr(route_obj, "path", "unmatched"))
                duration = time.perf_counter() - started
                span.update_name(f"{method} {route}")
                span.set_attribute("http.route", route)
                span.set_attribute("http.response.status_code", status_code)
                self.observability.record_http(method, route, status_code, duration)
                error_code = f"http_{status_code}" if status_code >= 400 else None
                with bind_log_context(error_code=error_code):
                    logger.info(
                        "http request completed",
                        extra={
                            "method": method,
                            "route": route,
                            "status_code": status_code,
                            "duration_ms": round(duration * 1000, 2),
                        },
                    )
