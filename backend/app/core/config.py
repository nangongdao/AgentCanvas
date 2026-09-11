"""Application settings loaded from environment / .env file."""

import os
import socket
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.core.oidc_config import OIDCSettings, load_oidc_settings

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"


@dataclass(frozen=True)
class Settings:
    """Immutable application configuration."""

    def __post_init__(self) -> None:
        # Coerce data_dir to a real Path so string inputs from env/tests can
        # never produce `str / str` errors in derived paths (checkpoint_db_path
        # etc.). dataclasses don't coerce types automatically.
        object.__setattr__(self, "data_dir", Path(self.data_dir))
        object.__setattr__(self, "plugin_root_dir", Path(self.plugin_root_dir))
        object.__setattr__(self, "docker_secret_dir", Path(self.docker_secret_dir))

    # Server
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    process_role: str = "all"
    instance_id: str = field(default_factory=socket.gethostname)
    log_level: str = "INFO"
    log_format: str = "json"
    environment: str = "development"
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    # C8-3: HSTS is emitted only when explicitly enabled (deployments behind
    # TLS set HSTS_ENABLED=true); local/dev HTTP must never advertise it.
    hsts_enabled: bool = False
    hsts_max_age_seconds: int = 31536000

    # U4 observability. Empty OTLP endpoint keeps telemetry local while the
    # application-owned Prometheus endpoint remains available.
    otel_service_name: str = "agentcanvas-backend"
    otel_exporter_otlp_endpoint: str = ""
    otel_trace_sample_ratio: float = 1.0

    # Security
    secret_key: str = ""
    docker_secret_dir: Path = Path("/run/secrets")
    auth_mode: str = "disabled"
    admin_api_token: str = ""
    editor_api_token: str = ""
    viewer_api_token: str = ""
    auth_session_ttl_seconds: int = 15 * 60
    auth_refresh_ttl_seconds: int = 30 * 24 * 60 * 60
    oidc: OIDCSettings = field(default_factory=OIDCSettings)

    # Single-process request limiting (U3-6 request-rate slice)
    rate_limit_window_seconds: int = 60
    rate_limit_default_requests: int = 300
    rate_limit_login_requests: int = 10
    rate_limit_execution_requests: int = 10
    rate_limit_webhook_requests: int = 60
    rate_limit_workflow_api_requests: int = 60
    rate_limit_chat_requests: int = 20
    # C3-5: end-user send budget per runtime chat session per window; enforced
    # from durable message rows so it holds across API replicas.
    rate_limit_app_runtime_requests: int = 30
    # C8-2: per-client send bucket for the unauthenticated app runtime,
    # independent of the per-session budget above so one does not starve the
    # other under low test/limit values.
    rate_limit_app_runtime_send_requests: int = 60
    rate_limit_upload_requests: int = 20
    rate_limit_ingest_requests: int = 10
    rate_limit_retrieval_requests: int = 30
    rate_limit_mcp_requests: int = 30
    # Provider endpoint discovery performs an operator-triggered outbound request,
    # so it gets its own bucket rather than sharing an unrelated budget.
    rate_limit_discovery_requests: int = 30
    rate_limit_max_buckets: int = 10_000
    # C8-2: public app runtime is an unauthenticated surface; keep per-client
    # send volume bounded independently of platform chat.
    app_runtime_max_concurrent: int = 4
    app_runtime_timeout_seconds: float = 60.0

    # U3-6 request body, concurrency, and operation timeouts
    request_body_max_bytes: int = 1 * 1024 * 1024
    upload_request_max_bytes: int = 21 * 1024 * 1024
    execution_max_concurrent: int = 8
    execution_request_timeout_seconds: float = 30.0
    model_max_concurrent: int = 8
    model_max_calls_per_execution: int = 32
    model_rate_limit_calls: int = 120
    model_rate_limit_window_seconds: float = 60.0
    model_timeout_seconds: float = 120.0
    # D2 cost governance: per-execution ceilings. 0 / empty mean "no ceiling".
    model_max_concurrent_per_execution: int = 0
    model_max_tokens_per_execution: int = 0
    model_max_cost_usd_per_execution: str | None = None
    chat_max_concurrent: int = 8
    upload_max_concurrent: int = 4
    upload_timeout_seconds: float = 60.0
    ingest_max_concurrent: int = 2
    ingest_timeout_seconds: float = 300.0
    retrieval_max_concurrent: int = 8
    retrieval_timeout_seconds: float = 60.0
    login_max_concurrent: int = 16
    login_timeout_seconds: float = 10.0
    mcp_max_concurrent: int = 4
    mcp_timeout_seconds: float = 30.0
    discovery_max_concurrent: int = 4
    discovery_timeout_seconds: float = 15.0

    # D4 Phase 2 outbound resilience. Redis-backed horizontal roles share these
    # policies; SQLite and unit-test deployments keep process-local state.
    model_resilience_max_attempts: int = 2
    model_resilience_failure_threshold: int = 3
    model_resilience_reset_seconds: float = 30.0
    model_resilience_retry_budget: int = 32
    mcp_resilience_max_attempts: int = 2
    mcp_resilience_failure_threshold: int = 3
    mcp_resilience_reset_seconds: float = 30.0
    mcp_resilience_retry_budget: int = 32
    resilience_retry_window_seconds: float = 60.0

    # Default LLM provider (OpenAI-compatible)
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"

    # Retrieval / ingestion
    rag_max_upload_bytes: int = 20 * 1024 * 1024
    embedding_batch_size: int = 64
    embedding_concurrency: int = 4
    embedding_timeout_seconds: int = 60
    local_embedding_dimensions: int = 384
    chroma_max_concurrent: int = 4
    vector_max_concurrent: int = 4
    # I1 Phase 7 vector backend. ``auto`` keeps Chroma on SQLite single-host and
    # switches to pgvector when DATABASE_URL is PostgreSQL. ``sql`` is the
    # portable database path used by local migration tests.
    vector_backend: str = "auto"

    # Storage
    database_url: str = ""
    redis_url: str = ""
    redis_aof_dir: Path | None = None
    data_dir: Path = field(default_factory=lambda: DATA_DIR)
    # Release containers run the dedicated migration service before the web
    # process. Keep the default enabled for local/dev startup compatibility.
    startup_migrations: bool = True
    sqlite_busy_timeout_ms: int = 5_000
    sqlite_synchronous: str = "NORMAL"
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_timeout_seconds: int = 30
    database_pool_recycle_seconds: int = 1_800
    database_migration_lock_timeout_seconds: int = 60

    # Engine defaults
    default_max_loop_iterations: int = 20
    default_recursion_limit: int = 50
    default_timeout_seconds: int = 300
    sse_heartbeat_seconds: int = 15
    event_flush_interval_ms: int = 50
    # I1 Phase 4 in-process worker / lease knobs. Dedicated worker processes
    # will reuse the same durable queue semantics with multi-owner fencing.
    execution_worker_poll_seconds: float = 0.5
    execution_worker_id: str = ""
    execution_lease_seconds: int = 30
    execution_lease_max_attempts: int = 3
    execution_scheduler_poll_seconds: float = 1.0
    execution_scheduler_batch_size: int = 100
    workflow_schedule_poll_seconds: float = 1.0
    workflow_schedule_batch_size: int = 100
    online_source_sync_poll_seconds: float = 5.0
    online_source_sync_batch_size: int = 10
    online_source_sync_lease_seconds: int = 1_800
    # I1 Phase 5 event relay / shared SSE tail. Redis is optional; without it
    # the relay stays idle and SSE uses bounded PostgreSQL polling.
    event_relay_poll_seconds: float = 0.5
    event_relay_batch_size: int = 100
    event_stream_maxlen: int = 10_000
    # C1-4 durable outbound workflow callbacks.
    workflow_callback_poll_seconds: float = 1.0
    workflow_callback_batch_size: int = 100
    workflow_callback_lease_seconds: int = 30
    workflow_callback_default_timeout_seconds: int = 10
    workflow_callback_default_max_attempts: int = 5
    workflow_callback_default_retry_delay_seconds: int = 10

    # C6-1 execution event retention. ``execution_events`` grows unbounded
    # without a prune step; the retention scheduler deletes detail rows past
    # ``retention_days`` for executions that have reached a terminal state.
    # retention_days=0 disables pruning entirely. The grace window guards
    # against clock drift deleting events from a just-finished execution.
    execution_event_retention_days: int = 30
    execution_event_retention_poll_seconds: float = 300.0
    execution_event_retention_batch_size: int = 1_000
    execution_event_retention_grace_days: int = 7

    # C7-4 outbound email. Without smtp_host the NullMailer logs payloads
    # and invitation links are returned to the inviting admin instead of
    # being emailed; with SMTP configured, stdlib smtplib delivers them
    # off the event loop.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "agentcanvas@localhost"
    smtp_starttls: bool = True
    invitation_expire_days: int = 7

    # C7-5 tenant data compliance. The deletion grace window is the frozen
    # period between a deletion request and the hard purge; an org/platform
    # admin may cancel within it. ``org_deletion_purge_poll_seconds`` drives
    # the scheduler that picks up organizations whose purge has become due.
    # A grace of 0 means the purge is due immediately after the request.
    org_deletion_grace_days: int = 30
    org_deletion_purge_batch_size: int = 200
    org_deletion_purge_poll_seconds: float = 60.0

    # C7-1 usage fact aggregation. Each sweep rewrites the trailing
    # ``lookback_days`` complete UTC days into ``usage_daily_facts``; the
    # rewrite is idempotent and absorbs executions that finish after
    # midnight. Days older than the window are frozen so exported billing
    # history cannot drift when model prices change later.
    usage_fact_lookback_days: int = 3
    usage_fact_poll_seconds: float = 900.0

    # I1 Phase 6 collaboration: process-local without REDIS_URL; shared Redis hub
    # when configured. Redis down with REDIS_URL set fails closed (read-only).
    collaboration_presence_ttl_seconds: int = 30
    collaboration_lock_ttl_seconds: int = 30
    collaboration_sweep_interval_seconds: float = 5.0

    # MCP
    mcp_idle_timeout_seconds: int = 600
    mcp_reaper_interval_seconds: int = 60
    mcp_connect_timeout_seconds: int = 10
    mcp_stdio_allowed_commands: tuple[str, ...] = ("python", "python.exe")
    mcp_stdio_allowed_roots: tuple[Path, ...] = field(
        default_factory=lambda: (BACKEND_DIR / "mcp_servers",)
    )
    mcp_stdio_allowed_packages: tuple[str, ...] = ()

    # D4 Phase 4 process-isolated node plugins.
    plugin_root_dir: Path = field(default_factory=lambda: BACKEND_DIR / "plugins")
    plugin_max_input_bytes: int = 64 * 1024
    plugin_max_output_bytes: int = 64 * 1024
    plugin_max_events: int = 32

    # C8-1 process-level sandbox for plugin/code-node subprocesses.
    # auto  — nsjail, then bwrap, then process_cleanup fallback (first available)
    # nsjail/bubblewrap — pin a backend, degrade to cleanup if the tool is absent
    # process — environment cleanup only (Windows dev, stripped images)
    # none   — opt-out, tests only; rejected in production by validate_runtime_settings
    sandbox_backend: str = "auto"
    sandbox_enforce_permissions: bool = True
    sandbox_cpu_time_seconds: int = 30
    sandbox_address_space_mb: int = 512
    sandbox_file_size_mb: int = 32
    sandbox_process_count: int = 16
    sandbox_open_files: int = 64

    @property
    def sandbox_backend_normalized(self) -> str:
        return (self.sandbox_backend or "auto").strip().lower() or "auto"

    @property
    def effective_database_url(self) -> str:
        """Resolve the async SQLAlchemy URL; default to SQLite under data_dir."""
        if self.database_url:
            return self.database_url
        db_path = (self.data_dir / "app.db").as_posix()
        return f"sqlite+aiosqlite:///{db_path}"

    @property
    def database_backend(self) -> str:
        """Return the validated SQLAlchemy backend name."""
        return make_url(self.effective_database_url).get_backend_name()

    @property
    def database_display_url(self) -> str:
        """Render a database URL that is safe to include in operator logs."""
        return make_url(self.effective_database_url).render_as_string(hide_password=True)

    @property
    def checkpoint_db_path(self) -> Path:
        return self.data_dir / "checkpoints.db"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def workspace_dir(self) -> Path:
        """Sandbox directory exposed to the filesystem MCP server."""
        return self.data_dir / "workspace"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def oidc_enabled(self) -> bool:
        return self.oidc.enabled


def _env(key: str, default: str = "") -> str:
    value = os.environ.get(key, default)
    return value.strip() if value else default


def _csv(key: str, default: str = "") -> tuple[str, ...]:
    return tuple(value.strip() for value in _env(key, default).split(",") if value.strip())


def validate_runtime_settings(settings: Settings) -> None:
    """Fail fast for invalid auth modes and unsafe production configuration."""
    if settings.environment not in {"development", "test", "production"}:
        raise RuntimeError("APP_ENV must be development, test, or production")
    if settings.process_role not in {"all", "api", "worker", "relay", "scheduler"}:
        raise RuntimeError("APP_PROCESS_ROLE must be all, api, worker, relay, or scheduler")
    if settings.auth_mode not in {"disabled", "token"}:
        raise RuntimeError("AUTH_MODE must be disabled or token")
    if settings.log_format not in {"json", "console"}:
        raise RuntimeError("LOG_FORMAT must be json or console")
    try:
        database_driver = make_url(settings.effective_database_url).drivername
    except ArgumentError as exc:
        raise RuntimeError("DATABASE_URL must use sqlite+aiosqlite or postgresql+asyncpg") from exc
    if database_driver not in {"sqlite+aiosqlite", "postgresql+asyncpg"}:
        raise RuntimeError("DATABASE_URL must use sqlite+aiosqlite or postgresql+asyncpg")
    if settings.process_role != "all":
        if database_driver != "postgresql+asyncpg":
            raise RuntimeError("horizontal process roles require postgresql+asyncpg")
        if not settings.redis_url:
            raise RuntimeError("horizontal process roles require REDIS_URL")
        if settings.startup_migrations:
            raise RuntimeError("horizontal process roles require STARTUP_MIGRATIONS=false")
    vector_backend = (settings.vector_backend or "auto").strip().lower()
    if vector_backend not in {"auto", "chroma", "sql", "pgvector"}:
        raise RuntimeError("VECTOR_BACKEND must be auto, chroma, sql, or pgvector")
    if vector_backend == "pgvector" and database_driver != "postgresql+asyncpg":
        raise RuntimeError("VECTOR_BACKEND=pgvector requires postgresql+asyncpg")
    if not 0 <= settings.otel_trace_sample_ratio <= 1:
        raise RuntimeError("OTEL_TRACE_SAMPLE_RATIO must be between 0 and 1")
    if settings.auth_mode == "token" and len(settings.admin_api_token) < 16:
        raise RuntimeError("ADMIN_API_TOKEN must contain at least 16 characters")
    if settings.auth_session_ttl_seconds < 60:
        raise RuntimeError("AUTH_SESSION_TTL_SECONDS must be at least 60")
    if settings.auth_refresh_ttl_seconds <= settings.auth_session_ttl_seconds:
        raise RuntimeError("AUTH_REFRESH_TTL_SECONDS must exceed AUTH_SESSION_TTL_SECONDS")
    collaboration_limits = {
        "COLLABORATION_PRESENCE_TTL_SECONDS": settings.collaboration_presence_ttl_seconds,
        "COLLABORATION_LOCK_TTL_SECONDS": settings.collaboration_lock_ttl_seconds,
        "COLLABORATION_SWEEP_INTERVAL_SECONDS": settings.collaboration_sweep_interval_seconds,
    }
    for name, value in collaboration_limits.items():
        if value <= 0:
            raise RuntimeError(f"{name} must be positive")
    if settings.collaboration_sweep_interval_seconds > min(
        settings.collaboration_presence_ttl_seconds,
        settings.collaboration_lock_ttl_seconds,
    ):
        raise RuntimeError("COLLABORATION_SWEEP_INTERVAL_SECONDS cannot exceed a collaboration TTL")
    if settings.rag_max_upload_bytes < 1:
        raise RuntimeError("RAG_MAX_UPLOAD_BYTES must be positive")
    if settings.embedding_batch_size < 1:
        raise RuntimeError("EMBEDDING_BATCH_SIZE must be positive")
    if settings.embedding_concurrency < 1:
        raise RuntimeError("EMBEDDING_CONCURRENCY must be positive")
    if settings.embedding_timeout_seconds < 1:
        raise RuntimeError("EMBEDDING_TIMEOUT_SECONDS must be positive")
    if settings.local_embedding_dimensions < 32:
        raise RuntimeError("LOCAL_EMBEDDING_DIMENSIONS must be at least 32")
    if settings.chroma_max_concurrent < 1:
        raise RuntimeError("CHROMA_MAX_CONCURRENT must be positive")
    if settings.vector_max_concurrent < 1:
        raise RuntimeError("VECTOR_MAX_CONCURRENT must be positive")
    if settings.sqlite_busy_timeout_ms < 1:
        raise RuntimeError("SQLITE_BUSY_TIMEOUT_MS must be positive")
    if settings.sqlite_synchronous not in {"OFF", "NORMAL", "FULL", "EXTRA"}:
        raise RuntimeError("SQLITE_SYNCHRONOUS must be OFF, NORMAL, FULL, or EXTRA")
    database_limits = {
        "DATABASE_POOL_SIZE": settings.database_pool_size,
        "DATABASE_POOL_TIMEOUT_SECONDS": settings.database_pool_timeout_seconds,
        "DATABASE_POOL_RECYCLE_SECONDS": settings.database_pool_recycle_seconds,
        "DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS": (
            settings.database_migration_lock_timeout_seconds
        ),
    }
    for name, value in database_limits.items():
        if value < 1:
            raise RuntimeError(f"{name} must be positive")
    if settings.database_max_overflow < 0:
        raise RuntimeError("DATABASE_MAX_OVERFLOW must be zero or positive")
    rate_limits = {
        "RATE_LIMIT_WINDOW_SECONDS": settings.rate_limit_window_seconds,
        "RATE_LIMIT_DEFAULT_REQUESTS": settings.rate_limit_default_requests,
        "RATE_LIMIT_LOGIN_REQUESTS": settings.rate_limit_login_requests,
        "RATE_LIMIT_EXECUTION_REQUESTS": settings.rate_limit_execution_requests,
        "RATE_LIMIT_WEBHOOK_REQUESTS": settings.rate_limit_webhook_requests,
        "RATE_LIMIT_WORKFLOW_API_REQUESTS": settings.rate_limit_workflow_api_requests,
        "RATE_LIMIT_CHAT_REQUESTS": settings.rate_limit_chat_requests,
        "RATE_LIMIT_APP_RUNTIME_REQUESTS": settings.rate_limit_app_runtime_requests,
        "RATE_LIMIT_APP_RUNTIME_SEND_REQUESTS": settings.rate_limit_app_runtime_send_requests,
        "RATE_LIMIT_UPLOAD_REQUESTS": settings.rate_limit_upload_requests,
        "RATE_LIMIT_INGEST_REQUESTS": settings.rate_limit_ingest_requests,
        "RATE_LIMIT_RETRIEVAL_REQUESTS": settings.rate_limit_retrieval_requests,
        "RATE_LIMIT_MCP_REQUESTS": settings.rate_limit_mcp_requests,
        "RATE_LIMIT_DISCOVERY_REQUESTS": settings.rate_limit_discovery_requests,
        "RATE_LIMIT_MAX_BUCKETS": settings.rate_limit_max_buckets,
    }
    for name, value in rate_limits.items():
        if value < 1:
            raise RuntimeError(f"{name} must be positive")
    operation_limits: dict[str, float] = {
        "REQUEST_BODY_MAX_BYTES": settings.request_body_max_bytes,
        "UPLOAD_REQUEST_MAX_BYTES": settings.upload_request_max_bytes,
        "EXECUTION_MAX_CONCURRENT": settings.execution_max_concurrent,
        "EXECUTION_REQUEST_TIMEOUT_SECONDS": settings.execution_request_timeout_seconds,
        "APP_RUNTIME_MAX_CONCURRENT": settings.app_runtime_max_concurrent,
        "APP_RUNTIME_TIMEOUT_SECONDS": settings.app_runtime_timeout_seconds,
        "MODEL_MAX_CONCURRENT": settings.model_max_concurrent,
        "MODEL_MAX_CALLS_PER_EXECUTION": settings.model_max_calls_per_execution,
        "MODEL_RATE_LIMIT_CALLS": settings.model_rate_limit_calls,
        "MODEL_RATE_LIMIT_WINDOW_SECONDS": settings.model_rate_limit_window_seconds,
        "MODEL_TIMEOUT_SECONDS": settings.model_timeout_seconds,
        "CHAT_MAX_CONCURRENT": settings.chat_max_concurrent,
        "UPLOAD_MAX_CONCURRENT": settings.upload_max_concurrent,
        "UPLOAD_TIMEOUT_SECONDS": settings.upload_timeout_seconds,
        "INGEST_MAX_CONCURRENT": settings.ingest_max_concurrent,
        "INGEST_TIMEOUT_SECONDS": settings.ingest_timeout_seconds,
        "RETRIEVAL_MAX_CONCURRENT": settings.retrieval_max_concurrent,
        "RETRIEVAL_TIMEOUT_SECONDS": settings.retrieval_timeout_seconds,
        "LOGIN_MAX_CONCURRENT": settings.login_max_concurrent,
        "LOGIN_TIMEOUT_SECONDS": settings.login_timeout_seconds,
        "MCP_MAX_CONCURRENT": settings.mcp_max_concurrent,
        "MCP_TIMEOUT_SECONDS": settings.mcp_timeout_seconds,
        "DISCOVERY_MAX_CONCURRENT": settings.discovery_max_concurrent,
        "DISCOVERY_TIMEOUT_SECONDS": settings.discovery_timeout_seconds,
    }
    for name, limit_value in operation_limits.items():
        if limit_value <= 0:
            raise RuntimeError(f"{name} must be positive")
    resilience_limits = {
        "MODEL_RESILIENCE_MAX_ATTEMPTS": settings.model_resilience_max_attempts,
        "MODEL_RESILIENCE_FAILURE_THRESHOLD": settings.model_resilience_failure_threshold,
        "MCP_RESILIENCE_MAX_ATTEMPTS": settings.mcp_resilience_max_attempts,
        "MCP_RESILIENCE_FAILURE_THRESHOLD": settings.mcp_resilience_failure_threshold,
    }
    for name, value in resilience_limits.items():
        if value < 1:
            raise RuntimeError(f"{name} must be positive")
    if settings.model_resilience_retry_budget < 0:
        raise RuntimeError("MODEL_RESILIENCE_RETRY_BUDGET must be non-negative")
    if settings.mcp_resilience_retry_budget < 0:
        raise RuntimeError("MCP_RESILIENCE_RETRY_BUDGET must be non-negative")
    plugin_limits = {
        "PLUGIN_MAX_INPUT_BYTES": settings.plugin_max_input_bytes,
        "PLUGIN_MAX_OUTPUT_BYTES": settings.plugin_max_output_bytes,
        "PLUGIN_MAX_EVENTS": settings.plugin_max_events,
    }
    for name, value in plugin_limits.items():
        if value < 1:
            raise RuntimeError(f"{name} must be positive")
    if settings.sandbox_backend_normalized not in {
        "auto",
        "nsjail",
        "bubblewrap",
        "process",
        "none",
    }:
        raise RuntimeError("SANDBOX_BACKEND must be auto, nsjail, bubblewrap, process, or none")
    if settings.is_production and settings.sandbox_backend_normalized == "none":
        raise RuntimeError("SANDBOX_BACKEND=none is not permitted in production")
    sandbox_limits = {
        "SANDBOX_CPU_TIME_SECONDS": settings.sandbox_cpu_time_seconds,
        "SANDBOX_ADDRESS_SPACE_MB": settings.sandbox_address_space_mb,
        "SANDBOX_FILE_SIZE_MB": settings.sandbox_file_size_mb,
        "SANDBOX_PROCESS_COUNT": settings.sandbox_process_count,
        "SANDBOX_OPEN_FILES": settings.sandbox_open_files,
    }
    for name, value in sandbox_limits.items():
        if value < 1:
            raise RuntimeError(f"{name} must be positive")
    docker_secret_dir = str(settings.docker_secret_dir)
    is_rooted_windows_path = os.name == "nt" and docker_secret_dir.startswith(("/", "\\"))
    if not settings.docker_secret_dir.is_absolute() and not is_rooted_windows_path:
        raise RuntimeError("DOCKER_SECRET_DIR must be an absolute path")
    for name, value in {
        "MODEL_RESILIENCE_RESET_SECONDS": settings.model_resilience_reset_seconds,
        "MCP_RESILIENCE_RESET_SECONDS": settings.mcp_resilience_reset_seconds,
        "RESILIENCE_RETRY_WINDOW_SECONDS": settings.resilience_retry_window_seconds,
    }.items():
        if value <= 0:
            raise RuntimeError(f"{name} must be positive")
    if settings.model_max_concurrent_per_execution < 0:
        raise RuntimeError("MODEL_MAX_CONCURRENT_PER_EXECUTION must be non-negative")
    if settings.model_max_tokens_per_execution < 0:
        raise RuntimeError("MODEL_MAX_TOKENS_PER_EXECUTION must be non-negative")
    if settings.model_max_cost_usd_per_execution is not None:
        try:
            cost_limit = Decimal(settings.model_max_cost_usd_per_execution)
        except InvalidOperation as exc:
            raise RuntimeError(
                "MODEL_MAX_COST_USD_PER_EXECUTION must be a non-negative decimal"
            ) from exc
        if not cost_limit.is_finite() or cost_limit < 0:
            raise RuntimeError("MODEL_MAX_COST_USD_PER_EXECUTION must be a non-negative decimal")
    if settings.upload_request_max_bytes <= settings.rag_max_upload_bytes:
        raise RuntimeError(
            "UPLOAD_REQUEST_MAX_BYTES must exceed RAG_MAX_UPLOAD_BYTES for multipart overhead"
        )
    execution_lease_limits = {
        "EXECUTION_WORKER_POLL_SECONDS": settings.execution_worker_poll_seconds,
        "EXECUTION_LEASE_SECONDS": settings.execution_lease_seconds,
        "EXECUTION_LEASE_MAX_ATTEMPTS": settings.execution_lease_max_attempts,
        "EXECUTION_SCHEDULER_POLL_SECONDS": settings.execution_scheduler_poll_seconds,
        "EXECUTION_SCHEDULER_BATCH_SIZE": settings.execution_scheduler_batch_size,
        "WORKFLOW_SCHEDULE_POLL_SECONDS": settings.workflow_schedule_poll_seconds,
        "WORKFLOW_SCHEDULE_BATCH_SIZE": settings.workflow_schedule_batch_size,
        "ONLINE_SOURCE_SYNC_POLL_SECONDS": settings.online_source_sync_poll_seconds,
        "ONLINE_SOURCE_SYNC_BATCH_SIZE": settings.online_source_sync_batch_size,
        "ONLINE_SOURCE_SYNC_LEASE_SECONDS": settings.online_source_sync_lease_seconds,
        "EVENT_RELAY_POLL_SECONDS": settings.event_relay_poll_seconds,
        "EVENT_RELAY_BATCH_SIZE": settings.event_relay_batch_size,
        "EVENT_STREAM_MAXLEN": settings.event_stream_maxlen,
        "WORKFLOW_CALLBACK_POLL_SECONDS": settings.workflow_callback_poll_seconds,
        "WORKFLOW_CALLBACK_BATCH_SIZE": settings.workflow_callback_batch_size,
        "WORKFLOW_CALLBACK_LEASE_SECONDS": settings.workflow_callback_lease_seconds,
        "WORKFLOW_CALLBACK_DEFAULT_TIMEOUT_SECONDS": settings.workflow_callback_default_timeout_seconds,
        "WORKFLOW_CALLBACK_DEFAULT_MAX_ATTEMPTS": settings.workflow_callback_default_max_attempts,
        "WORKFLOW_CALLBACK_DEFAULT_RETRY_DELAY_SECONDS": settings.workflow_callback_default_retry_delay_seconds,
        "EXECUTION_EVENT_RETENTION_POLL_SECONDS": settings.execution_event_retention_poll_seconds,
        "EXECUTION_EVENT_RETENTION_BATCH_SIZE": settings.execution_event_retention_batch_size,
        "EXECUTION_EVENT_RETENTION_GRACE_DAYS": settings.execution_event_retention_grace_days,
        "ORG_DELETION_PURGE_POLL_SECONDS": settings.org_deletion_purge_poll_seconds,
        "ORG_DELETION_PURGE_BATCH_SIZE": settings.org_deletion_purge_batch_size,
    }
    for name, value in execution_lease_limits.items():
        if value <= 0:
            raise RuntimeError(f"{name} must be positive")
    # retention_days=0 disables pruning, so it only needs to be non-negative.
    if settings.execution_event_retention_days < 0:
        raise RuntimeError("EXECUTION_EVENT_RETENTION_DAYS must be non-negative")
    if settings.invitation_expire_days < 1:
        raise RuntimeError("INVITATION_EXPIRE_DAYS must be >= 1")
    # grace_days=0 makes the purge due immediately after the request.
    if settings.org_deletion_grace_days < 0:
        raise RuntimeError("ORG_DELETION_GRACE_DAYS must be non-negative")
    if settings.usage_fact_lookback_days < 1:
        raise RuntimeError("USAGE_FACT_LOOKBACK_DAYS must be >= 1")
    if settings.usage_fact_poll_seconds <= 0:
        raise RuntimeError("USAGE_FACT_POLL_SECONDS must be positive")
    settings.oidc.validate(
        environment=settings.environment,
        auth_mode=settings.auth_mode,
        cors_origins=settings.cors_origins,
    )
    if not settings.is_production:
        return
    if settings.auth_mode != "token":
        raise RuntimeError("production requires AUTH_MODE=token")
    if len(settings.admin_api_token) < 32:
        raise RuntimeError("production ADMIN_API_TOKEN must contain at least 32 characters")
    if not settings.secret_key:
        raise RuntimeError("production requires SECRET_KEY")


def load_settings() -> Settings:
    """Build Settings from environment, loading .env from project root first."""
    for candidate in (PROJECT_ROOT / ".env", BACKEND_DIR / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            break

    cors_raw = _env("CORS_ORIGINS")
    cors = (
        tuple(origin.strip() for origin in cors_raw.split(",") if origin.strip())
        if cors_raw
        else Settings.cors_origins
    )

    return Settings(
        app_host=_env("APP_HOST", "0.0.0.0"),
        app_port=int(_env("APP_PORT", "8000")),
        process_role=_env("APP_PROCESS_ROLE", "all").lower(),
        instance_id=_env("APP_INSTANCE_ID") or socket.gethostname(),
        log_level=_env("LOG_LEVEL", "INFO").upper(),
        log_format=_env("LOG_FORMAT", "json").lower(),
        environment=_env("APP_ENV", "development").lower(),
        cors_origins=cors,
        hsts_enabled=_env("HSTS_ENABLED", "false").lower() == "true",
        hsts_max_age_seconds=int(_env("HSTS_MAX_AGE_SECONDS", "31536000")),
        otel_service_name=_env("OTEL_SERVICE_NAME", "agentcanvas-backend"),
        otel_exporter_otlp_endpoint=_env("OTEL_EXPORTER_OTLP_ENDPOINT"),
        otel_trace_sample_ratio=float(_env("OTEL_TRACE_SAMPLE_RATIO", "1.0")),
        secret_key=_env("SECRET_KEY"),
        docker_secret_dir=Path(_env("DOCKER_SECRET_DIR", "/run/secrets")).expanduser(),
        auth_mode=_env("AUTH_MODE", "disabled").lower(),
        admin_api_token=_env("ADMIN_API_TOKEN"),
        editor_api_token=_env("EDITOR_API_TOKEN"),
        viewer_api_token=_env("VIEWER_API_TOKEN"),
        auth_session_ttl_seconds=int(_env("AUTH_SESSION_TTL_SECONDS", "900")),
        auth_refresh_ttl_seconds=int(_env("AUTH_REFRESH_TTL_SECONDS", "2592000")),
        oidc=load_oidc_settings(_env, _csv),
        collaboration_presence_ttl_seconds=int(_env("COLLABORATION_PRESENCE_TTL_SECONDS", "30")),
        collaboration_lock_ttl_seconds=int(_env("COLLABORATION_LOCK_TTL_SECONDS", "30")),
        collaboration_sweep_interval_seconds=float(
            _env("COLLABORATION_SWEEP_INTERVAL_SECONDS", "5")
        ),
        rate_limit_window_seconds=int(_env("RATE_LIMIT_WINDOW_SECONDS", "60")),
        rate_limit_default_requests=int(_env("RATE_LIMIT_DEFAULT_REQUESTS", "300")),
        rate_limit_login_requests=int(_env("RATE_LIMIT_LOGIN_REQUESTS", "10")),
        rate_limit_execution_requests=int(_env("RATE_LIMIT_EXECUTION_REQUESTS", "10")),
        rate_limit_webhook_requests=int(_env("RATE_LIMIT_WEBHOOK_REQUESTS", "60")),
        rate_limit_workflow_api_requests=int(_env("RATE_LIMIT_WORKFLOW_API_REQUESTS", "60")),
        rate_limit_chat_requests=int(_env("RATE_LIMIT_CHAT_REQUESTS", "20")),
        rate_limit_app_runtime_requests=int(_env("RATE_LIMIT_APP_RUNTIME_REQUESTS", "30")),
        rate_limit_app_runtime_send_requests=int(
            _env("RATE_LIMIT_APP_RUNTIME_SEND_REQUESTS", "60")
        ),
        rate_limit_upload_requests=int(_env("RATE_LIMIT_UPLOAD_REQUESTS", "20")),
        rate_limit_ingest_requests=int(_env("RATE_LIMIT_INGEST_REQUESTS", "10")),
        rate_limit_retrieval_requests=int(_env("RATE_LIMIT_RETRIEVAL_REQUESTS", "30")),
        rate_limit_mcp_requests=int(_env("RATE_LIMIT_MCP_REQUESTS", "30")),
        rate_limit_discovery_requests=int(_env("RATE_LIMIT_DISCOVERY_REQUESTS", "30")),
        rate_limit_max_buckets=int(_env("RATE_LIMIT_MAX_BUCKETS", "10000")),
        request_body_max_bytes=int(_env("REQUEST_BODY_MAX_BYTES", str(1 * 1024 * 1024))),
        upload_request_max_bytes=int(_env("UPLOAD_REQUEST_MAX_BYTES", str(21 * 1024 * 1024))),
        execution_max_concurrent=int(_env("EXECUTION_MAX_CONCURRENT", "8")),
        execution_request_timeout_seconds=float(_env("EXECUTION_REQUEST_TIMEOUT_SECONDS", "30")),
        app_runtime_max_concurrent=int(_env("APP_RUNTIME_MAX_CONCURRENT", "4")),
        app_runtime_timeout_seconds=float(_env("APP_RUNTIME_TIMEOUT_SECONDS", "60")),
        model_max_concurrent=int(_env("MODEL_MAX_CONCURRENT", "8")),
        model_max_calls_per_execution=int(_env("MODEL_MAX_CALLS_PER_EXECUTION", "32")),
        model_max_concurrent_per_execution=int(_env("MODEL_MAX_CONCURRENT_PER_EXECUTION", "0")),
        model_max_tokens_per_execution=int(_env("MODEL_MAX_TOKENS_PER_EXECUTION", "0")),
        model_max_cost_usd_per_execution=(_env("MODEL_MAX_COST_USD_PER_EXECUTION") or None),
        model_rate_limit_calls=int(_env("MODEL_RATE_LIMIT_CALLS", "120")),
        model_rate_limit_window_seconds=float(_env("MODEL_RATE_LIMIT_WINDOW_SECONDS", "60")),
        model_timeout_seconds=float(_env("MODEL_TIMEOUT_SECONDS", "120")),
        chat_max_concurrent=int(_env("CHAT_MAX_CONCURRENT", "8")),
        upload_max_concurrent=int(_env("UPLOAD_MAX_CONCURRENT", "4")),
        upload_timeout_seconds=float(_env("UPLOAD_TIMEOUT_SECONDS", "60")),
        ingest_max_concurrent=int(_env("INGEST_MAX_CONCURRENT", "2")),
        ingest_timeout_seconds=float(_env("INGEST_TIMEOUT_SECONDS", "300")),
        retrieval_max_concurrent=int(_env("RETRIEVAL_MAX_CONCURRENT", "8")),
        retrieval_timeout_seconds=float(_env("RETRIEVAL_TIMEOUT_SECONDS", "60")),
        login_max_concurrent=int(_env("LOGIN_MAX_CONCURRENT", "16")),
        login_timeout_seconds=float(_env("LOGIN_TIMEOUT_SECONDS", "10")),
        mcp_max_concurrent=int(_env("MCP_MAX_CONCURRENT", "4")),
        mcp_timeout_seconds=float(_env("MCP_TIMEOUT_SECONDS", "30")),
        discovery_max_concurrent=int(_env("DISCOVERY_MAX_CONCURRENT", "4")),
        discovery_timeout_seconds=float(_env("DISCOVERY_TIMEOUT_SECONDS", "15")),
        model_resilience_max_attempts=int(_env("MODEL_RESILIENCE_MAX_ATTEMPTS", "2")),
        model_resilience_failure_threshold=int(_env("MODEL_RESILIENCE_FAILURE_THRESHOLD", "3")),
        model_resilience_reset_seconds=float(_env("MODEL_RESILIENCE_RESET_SECONDS", "30")),
        model_resilience_retry_budget=int(_env("MODEL_RESILIENCE_RETRY_BUDGET", "32")),
        mcp_resilience_max_attempts=int(_env("MCP_RESILIENCE_MAX_ATTEMPTS", "2")),
        mcp_resilience_failure_threshold=int(_env("MCP_RESILIENCE_FAILURE_THRESHOLD", "3")),
        mcp_resilience_reset_seconds=float(_env("MCP_RESILIENCE_RESET_SECONDS", "30")),
        mcp_resilience_retry_budget=int(_env("MCP_RESILIENCE_RETRY_BUDGET", "32")),
        resilience_retry_window_seconds=float(_env("RESILIENCE_RETRY_WINDOW_SECONDS", "60")),
        openai_base_url=_env("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_model=_env("OPENAI_MODEL", "gpt-4o-mini"),
        embedding_model=_env("EMBEDDING_MODEL", "text-embedding-3-small"),
        rag_max_upload_bytes=int(_env("RAG_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))),
        embedding_batch_size=int(_env("EMBEDDING_BATCH_SIZE", "64")),
        embedding_concurrency=int(_env("EMBEDDING_CONCURRENCY", "4")),
        embedding_timeout_seconds=int(_env("EMBEDDING_TIMEOUT_SECONDS", "60")),
        local_embedding_dimensions=int(_env("LOCAL_EMBEDDING_DIMENSIONS", "384")),
        chroma_max_concurrent=int(_env("CHROMA_MAX_CONCURRENT", "4")),
        vector_max_concurrent=int(_env("VECTOR_MAX_CONCURRENT", "4")),
        vector_backend=_env("VECTOR_BACKEND", "auto").lower() or "auto",
        database_url=_env("DATABASE_URL"),
        redis_url=_env("REDIS_URL"),
        data_dir=Path(_env("APP_DATA_DIR", str(DATA_DIR))).expanduser(),
        redis_aof_dir=(Path(_env("REDIS_AOF_DIR")).expanduser() if _env("REDIS_AOF_DIR") else None),
        startup_migrations=_env("STARTUP_MIGRATIONS", "true").lower()
        not in {"0", "false", "no", "off"},
        sqlite_busy_timeout_ms=int(_env("SQLITE_BUSY_TIMEOUT_MS", "5000")),
        sqlite_synchronous=_env("SQLITE_SYNCHRONOUS", "NORMAL").upper(),
        database_pool_size=int(_env("DATABASE_POOL_SIZE", "5")),
        database_max_overflow=int(_env("DATABASE_MAX_OVERFLOW", "10")),
        database_pool_timeout_seconds=int(_env("DATABASE_POOL_TIMEOUT_SECONDS", "30")),
        database_pool_recycle_seconds=int(_env("DATABASE_POOL_RECYCLE_SECONDS", "1800")),
        database_migration_lock_timeout_seconds=int(
            _env("DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS", "60")
        ),
        mcp_idle_timeout_seconds=int(_env("MCP_IDLE_TIMEOUT_SECONDS", "600")),
        mcp_reaper_interval_seconds=int(_env("MCP_REAPER_INTERVAL_SECONDS", "60")),
        mcp_connect_timeout_seconds=int(_env("MCP_CONNECT_TIMEOUT_SECONDS", "10")),
        mcp_stdio_allowed_commands=_csv("MCP_STDIO_ALLOWED_COMMANDS", "python,python.exe"),
        mcp_stdio_allowed_roots=tuple(
            Path(value).expanduser() for value in _csv("MCP_STDIO_ALLOWED_ROOTS")
        )
        or (BACKEND_DIR / "mcp_servers",),
        mcp_stdio_allowed_packages=_csv("MCP_STDIO_ALLOWED_PACKAGES"),
        plugin_root_dir=Path(_env("PLUGIN_ROOT_DIR", str(BACKEND_DIR / "plugins"))).expanduser(),
        plugin_max_input_bytes=int(_env("PLUGIN_MAX_INPUT_BYTES", str(64 * 1024))),
        plugin_max_output_bytes=int(_env("PLUGIN_MAX_OUTPUT_BYTES", str(64 * 1024))),
        plugin_max_events=int(_env("PLUGIN_MAX_EVENTS", "32")),
        sandbox_backend=_env("SANDBOX_BACKEND", "auto"),
        sandbox_enforce_permissions=_env("SANDBOX_ENFORCE_PERMISSIONS", "true").lower()
        not in {"0", "false", "no", "off"},
        sandbox_cpu_time_seconds=int(_env("SANDBOX_CPU_TIME_SECONDS", "30")),
        sandbox_address_space_mb=int(_env("SANDBOX_ADDRESS_SPACE_MB", "512")),
        sandbox_file_size_mb=int(_env("SANDBOX_FILE_SIZE_MB", "32")),
        sandbox_process_count=int(_env("SANDBOX_PROCESS_COUNT", "16")),
        sandbox_open_files=int(_env("SANDBOX_OPEN_FILES", "64")),
        default_max_loop_iterations=int(_env("DEFAULT_MAX_LOOP_ITERATIONS", "20")),
        default_recursion_limit=int(_env("DEFAULT_RECURSION_LIMIT", "50")),
        default_timeout_seconds=int(_env("DEFAULT_TIMEOUT_SECONDS", "300")),
        sse_heartbeat_seconds=int(_env("SSE_HEARTBEAT_SECONDS", "15")),
        event_flush_interval_ms=int(_env("EVENT_FLUSH_INTERVAL_MS", "50")),
        execution_worker_poll_seconds=float(_env("EXECUTION_WORKER_POLL_SECONDS", "0.5")),
        execution_worker_id=_env("EXECUTION_WORKER_ID"),
        execution_lease_seconds=int(_env("EXECUTION_LEASE_SECONDS", "30")),
        execution_lease_max_attempts=int(_env("EXECUTION_LEASE_MAX_ATTEMPTS", "3")),
        execution_scheduler_poll_seconds=float(_env("EXECUTION_SCHEDULER_POLL_SECONDS", "1.0")),
        execution_scheduler_batch_size=int(_env("EXECUTION_SCHEDULER_BATCH_SIZE", "100")),
        workflow_schedule_poll_seconds=float(_env("WORKFLOW_SCHEDULE_POLL_SECONDS", "1")),
        workflow_schedule_batch_size=int(_env("WORKFLOW_SCHEDULE_BATCH_SIZE", "100")),
        online_source_sync_poll_seconds=float(_env("ONLINE_SOURCE_SYNC_POLL_SECONDS", "5")),
        online_source_sync_batch_size=int(_env("ONLINE_SOURCE_SYNC_BATCH_SIZE", "10")),
        online_source_sync_lease_seconds=int(_env("ONLINE_SOURCE_SYNC_LEASE_SECONDS", "1800")),
        event_relay_poll_seconds=float(_env("EVENT_RELAY_POLL_SECONDS", "0.5")),
        event_relay_batch_size=int(_env("EVENT_RELAY_BATCH_SIZE", "100")),
        event_stream_maxlen=int(_env("EVENT_STREAM_MAXLEN", "10000")),
        workflow_callback_poll_seconds=float(_env("WORKFLOW_CALLBACK_POLL_SECONDS", "1")),
        workflow_callback_batch_size=int(_env("WORKFLOW_CALLBACK_BATCH_SIZE", "100")),
        workflow_callback_lease_seconds=int(_env("WORKFLOW_CALLBACK_LEASE_SECONDS", "30")),
        workflow_callback_default_timeout_seconds=int(
            _env("WORKFLOW_CALLBACK_DEFAULT_TIMEOUT_SECONDS", "10")
        ),
        workflow_callback_default_max_attempts=int(
            _env("WORKFLOW_CALLBACK_DEFAULT_MAX_ATTEMPTS", "5")
        ),
        workflow_callback_default_retry_delay_seconds=int(
            _env("WORKFLOW_CALLBACK_DEFAULT_RETRY_DELAY_SECONDS", "10")
        ),
        execution_event_retention_days=int(_env("EXECUTION_EVENT_RETENTION_DAYS", "30")),
        execution_event_retention_poll_seconds=float(
            _env("EXECUTION_EVENT_RETENTION_POLL_SECONDS", "300")
        ),
        execution_event_retention_batch_size=int(
            _env("EXECUTION_EVENT_RETENTION_BATCH_SIZE", "1000")
        ),
        execution_event_retention_grace_days=int(
            _env("EXECUTION_EVENT_RETENTION_GRACE_DAYS", "7")
        ),
        smtp_host=_env("SMTP_HOST", ""),
        smtp_port=int(_env("SMTP_PORT", "587")),
        smtp_username=_env("SMTP_USERNAME", ""),
        smtp_password=_env("SMTP_PASSWORD", ""),
        smtp_from=_env("SMTP_FROM", "agentcanvas@localhost"),
        smtp_starttls=_env("SMTP_STARTTLS", "true").lower() in {"1", "true", "yes"},
        invitation_expire_days=int(_env("INVITATION_EXPIRE_DAYS", "7")),
        usage_fact_lookback_days=int(_env("USAGE_FACT_LOOKBACK_DAYS", "3")),
        usage_fact_poll_seconds=float(_env("USAGE_FACT_POLL_SECONDS", "900")),
        org_deletion_grace_days=int(_env("ORG_DELETION_GRACE_DAYS", "30")),
        org_deletion_purge_batch_size=int(_env("ORG_DELETION_PURGE_BATCH_SIZE", "200")),
        org_deletion_purge_poll_seconds=float(_env("ORG_DELETION_PURGE_POLL_SECONDS", "60")),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings instance."""
    settings = load_settings()
    # Coordination-only processes use PostgreSQL/Redis and run on a read-only
    # root filesystem. They must not create local application directories.
    if settings.process_role not in {"scheduler", "relay"}:
        for directory in (
            settings.data_dir,
            settings.uploads_dir,
            settings.workspace_dir,
            settings.chroma_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
    return settings
