"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from app.api.router import api_router
from app.contracts import API_CONTRACT_VERSION, install_contract_openapi
from app.core.config import Settings, get_settings
from app.core.container import build_container
from app.core.logging import setup_logging
from app.core.observability import Observability, RequestObservabilityMiddleware

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    external_secret_resolver: Callable[[str], str] | None = None,
) -> FastAPI:
    """Build the FastAPI app with middleware, routes and lifecycle hooks."""
    settings = settings or get_settings()
    if settings.process_role not in {"all", "api"}:
        raise RuntimeError("FastAPI requires APP_PROCESS_ROLE=api (or all for single-host mode)")
    setup_logging(settings.log_level, json_logs=settings.log_format == "json")
    observability = Observability(
        service_name=settings.otel_service_name,
        environment=settings.environment,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
        trace_sample_ratio=settings.otel_trace_sample_ratio,
    )
    rate_limit_client = None
    if settings.redis_url:
        from redis.asyncio import Redis

        # Redis.from_url is lazy; the first request validates shared backend
        # availability and the lifespan closes the pool on shutdown.
        rate_limit_client = Redis.from_url(settings.redis_url, decode_responses=True, protocol=2)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = None
        try:
            container = await build_container(
                settings,
                observability=observability,
                external_secret_resolver=external_secret_resolver,
            )
            app.state.container = container
            logger.info(
                "AgentCanvas backend starting (role=%s db=%s)",
                settings.process_role,
                settings.database_display_url,
            )
            yield
        finally:
            if container is not None:
                await container.shutdown()
                logger.info("AgentCanvas backend shutdown complete")
            if rate_limit_client is not None:
                await rate_limit_client.aclose()

    app = FastAPI(title="AgentCanvas", version=API_CONTRACT_VERSION, lifespan=lifespan)
    install_contract_openapi(app)
    app.state.settings = settings
    app.state.observability = observability

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        content, content_type = observability.render_metrics()
        return Response(content=content, headers={"Content-Type": content_type})

    # U3-6 request-rate slice. Expensive dynamic routes use named buckets so
    # changing a resource ID cannot evade a limit. Horizontal API roles use
    # the configured Redis-backed counter; local mode remains bounded in-process.
    from app.core.operation_policies import rate_limit_rules, request_policy_rules
    from app.core.ratelimit import (
        RateLimitConfig,
        RateLimitMiddleware,
    )
    from app.core.request_policy import (
        RequestPolicyConfig,
        RequestPolicyMiddleware,
    )

    # U3-6 body/concurrency/timeout policies. Upload requests include a bounded
    # multipart allowance above the file ceiling; other routes use the global limit.
    app.add_middleware(
        RequestPolicyMiddleware,
        default_config=RequestPolicyConfig(max_body_bytes=settings.request_body_max_bytes),
        rules=request_policy_rules(settings),
    )

    # Add the BaseHTTP rate limiter after the pure-ASGI operation guard. This
    # makes it outermost, so request timeouts cancel route coroutines directly
    # and wait for their cleanup before returning a response.
    app.add_middleware(
        RateLimitMiddleware,
        default_config=RateLimitConfig(
            settings.rate_limit_default_requests,
            settings.rate_limit_window_seconds,
        ),
        max_buckets=settings.rate_limit_max_buckets,
        rules=rate_limit_rules(settings),
        redis_client=rate_limit_client,
    )

    # Starlette runs the most recently added middleware outermost. CORS must
    # wrap locally generated 429 responses so browser clients can read them.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "Retry-After",
            "X-Execution-ID",
            "X-Request-ID",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-AgentCanvas-Instance",
        ],
    )

    from app.core.instance_header import InstanceHeaderMiddleware

    app.add_middleware(InstanceHeaderMiddleware, instance_id=settings.instance_id)

    # Correlation and tracing are outermost, including CORS/rate-limit errors.
    # The pure-ASGI implementation preserves direct cancellation semantics.
    app.add_middleware(RequestObservabilityMiddleware, observability=observability)

    # C8-3: site-wide defensive security headers. Added last so Starlette
    # places it outermost — every response (route, 429, CORS rejection, probe)
    # is stamped. HSTS only when the deployment opts in via HSTS_ENABLED.
    from app.core.security_headers import SecurityHeadersMiddleware

    app.add_middleware(
        SecurityHeadersMiddleware,
        strict_transport_security=(
            f"max-age={settings.hsts_max_age_seconds}; includeSubDomains"
            if settings.hsts_enabled
            else None
        ),
    )

    # Health & readiness probes are mounted before the API router so they are
    # reachable even if a downstream router import fails, and so they are never
    # shadowed by an auth dependency (liveness must work unauthenticated).
    from app.api.routes.health import router as health_router

    app.include_router(health_router)
    app.include_router(api_router)
    return app


app = create_app()
