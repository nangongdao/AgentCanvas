# AgentCanvas Observability

This document is the U4 metric, log, trace, dashboard, and alert contract. Metrics use low-cardinality labels only; resource IDs remain trace/log fields.

## Metric Dictionary

| Metric | Labels | Meaning / SLO use |
|---|---|---|
| `agentcanvas_http_requests_total` | method, route, status | Request volume and HTTP error ratio; route is the FastAPI template, never the raw path |
| `agentcanvas_http_duration_seconds` | method, route, status | API p50/p95/p99; workflow creation target p95 `<500 ms` |
| `agentcanvas_executions_total` | status | Workflow success, failure, and cancellation ratio |
| `agentcanvas_execution_duration_seconds` | status | End-to-end workflow duration excluding human approval wait after process restart |
| `agentcanvas_node_duration_seconds` | status | Node latency and failure distribution; node IDs are intentionally excluded |
| `agentcanvas_provider_calls_total` | provider, status | Provider reliability and call volume |
| `agentcanvas_provider_duration_seconds` | provider, status | Provider latency p95/p99 |
| `agentcanvas_provider_ttft_seconds` | provider | Streaming time to first text token |
| `agentcanvas_provider_tokens_total` | provider, direction | Prompt/completion token consumption; monetary cost stays a dashboard transform because pricing changes independently |
| `agentcanvas_mcp_calls_total` / `agentcanvas_mcp_duration_seconds` | status | MCP reliability and tool-call latency |
| `agentcanvas_ingest_documents_total` / `agentcanvas_ingest_duration_seconds` | status | Ingestion throughput and terminal status |
| `agentcanvas_ingest_bytes_total` | none | Successfully ingested bytes |
| `agentcanvas_retrieval_requests_total` / `agentcanvas_retrieval_duration_seconds` | status | Retrieval reliability and latency |
| `agentcanvas_sse_connections` | none | Active execution event streams |
| `agentcanvas_sse_replayed_events_total` | none | Events replayed after reconnect |
| `agentcanvas_event_queue_depth` | none | Persistence batch backlog samples |

Prometheus scrapes `GET /metrics` on the private backend network. The production frontend does not proxy this path. OTLP traces and metrics are also exported every 30 seconds when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.

## Structured Logs

`LOG_FORMAT=json` emits one object per line with `timestamp`, `level`, `logger`, `message` and the available `request_id`, `execution_id`, `workflow_id`, `node_id`, `provider`, and `error_code`. HTTP completion records add method, route template, status, and duration. The formatter recursively masks secret-like keys, Bearer credentials, URL credentials, and secret query parameters. Request bodies, prompts, tool arguments, document text, cookies, and authorization values are never logged.

Every HTTP response carries `X-Request-ID`. A valid caller-provided value is retained; malformed values are replaced. W3C `traceparent` is extracted so a failed request can be followed through its `HTTP <route>` server span, `workflow.execute`/`workflow.resume`, `workflow.node`, and `provider.*` child spans. Resource IDs are trace attributes for diagnosis, not metric labels.

## Local Dashboard

```bash
docker compose -f compose.prod.yml --env-file .env.production \
  --profile observability up -d prometheus grafana
```

Grafana binds to `127.0.0.1:${GRAFANA_PORT:-3000}` and provisions the AgentCanvas overview dashboard plus Prometheus datasource. Set `GRAFANA_ADMIN_PASSWORD` before enabling the profile. Prometheus and the backend remain private Compose services.

Alert rules are in `observability/alerts.yml`. Initial thresholds deliberately favor actionable symptoms over noise: backend unavailable for 2 minutes, HTTP 5xx ratio above 5%, execution failures above 10%, provider p95 above 30 seconds, and event backlog p95 above 100. Revisit them after the first seven days of production-shaped traffic.

## Diagnostic Flow

1. Start from the alert panel and select the affected route/provider/status.
2. Find an error JSON log in the same interval and capture `request_id` or `execution_id`.
3. Query the trace backend with that field and inspect execution, node, and provider spans.
4. Confirm the terminal event and persisted execution row before retrying; do not infer success from a disconnected SSE client.
