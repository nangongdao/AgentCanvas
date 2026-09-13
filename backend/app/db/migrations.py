"""Alembic startup integration and legacy database adoption."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from alembic.config import Config
from sqlalchemy import inspect

from alembic import command
from app.core.config import BACKEND_DIR, Settings, validate_runtime_settings
from app.db.base import create_engine

BASELINE_REVISION = "0001_p0_p3"
CURRENT_REVISION = "2a2231226aa0"
BASELINE_TABLES = frozenset(
    {"workflows", "executions", "execution_events", "model_configs", "mcp_servers"}
)
P4_TABLES = frozenset({"knowledge_bases", "documents", "embedding_cache"})
P5_TABLES = frozenset({"chat_sessions", "chat_messages"})
U3_TABLES = frozenset({"ingest_jobs"})
D1_TABLES = frozenset({"workflow_versions", "workflow_templates"})
D2_TABLES = frozenset(
    {
        "evaluation_datasets",
        "evaluation_dataset_versions",
        "evaluation_runs",
        "evaluation_case_results",
        "evaluation_comparisons",
    }
)
D2_COST_TABLES = frozenset({"cost_alerts"})
D3_IDENTITY_TABLES = frozenset({"users", "sessions"})
D3_TENANT_TABLES = frozenset({"organizations", "projects", "memberships"})
D3_SERVICE_TABLES = frozenset({"service_accounts", "api_tokens"})
WORKFLOW_API_TABLES = frozenset({"workflow_api_publications"})
D3_FEDERATED_IDENTITY_TABLES = frozenset({"refresh_tokens", "oidc_identities"})
D3_IDENTITY_HARDENING_TABLES = frozenset({"identity_bootstrap"})
D3_COLLABORATION_TABLES = frozenset({"workflow_comments", "workflow_reviews"})
D3_AUDIT_TABLES = frozenset({"audit_logs"})
D3_QUOTA_TABLES = frozenset(
    {
        "project_quotas",
        "project_quota_counters",
        "project_quota_period_usage",
        "project_quota_reservations",
    }
)
D4_CATALOG_TABLES = frozenset(
    {"mcp_catalog_entries", "mcp_catalog_versions", "mcp_catalog_upgrade_history"}
)
I1_QUEUE_TABLES = frozenset({"execution_queue_items"})
I1_EVENT_RELAY_TABLES = frozenset({"execution_event_relay_cursors"})
I1_VECTOR_TABLES = frozenset({"document_chunks"})
TRIGGER_TABLES = frozenset({"webhook_triggers"})
SCHEDULE_TABLES = frozenset({"workflow_schedules"})
CALLBACK_TABLES = frozenset(
    {"workflow_callbacks", "workflow_callback_deliveries", "workflow_callback_cursors"}
)
APP_TABLES = frozenset({"apps"})
C3_4_TABLES = frozenset({"chat_message_feedback", "chat_session_variables"})
HYBRID_TABLES: frozenset[str] = frozenset()  # 0035 adds columns only
CHUNK_STRATEGY_TABLES: frozenset[str] = frozenset()  # 0036 adds columns only
ONLINE_SOURCE_TABLES: frozenset[str] = frozenset({"online_sources"})
USAGE_FACT_TABLES: frozenset[str] = frozenset({"usage_daily_facts"})
ORG_PLAN_TABLES: frozenset[str] = frozenset({"org_plans"})
PLATFORM_ADMIN_TABLES: frozenset[str] = frozenset({"platform_announcements"})
ORG_INVITATION_TABLES: frozenset[str] = frozenset({"organization_invitations"})
# 0046 adds deletion columns to organizations only.
ORG_DELETION_TABLES: frozenset[str] = frozenset()
# 0047 adds the evaluation_policy column to workflows only.
EVALUATION_POLICY_TABLES: frozenset[str] = frozenset()
CURRENT_TABLES = (
    BASELINE_TABLES
    | P4_TABLES
    | P5_TABLES
    | U3_TABLES
    | D1_TABLES
    | D2_TABLES
    | D2_COST_TABLES
    | D3_IDENTITY_TABLES
    | D3_TENANT_TABLES
    | D3_SERVICE_TABLES
    | WORKFLOW_API_TABLES
    | D3_FEDERATED_IDENTITY_TABLES
    | D3_IDENTITY_HARDENING_TABLES
    | D3_COLLABORATION_TABLES
    | D3_AUDIT_TABLES
    | D3_QUOTA_TABLES
    | D4_CATALOG_TABLES
    | I1_QUEUE_TABLES
    | I1_EVENT_RELAY_TABLES
    | I1_VECTOR_TABLES
    | TRIGGER_TABLES
    | SCHEDULE_TABLES
    | CALLBACK_TABLES
    | APP_TABLES
    | C3_4_TABLES
    | HYBRID_TABLES
    | CHUNK_STRATEGY_TABLES
    | ONLINE_SOURCE_TABLES
    | USAGE_FACT_TABLES
    | ORG_PLAN_TABLES
    | PLATFORM_ADMIN_TABLES
    | ORG_INVITATION_TABLES
    | ORG_DELETION_TABLES
    | EVALUATION_POLICY_TABLES
)

# Alembic installs module-level EnvironmentContext proxies while a command runs.
_ALEMBIC_COMMAND_LOCK = threading.Lock()


def _alembic_config(settings: Settings) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.effective_database_url.replace("%", "%%"))
    config.set_main_option(
        "agentcanvas.migration_lock_timeout_seconds",
        str(settings.database_migration_lock_timeout_seconds),
    )
    return config


def _run_command(settings: Settings, action: str) -> None:
    with _ALEMBIC_COMMAND_LOCK:
        config = _alembic_config(settings)
        if action == "stamp-baseline":
            command.stamp(config, BASELINE_REVISION)
            return
        if action == "check":
            command.check(config)
            return
        command.upgrade(config, "head")


async def _table_names(settings: Settings) -> set[str]:
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            names = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_table_names()
            )
            return set(names)
    finally:
        await engine.dispose()


async def upgrade_database(settings: Settings) -> None:
    """Upgrade an empty/versioned database or adopt a complete P0-P3 legacy schema."""
    validate_runtime_settings(settings)
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    table_names = await _table_names(settings)
    app_tables = table_names - {"alembic_version", "sqlite_sequence"}

    if "alembic_version" not in table_names and app_tables:
        missing = BASELINE_TABLES - app_tables
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise RuntimeError(
                "cannot adopt partially initialized database; "
                f"missing baseline tables: {missing_text}"
            )
        await asyncio.to_thread(_run_command, settings, "stamp-baseline")

    await asyncio.to_thread(_run_command, settings, "upgrade")


async def check_database_schema(settings: Settings) -> None:
    """Fail when the migrated database schema drifts from ORM metadata."""
    validate_runtime_settings(settings)
    await asyncio.to_thread(_run_command, settings, "check")
