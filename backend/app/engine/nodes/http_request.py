"""HTTP Request node executor — outbound calls with SSRF protection and retries."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from app.core.outbound_http import (
    OutboundDestinationError,
    _PinnedAsyncHTTPTransport,
    resolve_public_destination,
)
from app.engine.nodes.base import BaseNodeExecutor, CompileContext, NodeFn, register_node
from app.engine.state import WorkflowState
from app.engine.templates import build_context, render_value
from app.schemas.dsl import HttpAuthConfig, HttpRequestConfig, NodeSpec
from app.schemas.events import EventType
from app.services.execution_inspection import bounded_error, bounded_json_snapshot

logger = logging.getLogger(__name__)

# Sensible retry defaults; node config may override ``retry_on_status``.
DEFAULT_RETRY_STATUSES = (408, 429, 502, 503, 504)
MAX_BODY_PREVIEW_CHARS = 4000


def _resolve_auth_headers(
    auth: HttpAuthConfig,
    template_ctx: dict[str, Any],
    resolver: Any,
) -> dict[str, str]:
    """Build the auth headers for a request, resolving secret references."""
    headers: dict[str, str] = {}
    if auth.type == "none":
        return headers

    def _secret(reference: str) -> str:
        if not reference:
            raise RuntimeError(f"auth.type='{auth.type}' requires a secret reference")
        if resolver is None:
            raise RuntimeError("secret resolver is not available; cannot resolve auth token")
        # DSL stores secret references (env://..., docker://..., external://...,
        # plain:...) as plain text — the reference is not secret, only the
        # resolved value is. Resolve it through the provider chain at runtime.
        return resolver.providers.resolve(reference)

    if auth.type == "bearer":
        token = _secret(auth.token_ref)
        headers["Authorization"] = f"{auth.value_prefix or 'Bearer '}{token}".strip()
    elif auth.type == "header":
        token = _secret(auth.token_ref)
        prefix = auth.value_prefix
        headers[auth.header_name] = f"{prefix}{token}" if prefix else token
    elif auth.type == "basic":
        import base64

        username = render_value(auth.username, template_ctx, strict=True)
        password = _secret(auth.password_ref)
        credential = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        headers["Authorization"] = f"Basic {credential}"
    return headers


def _extract_json_path(data: Any, path: str) -> Any:
    """Walk a dotted path through parsed JSON, returning ``None`` if missing."""
    if not path:
        return data
    cur: Any = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def _map_response(
    response: httpx.Response,
    mapping: Any,
) -> dict[str, Any]:
    """Reduce an HTTP response into structured node output."""
    body_bytes = response.content
    text = ""
    parsed: Any = None
    try:
        text = body_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = body_bytes.decode("utf-8", errors="replace")
    if mapping.extract_json:
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = None
    output: dict[str, Any] = {
        "status_code": response.status_code,
        "url": str(response.request.url),
        "method": response.request.method,
    }
    if mapping.include_headers:
        output["headers"] = {k.lower(): v for k, v in response.headers.items()}
    if parsed is not None:
        extracted = _extract_json_path(parsed, mapping.json_path)
        output["json"] = parsed
        if mapping.json_path:
            output["output"] = extracted
        else:
            output["output"] = parsed
    elif mapping.text_fallback:
        output["text"] = text
        output["output"] = text
    else:
        output["output"] = None
    return output


class HttpNodeError(RuntimeError):
    """Raised when an HTTP request fails or returns an unexpected status."""


@register_node("http")
class HttpRequestNodeExecutor(BaseNodeExecutor):
    config_model = HttpRequestConfig
    # Output shape produced by ``_map_response``: the ``output`` field carries
    # the configured json-path extraction (or parsed body / text fallback), and
    # the sibling fields describe the raw response. ``headers`` only appears
    # when ``response.include_headers`` is set; the editor falls back to the
    # live run snapshot for request-specific shapes.
    output_schema = {
        "type": "object",
        "properties": {
            "output": {
                "description": "按 json_path 提取的值;未提取时为解析后的 body 或文本回退",
            },
            "status_code": {"type": "integer", "description": "HTTP 响应状态码"},
            "url": {"type": "string", "description": "实际请求 URL"},
            "method": {"type": "string", "description": "HTTP 方法"},
            "headers": {
                "type": "object",
                "description": "响应头(仅当 response.include_headers 为真时存在)",
            },
            "json": {"type": "object", "description": "解析后的响应体(仅当可解析为 JSON 时存在)"},
            "text": {"type": "string", "description": "原始响应文本(仅当 text_fallback 为真时存在)"},
        },
    }

    def build(self, node: NodeSpec, ctx: CompileContext) -> NodeFn:
        cfg = HttpRequestConfig.model_validate(node.config or {})
        node_id = node.id

        async def run(state: WorkflowState) -> dict[str, Any]:
            template_ctx = build_context(
                inputs=state.get("inputs") or {},
                node_outputs=state.get("node_outputs") or {},
            )

            url = render_value(cfg.url, template_ctx, strict=True)
            if not isinstance(url, str) or not url:
                raise TypeError(f"http node '{node_id}' url must resolve to a non-empty string")

            headers = {k: str(render_value(v, template_ctx, strict=True)) for k, v in cfg.headers.items()}
            headers.setdefault("Accept", "application/json, text/plain, */*")
            auth_headers = _resolve_auth_headers(cfg.auth, template_ctx, ctx.secret_resolver)
            headers.update(auth_headers)

            query = {k: str(render_value(v, template_ctx, strict=True)) for k, v in cfg.query.items()}
            body = render_value(cfg.body, template_ctx, strict=True)
            content = _coerce_body(body)
            if content is not None and "Content-Type" not in headers:
                headers["Content-Type"] = (
                    "application/json" if isinstance(body, (dict, list)) else "text/plain; charset=utf-8"
                )

            retry_cfg = cfg.retry or {}
            max_attempts = max(1, int(retry_cfg.get("max_attempts", 2)))
            retry_statuses = {
                int(s) for s in (retry_cfg.get("retry_on_status") or DEFAULT_RETRY_STATUSES)
            }
            expected = set(cfg.expected_status)

            await ctx.emitter.emit(
                EventType.NODE_STREAMING,
                node_id=node_id,
                payload={
                    "kind": "http_request",
                    "method": cfg.method.value,
                    "url": url,
                    "has_body": content is not None,
                    "auth_type": cfg.auth.type,
                },
            )

            last_error: Exception | None = None
            last_response: httpx.Response | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    last_response = await self._send(
                        cfg,
                        url,
                        headers=headers,
                        query=query,
                        content=content,
                        timeout_seconds=cfg.timeout_seconds,
                        transport=ctx.http_transport,
                    )
                    last_error = None
                except (httpx.HTTPError, OutboundDestinationError, OSError) as exc:
                    last_error = exc
                    last_response = None
                    if attempt >= max_attempts:
                        break
                    await asyncio.sleep(min(30, 2 ** (attempt - 1)))
                    continue

                status = last_response.status_code
                if status in expected:
                    break
                if status in retry_statuses and attempt < max_attempts:
                    await asyncio.sleep(min(30, 2 ** (attempt - 1)))
                    continue
                # Non-retryable failure status
                break

            if last_error is not None:
                err_msg = bounded_error(f"HTTP request failed: {last_error}")
                raise HttpNodeError(err_msg)

            assert last_response is not None  # for type checkers; guaranteed by the loop
            status = last_response.status_code
            mapped = _map_response(last_response, cfg.response)
            await ctx.emitter.emit(
                EventType.NODE_STREAMING,
                node_id=node_id,
                payload={
                    "kind": "http_response",
                    "status_code": status,
                    "elapsed_ms": _elapsed_ms(last_response),
                    "body_preview": _preview(mapped),
                },
            )
            if status not in expected:
                raise HttpNodeError(
                    bounded_error(f"http node '{node_id}' got unexpected status {status}")
                )

            return {
                "node_outputs": {
                    node_id: {
                        "output": bounded_json_snapshot(mapped.get("output")),
                        **{k: v for k, v in mapped.items() if k != "output"},
                    }
                }
            }

        return run

    async def _send(
        self,
        cfg: HttpRequestConfig,
        url: str,
        *,
        headers: dict[str, str],
        query: dict[str, str],
        content: bytes | str | None,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None,
    ) -> httpx.Response:
        """Issue one HTTP attempt through the SSRF-safe transport.

        ``allow_private_network`` gates the SSRF guard uniformly: when False
        (the default) the destination is pinned to pre-resolved public IPs;
        when True the public-IP pin is lifted for explicitly opted-in on-prem
        targets. Redirects, Unix sockets, and environment proxies stay
        disabled on every path.
        """
        if not cfg.allow_private_network:
            # Validate the destination before any transport touches it. This
            # runs the SSRF guard even with an injected (test) transport so
            # the guarantee cannot be bypassed by swapping transports.
            destination = resolve_public_destination(url)
            transport_to_use: Any = (
                transport if transport is not None else _PinnedAsyncHTTPTransport(destination)
            )
        else:
            # Explicitly opted into private-network calls (e.g. on-prem).
            # Redirects/sockets/proxies stay off; only the public-IP pin is lifted.
            transport_to_use = transport
        async with httpx.AsyncClient(
            transport=transport_to_use,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            return await client.request(
                cfg.method.value,
                url,
                headers=headers,
                params=query or None,
                content=content,
                timeout=timeout_seconds,
            )


def _coerce_body(body: Any) -> bytes | str | None:
    if body is None:
        return None
    if isinstance(body, (bytes, str)):
        return body
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _elapsed_ms(response: httpx.Response) -> int | None:
    """Return the response elapsed time in ms, or None when unavailable.

    httpx only populates ``elapsed`` after the response stream is closed by a
    real transport; injected (mock) transports and some error paths leave it
    unset, where accessing it raises. Treat that as "unknown".
    """
    try:
        return int(response.elapsed.total_seconds() * 1000)
    except (RuntimeError, ValueError):
        return None


def _preview(mapped: dict[str, Any]) -> str:
    text = mapped.get("text") or mapped.get("output")
    if not isinstance(text, str):
        try:
            text = json.dumps(text, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(text)
    if len(text) > MAX_BODY_PREVIEW_CHARS:
        return f"{text[:MAX_BODY_PREVIEW_CHARS]}... [truncated]"
    return text
