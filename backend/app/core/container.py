"""Service container / manual DI for app lifespan."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.auth import AuthService
from app.core.collaboration import CollaborationHub, build_collaboration_hub
from app.core.config import BACKEND_DIR, Settings, validate_runtime_settings
from app.core.mcp_policy import McpStdioPolicy
from app.core.observability import Observability
from app.core.oidc import OIDCClient
from app.core.refresh_tokens import RefreshTokenService
from app.core.resilience import ResilienceConfig, ResilienceRegistry
from app.core.sandbox import SandboxBackend
from app.core.secret_providers import SecretResolver, build_secret_provider_chain
from app.core.security import SecretBox, create_secret_box
from app.db.base import create_engine, create_session_factory, init_db
from app.db.migrations import upgrade_database
from app.db.models import (
    McpCatalogEntry,
    McpCatalogUpgradeHistory,
    McpCatalogVersion,
    ModelConfig,
)
from app.db.repositories import ExecutionQueueRepo, ExecutionRepo, ModelConfigRepo
from app.db.repositories.mcp import McpServerRepo
from app.engine.checkpoint import (
    CheckpointerUnavailable,
    close_checkpointer,
    ensure_waiting_approval_checkpoints,
    make_checkpointer,
)
from app.engine.compiler import WorkflowCompiler
from app.engine.event_relay import EventRelay
from app.engine.event_stream import ExecutionEventStream
from app.engine.events import EventBus
from app.engine.execution_event_retention import ExecutionEventRetentionScheduler
from app.engine.execution_lease import WorkerLease
from app.engine.execution_scheduler import ExecutionLeaseScheduler
from app.engine.executor import ExecutionEngine
from app.engine.online_source_scheduler import OnlineSourceScheduler
from app.engine.org_deletion_scheduler import OrgDeletionScheduler
from app.engine.usage_fact_scheduler import UsageFactScheduler
from app.engine.workflow_callback_dispatcher import WorkflowCallbackDispatcher
from app.engine.workflow_schedule_scheduler import WorkflowScheduleScheduler
from app.mcphub import McpManager
from app.memory import BaseMemoryStore, build_memory_store
from app.plugins import PluginRegistry
from app.rag import DocumentStorage, EmbeddingCoordinator, RagService, build_vector_store
from app.sandbox_runtime import select_runtime_sandbox
from app.schemas.events import ExecutionEvent
from app.services import seed_workflows
from app.services.evaluation_manager import EvaluationManager
from app.services.oidc_identity import OIDCIdentityService
from app.services.online_source_sync import OnlineSourceSyncService
from app.services.project_quotas import reconcile_project_quota_state
from app.services.template_seeds import seed_workflow_templates
from app.services.workflow_subworkflow_loader import build_subworkflow_loader

logger = logging.getLogger(__name__)

RESTART_RECOVERY_ERROR = "backend restarted before execution completed"


def _resilience_redis_url(settings: Settings) -> str:
    """Use shared resilience only in the explicit horizontal topology."""
    return settings.redis_url if settings.process_role != "all" else ""


@dataclass
class ServiceContainer:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    secret_box: SecretBox
    secret_resolver: SecretResolver
    auth_service: AuthService
    refresh_token_service: RefreshTokenService
    oidc_client: OIDCClient
    oidc_identity_service: OIDCIdentityService
    collaboration_hub: CollaborationHub
    mcp_stdio_policy: McpStdioPolicy
    event_bus: EventBus
    event_relay: EventRelay
    execution_event_stream: ExecutionEventStream | None
    execution_scheduler: ExecutionLeaseScheduler
    workflow_schedule_scheduler: WorkflowScheduleScheduler
    workflow_callback_dispatcher: WorkflowCallbackDispatcher
    execution_engine: ExecutionEngine
    evaluation_manager: EvaluationManager
    mcp_manager: McpManager
    rag_service: RagService
    online_source_sync: OnlineSourceSyncService
    online_source_scheduler: OnlineSourceScheduler
    execution_event_retention_scheduler: ExecutionEventRetentionScheduler
    usage_fact_scheduler: UsageFactScheduler
    org_deletion_scheduler: OrgDeletionScheduler
    memory_store: BaseMemoryStore
    observability: Observability
    resilience: ResilienceRegistry
    plugin_registry: PluginRegistry
    sandbox: SandboxBackend

    async def shutdown(self) -> None:
        from app.engine.checkpoint import close_checkpointer
        from app.memory import RedisMemoryStore

        # Drain in-flight executions first so their terminal events are
        # persisted and their status rows are written before we tear down
        # the DB engine and checkpointer they depend on (R-07 / U3-1).
        try:
            await self.evaluation_manager.shutdown()
        except Exception:
            logger.exception("evaluation manager shutdown failed; continuing cleanup")
        try:
            await self.workflow_schedule_scheduler.stop()
        except Exception:
            logger.exception("workflow schedule scheduler shutdown failed; continuing cleanup")
        try:
            await self.workflow_callback_dispatcher.stop()
        except Exception:
            logger.exception("workflow callback dispatcher shutdown failed; continuing cleanup")
        try:
            await self.execution_scheduler.stop()
        except Exception:
            logger.exception("execution scheduler shutdown failed; continuing cleanup")
        try:
            await self.execution_engine.shutdown()
        except Exception:
            logger.exception("execution engine shutdown failed; continuing cleanup")
        try:
            await self.online_source_scheduler.stop()
        except Exception:
            logger.exception("online source scheduler shutdown failed; continuing cleanup")
        try:
            await self.execution_event_retention_scheduler.stop()
        except Exception:
            logger.exception(
                "execution event retention scheduler shutdown failed; continuing cleanup"
            )
        try:
            await self.usage_fact_scheduler.stop()
        except Exception:
            logger.exception("usage fact scheduler shutdown failed; continuing cleanup")
        try:
            await self.org_deletion_scheduler.stop()
        except Exception:
            logger.exception("org deletion scheduler shutdown failed; continuing cleanup")
        try:
            await self.event_relay.stop()
        except Exception:
            logger.exception("execution event relay shutdown failed; continuing cleanup")
        try:
            await self.rag_service.shutdown()
        except Exception:
            logger.exception("RAG ingestion shutdown failed; continuing cleanup")
        try:
            await self.rag_service.vector_store.close()
        except Exception:
            logger.exception("vector store shutdown failed; continuing cleanup")
        await self.collaboration_hub.close()
        await self.mcp_manager.shutdown()
        await self.resilience.close()
        await self.oidc_client.aclose()
        await close_checkpointer()
        if isinstance(self.memory_store, RedisMemoryStore):
            await self.memory_store.aclose()
        await self.engine.dispose()
        self.observability.shutdown()


async def _persist_events(
    session_factory: async_sessionmaker[AsyncSession],
    events: list[ExecutionEvent],
    execution_leases: Mapping[str, WorkerLease] | None = None,
) -> None:
    async with session_factory() as session:
        repo = ExecutionRepo(session)
        queue = ExecutionQueueRepo(session)
        rows: list[dict[str, Any]] = []
        lease_validity: dict[str, bool] = {}
        for event in events:
            lease = execution_leases.get(event.execution_id) if execution_leases else None
            if lease is not None:
                valid = lease_validity.get(event.execution_id)
                if valid is None:
                    valid = await queue.owns_lease(
                        item_id=lease.item_id,
                        owner_id=lease.owner_id,
                        lease_generation=lease.generation,
                        lock=True,
                    )
                    lease_validity[event.execution_id] = valid
                if not valid:
                    logger.warning(
                        "dropping stale worker event for execution %s seq=%s",
                        event.execution_id,
                        event.seq,
                    )
                    continue
            try:
                ts = datetime.fromisoformat(event.ts)
            except ValueError:
                ts = datetime.now(UTC)
            rows.append(
                {
                    "execution_id": event.execution_id,
                    "seq": event.seq,
                    "event_type": event.event_type.value,
                    "node_id": event.node_id,
                    "payload_json": event.payload,
                    "ts": ts,
                }
            )
        if not rows:
            await session.rollback()
            return
        try:
            await repo.append_events(rows)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def seed_default_model(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    secret_box: SecretBox,
    secret_resolver: SecretResolver | None = None,
) -> None:
    """Ensure default chat and embedding model rows exist from .env."""
    resolver = secret_resolver or SecretResolver(secret_box)
    async with session_factory() as session:
        repo = ModelConfigRepo(session)
        existing = await repo.get("default")
        if existing is None:
            if not settings.openai_api_key:
                logger.warning(
                    "OPENAI_API_KEY not set; default chat model will use the mock provider"
                )
            existing = ModelConfig(
                id="default",
                name="Default OpenAI-Compatible",
                provider="openai_compat",
                model_name=settings.openai_model,
                base_url=settings.openai_base_url,
                api_key_encrypted=resolver.encrypt(settings.openai_api_key or ""),
                params_json={"temperature": 0.3},
                kind="chat",
                is_default=True,
            )
            await repo.upsert(existing)
            logger.info("seeded default chat model config (%s)", settings.openai_model)
        elif settings.openai_api_key:
            existing.api_key_encrypted = resolver.encrypt(settings.openai_api_key)
            existing.base_url = settings.openai_base_url
            existing.model_name = settings.openai_model

        embedding = await repo.get("default-embedding")
        if embedding is None:
            embedding = ModelConfig(
                id="default-embedding",
                name="Default Embedding",
                provider="openai_compat",
                model_name=settings.embedding_model,
                base_url=settings.openai_base_url,
                api_key_encrypted=resolver.encrypt(settings.openai_api_key or ""),
                params_json={},
                kind="embedding",
                is_default=True,
            )
            await repo.upsert(embedding)
            logger.info("seeded default embedding config (%s)", settings.embedding_model)
        elif settings.openai_api_key:
            embedding.api_key_encrypted = resolver.encrypt(settings.openai_api_key)
            embedding.base_url = settings.openai_base_url
            embedding.model_name = settings.embedding_model
        await session.commit()


async def seed_demo_mcp_servers(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    secret_box: SecretBox,
    secret_resolver: SecretResolver | None = None,
) -> None:
    """Register the built-in stdio demo servers without overwriting user edits."""
    resolver = secret_resolver or SecretResolver(secret_box)
    server_dir = BACKEND_DIR / "mcp_servers"
    seeds: tuple[dict[str, Any], ...] = (
        {
            "id": "demo-calculator",
            "name": "Calculator",
            "command": sys.executable,
            "args_json": [str(server_dir / "calculator.py")],
            "catalog_id": "builtin-calculator",
            "source_ref": "builtin://calculator",
            "network": [],
            "filesystem": [str(server_dir)],
        },
        {
            "id": "demo-filesystem",
            "name": "Workspace Files",
            "command": sys.executable,
            "args_json": [str(server_dir / "filesystem.py")],
            "env": {"AGENTCANVAS_WORKSPACE": str(settings.workspace_dir)},
            "catalog_id": "builtin-filesystem",
            "source_ref": "builtin://filesystem",
            "network": [],
            "filesystem": [str(server_dir), str(settings.workspace_dir)],
        },
        {
            "id": "demo-websearch",
            "name": "Web Search",
            "command": sys.executable,
            "args_json": [str(server_dir / "websearch.py")],
            "catalog_id": "builtin-websearch",
            "source_ref": "builtin://websearch",
            "network": ["api.duckduckgo.com"],
            "filesystem": [str(server_dir)],
        },
    )
    async with session_factory() as session:
        repo = McpServerRepo(session)
        created = 0
        for seed in seeds:
            data = dict(seed)
            env = data.pop("env", {})
            catalog_id = str(data.pop("catalog_id"))
            source_ref = str(data.pop("source_ref"))
            network = list(data.pop("network"))
            filesystem = list(data.pop("filesystem"))
            catalog_entry = await session.get(McpCatalogEntry, catalog_id)
            if catalog_entry is None:
                catalog_entry = McpCatalogEntry(
                    id=catalog_id,
                    name=str(data["name"]),
                    description="Built-in AgentCanvas MCP server",
                    source_url=source_ref,
                )
                session.add(catalog_entry)
                await session.flush()
            catalog_version_id = f"{catalog_id}-v1"
            catalog_version = await session.get(McpCatalogVersion, catalog_version_id)
            if catalog_version is None:
                catalog_version = McpCatalogVersion(
                    id=catalog_version_id,
                    entry_id=catalog_id,
                    version="1.0.0",
                    source_ref=source_ref,
                    manifest_json={
                        "transport": "stdio",
                        "permissions": {
                            "network": network,
                            "filesystem": filesystem,
                            "commands": ["python", "python.exe"],
                        },
                    },
                    status="approved",
                    approved_at=datetime.now(UTC),
                    approved_by="system:builtin",
                )
                session.add(catalog_version)
                session.add(
                    McpCatalogUpgradeHistory(
                        entry_id=catalog_id,
                        from_version=None,
                        to_version="1.0.0",
                        action="approved",
                        actor_key="system:builtin",
                        details_json={"source_ref": source_ref},
                    )
                )
                await session.flush()
            row = await repo.get(str(seed["id"]))
            if row is None:
                await repo.create(
                    transport="stdio",
                    enabled=True,
                    env_json={},
                    env_encrypted=resolver.encrypt_mapping(env),
                    catalog_entry_id=catalog_id,
                    catalog_version_id=catalog_version_id,
                    **data,
                )
                created += 1
            elif row.catalog_version_id is None:
                row.catalog_entry_id = catalog_id
                row.catalog_version_id = catalog_version_id
        await session.commit()
    if created:
        logger.info("seeded %d demo MCP servers", created)


async def recover_interrupted_executions(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Fail only untracked running executions without touching live leases."""
    async with session_factory() as session:
        recovered = await ExecutionRepo(session).recover_running(RESTART_RECOVERY_ERROR)
        await session.commit()
    if recovered:
        logger.warning("failed %d untracked execution(s) after process restart", recovered)
    return recovered


async def migrate_stored_secrets(
    session_factory: async_sessionmaker[AsyncSession],
    secret_resolver: SecretResolver | SecretBox,
) -> int:
    """Encrypt legacy plaintext secrets and verify existing ciphertext at startup."""
    resolver = (
        secret_resolver
        if isinstance(secret_resolver, SecretResolver)
        else SecretResolver(secret_resolver)
    )
    migrated = 0
    async with session_factory() as session:
        mcp_rows = await McpServerRepo(session).list()
        for mcp_row in mcp_rows:
            legacy_env = dict(mcp_row.env_json or {})
            legacy_headers = dict(mcp_row.headers_json or {})
            if mcp_row.env_encrypted:
                resolver.validate_stored_mapping(mcp_row.env_encrypted)
            elif legacy_env:
                mcp_row.env_encrypted = resolver.encrypt_mapping(legacy_env)
                migrated += 1
            if mcp_row.headers_encrypted:
                resolver.validate_stored_mapping(mcp_row.headers_encrypted)
            elif legacy_headers:
                mcp_row.headers_encrypted = resolver.encrypt_mapping(legacy_headers)
                migrated += 1
            mcp_row.env_json = {}
            mcp_row.headers_json = {}

        model_rows = await ModelConfigRepo(session).list()
        for model_row in model_rows:
            stored = model_row.api_key_encrypted or ""
            if stored.startswith("plain:"):
                model_row.api_key_encrypted = resolver.encrypt(resolver.secret_box.decrypt(stored))
                migrated += 1
            elif stored:
                resolver.validate_stored(stored)
        await session.commit()
    if migrated:
        logger.info("encrypted %d legacy secret payloads", migrated)
    return migrated


async def build_container(
    settings: Settings,
    *,
    observability: Observability | None = None,
    external_secret_resolver: Callable[[str], str] | None = None,
) -> ServiceContainer:
    validate_runtime_settings(settings)
    process_role = settings.process_role
    observability = observability or Observability(environment=settings.environment)
    secret_box = create_secret_box(settings)
    secret_resolver = SecretResolver(
        secret_box,
        build_secret_provider_chain(
            settings.docker_secret_dir,
            external_resolver=external_secret_resolver,
        ),
    )
    if settings.startup_migrations:
        await upgrade_database(settings)
    else:
        logger.info("startup migrations disabled; expecting the release migration step")
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await init_db(engine)
    if process_role == "all":
        await recover_interrupted_executions(session_factory)
        await reconcile_project_quota_state(session_factory)

    # Initialize and verify checkpoint durability before starting any background
    # services. This also catches an incomplete SQLite -> PostgreSQL cutover:
    # waiting approval rows may not outlive the old checkpoint file silently.
    try:
        checkpointer = await make_checkpointer(settings)
        await ensure_waiting_approval_checkpoints(session_factory, checkpointer)
    except CheckpointerUnavailable:
        await close_checkpointer()
        await engine.dispose()
        raise

    if process_role == "all":
        await migrate_stored_secrets(session_factory, secret_resolver)
    auth_service = AuthService(settings)
    refresh_token_service = RefreshTokenService(settings)
    oidc_client = OIDCClient(settings, secret_box)
    oidc_identity_service = OIDCIdentityService(settings.oidc.default_role)
    collaboration_hub = await build_collaboration_hub(
        presence_ttl_seconds=settings.collaboration_presence_ttl_seconds,
        lock_ttl_seconds=settings.collaboration_lock_ttl_seconds,
        redis_url=settings.redis_url,
    )
    mcp_stdio_policy = McpStdioPolicy.from_settings(settings)
    resilience = ResilienceRegistry(redis_url=_resilience_redis_url(settings))
    sandbox = select_runtime_sandbox(settings)
    if sandbox.name != "none":
        logger.info(
            "plugin/code sandbox backend: %s (enforces_permissions=%s, degraded=%s)",
            sandbox.name,
            sandbox.describe().get("enforces_permissions"),
            sandbox.describe().get("degraded"),
        )
    plugin_registry = PluginRegistry(
        settings.plugin_root_dir,
        max_input_bytes=settings.plugin_max_input_bytes,
        max_output_bytes=settings.plugin_max_output_bytes,
        max_events=settings.plugin_max_events,
        sandbox=sandbox,
        cpu_time_seconds=settings.sandbox_cpu_time_seconds,
        address_space_mb=settings.sandbox_address_space_mb,
        file_size_mb=settings.sandbox_file_size_mb,
        process_count=settings.sandbox_process_count,
        open_files=settings.sandbox_open_files,
    )
    loaded_plugins = plugin_registry.load()
    if loaded_plugins:
        logger.info(
            "loaded %d versioned node plugin(s): %s",
            len(loaded_plugins),
            ", ".join(item.manifest.node_type for item in loaded_plugins),
        )
    model_resilience_config = ResilienceConfig(
        max_attempts=settings.model_resilience_max_attempts,
        failure_threshold=settings.model_resilience_failure_threshold,
        reset_timeout_seconds=settings.model_resilience_reset_seconds,
        half_open_probe_timeout_seconds=settings.default_timeout_seconds,
        retry_budget=settings.model_resilience_retry_budget,
        retry_window_seconds=settings.resilience_retry_window_seconds,
    )
    mcp_resilience_config = ResilienceConfig(
        max_attempts=settings.mcp_resilience_max_attempts,
        failure_threshold=settings.mcp_resilience_failure_threshold,
        reset_timeout_seconds=settings.mcp_resilience_reset_seconds,
        half_open_probe_timeout_seconds=settings.mcp_timeout_seconds,
        retry_budget=settings.mcp_resilience_retry_budget,
        retry_window_seconds=settings.resilience_retry_window_seconds,
    )
    event_bus = EventBus(
        flush_interval=settings.event_flush_interval_ms / 1000,
        observer=observability.observe_event,
        queue_observer=observability.observe_event_queue,
    )
    execution_leases: dict[str, WorkerLease] = {}
    event_relay = EventRelay(
        session_factory,
        redis_url=settings.redis_url,
        poll_seconds=settings.event_relay_poll_seconds,
        batch_size=settings.event_relay_batch_size,
        stream_maxlen=settings.event_stream_maxlen,
    )
    execution_scheduler = ExecutionLeaseScheduler(
        session_factory,
        poll_seconds=settings.execution_scheduler_poll_seconds,
        batch_size=settings.execution_scheduler_batch_size,
        max_attempts=settings.execution_lease_max_attempts,
    )
    workflow_schedule_scheduler = WorkflowScheduleScheduler(
        session_factory,
        poll_seconds=settings.workflow_schedule_poll_seconds,
        batch_size=settings.workflow_schedule_batch_size,
    )
    workflow_callback_dispatcher = WorkflowCallbackDispatcher(
        session_factory,
        secret_resolver,
        poll_seconds=settings.workflow_callback_poll_seconds,
        batch_size=settings.workflow_callback_batch_size,
        lease_seconds=settings.workflow_callback_lease_seconds,
        default_timeout_seconds=settings.workflow_callback_default_timeout_seconds,
        default_max_attempts=settings.workflow_callback_default_max_attempts,
        default_retry_delay_seconds=settings.workflow_callback_default_retry_delay_seconds,
        owner_id=settings.instance_id,
    )

    async def persist(events: list[ExecutionEvent]) -> None:
        await _persist_events(session_factory, events, execution_leases)
        # Wake the relay after durable rows land so Redis tails stay low-latency.
        event_relay.kick()
        workflow_callback_dispatcher.kick()

    event_bus.set_persist(persist)
    if process_role == "all":
        await event_relay.start()
        workflow_callback_dispatcher.start()
    elif process_role == "api":
        await event_relay.start(run_loop=False)

    mcp_manager = McpManager(
        session_factory,
        secret_box,
        mcp_stdio_policy,
        secret_resolver=secret_resolver,
        connect_timeout=settings.mcp_connect_timeout_seconds,
        request_timeout=settings.mcp_timeout_seconds,
        idle_timeout=settings.mcp_idle_timeout_seconds,
        reaper_interval=settings.mcp_reaper_interval_seconds,
        observability=observability,
        resilience=resilience,
        resilience_config=mcp_resilience_config,
    )
    mcp_manager.start_reaper()

    if process_role == "all":
        await seed_default_model(session_factory, settings, secret_box, secret_resolver)
        await seed_demo_mcp_servers(session_factory, settings, secret_box, secret_resolver)
        seeded = await seed_workflows(session_factory)
        if seeded:
            logger.info("seeded %d demo workflows", seeded)
        seeded_templates = await seed_workflow_templates(session_factory)
        if seeded_templates:
            logger.info("seeded %d official workflow templates", seeded_templates)

    vector_store = build_vector_store(settings, session_factory)
    rag_service = RagService(
        session_factory,
        DocumentStorage(settings.uploads_dir, settings.rag_max_upload_bytes),
        EmbeddingCoordinator(session_factory, settings, secret_box, secret_resolver),
        vector_store,
        ingest_timeout_seconds=settings.ingest_timeout_seconds,
        observability=observability,
        secret_resolver=secret_resolver,
        settings=settings,
    )
    if process_role == "all":
        recovered_documents = await rag_service.recover_processing_documents()
        if recovered_documents:
            logger.warning(
                "marked %d interrupted document ingestions as failed", recovered_documents
            )

    online_source_sync = OnlineSourceSyncService(
        session_factory=session_factory,
        rag_service=rag_service,
        storage=DocumentStorage(settings.uploads_dir, settings.rag_max_upload_bytes),
        lease_seconds=settings.online_source_sync_lease_seconds,
    )
    online_source_scheduler = OnlineSourceScheduler(
        session_factory,
        online_source_sync,
        owner_id=settings.instance_id,
        poll_seconds=settings.online_source_sync_poll_seconds,
        batch_size=settings.online_source_sync_batch_size,
        lease_seconds=settings.online_source_sync_lease_seconds,
    )
    execution_event_retention_scheduler = ExecutionEventRetentionScheduler(
        session_factory,
        retention_days=settings.execution_event_retention_days,
        grace_days=settings.execution_event_retention_grace_days,
        batch_size=settings.execution_event_retention_batch_size,
        poll_seconds=settings.execution_event_retention_poll_seconds,
        owner_id=settings.instance_id,
    )
    usage_fact_scheduler = UsageFactScheduler(
        session_factory,
        lookback_days=settings.usage_fact_lookback_days,
        poll_seconds=settings.usage_fact_poll_seconds,
        owner_id=settings.instance_id,
    )
    org_deletion_scheduler = OrgDeletionScheduler(
        session_factory,
        settings,
        rag_service=rag_service,
        batch_size=settings.org_deletion_purge_batch_size,
        poll_seconds=settings.org_deletion_purge_poll_seconds,
        owner_id=settings.instance_id,
    )
    memory_store = await build_memory_store(settings, session_factory)

    from app.services.chat_session_variables import ChatSessionVariableStore

    session_variable_store = ChatSessionVariableStore(session_factory)

    execution_engine = ExecutionEngine(
        session_factory=session_factory,
        event_bus=event_bus,
        secret_box=secret_box,
        secret_resolver=secret_resolver,
        compiler=WorkflowCompiler(),
        settings=settings,
        mcp_manager=mcp_manager,
        rag_service=rag_service,
        memory_store=memory_store,
        observability=observability,
        resilience=resilience,
        resilience_config=model_resilience_config,
        execution_leases=execution_leases,
        worker_owner_id=(
            settings.execution_worker_id
            or (settings.instance_id if process_role == "worker" else None)
        ),
        start_worker=process_role in {"all", "worker"},
        workflow_callback_dispatcher=workflow_callback_dispatcher,
        sandbox=sandbox,
        subworkflow_loader=build_subworkflow_loader(session_factory),
        session_variable_store=session_variable_store,
    )
    evaluation_manager = EvaluationManager(session_factory, execution_engine)
    if process_role == "all":
        recovered_evaluations = await evaluation_manager.recover_interrupted()
        if recovered_evaluations:
            logger.warning("marked %d interrupted evaluation runs as failed", recovered_evaluations)
        if settings.environment != "test":
            execution_scheduler.start()
            workflow_schedule_scheduler.start()
    if process_role in {"all", "worker"} and settings.environment != "test":
        # Test apps run under SQLite with TestClient; the background schedulers
        # poll/write the same DB file as the request path and are the cause of
        # intermittent "database is locked" flakes. Dedicated scheduler tests
        # instantiate their own schedulers, so skipping here loses no coverage.
        online_source_scheduler.start()
        execution_event_retention_scheduler.start()
        usage_fact_scheduler.start()
        org_deletion_scheduler.start()

    return ServiceContainer(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        secret_box=secret_box,
        secret_resolver=secret_resolver,
        auth_service=auth_service,
        refresh_token_service=refresh_token_service,
        oidc_client=oidc_client,
        oidc_identity_service=oidc_identity_service,
        collaboration_hub=collaboration_hub,
        mcp_stdio_policy=mcp_stdio_policy,
        event_bus=event_bus,
        event_relay=event_relay,
        execution_event_stream=event_relay.event_stream,
        execution_scheduler=execution_scheduler,
        workflow_schedule_scheduler=workflow_schedule_scheduler,
        workflow_callback_dispatcher=workflow_callback_dispatcher,
        execution_engine=execution_engine,
        evaluation_manager=evaluation_manager,
        mcp_manager=mcp_manager,
        rag_service=rag_service,
        online_source_sync=online_source_sync,
        online_source_scheduler=online_source_scheduler,
        execution_event_retention_scheduler=execution_event_retention_scheduler,
        usage_fact_scheduler=usage_fact_scheduler,
        org_deletion_scheduler=org_deletion_scheduler,
        memory_store=memory_store,
        observability=observability,
        resilience=resilience,
        plugin_registry=plugin_registry,
        sandbox=sandbox,
    )
