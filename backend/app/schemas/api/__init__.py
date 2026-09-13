"""Pydantic request/response models for HTTP APIs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app import __version__
from app.schemas.app import AppCreate as AppCreate
from app.schemas.app import AppIssueOut as AppIssueOut
from app.schemas.app import AppOut as AppOut
from app.schemas.app import AppRuntimeOut as AppRuntimeOut
from app.schemas.app import AppRuntimeSend as AppRuntimeSend
from app.schemas.app import AppRuntimeSessionCreate as AppRuntimeSessionCreate
from app.schemas.app import AppStatus as AppStatus
from app.schemas.app import AppType as AppType
from app.schemas.app import AppUpdate as AppUpdate
from app.schemas.app import AppUsageDayOut as AppUsageDayOut
from app.schemas.app import AppUsageOut as AppUsageOut
from app.schemas.app import AppVersionSwitch as AppVersionSwitch
from app.schemas.app import AppVisibility as AppVisibility
from app.schemas.app import InputFieldDef as InputFieldDef
from app.schemas.auth import AuthLogin as AuthLogin
from app.schemas.auth import AuthPreferencesUpdate as AuthPreferencesUpdate
from app.schemas.auth import AuthRegister as AuthRegister
from app.schemas.auth import AuthSessionOut as AuthSessionOut
from app.schemas.auth import UserOut as UserOut
from app.schemas.cost_governance import CostAlertOut as CostAlertOut
from app.schemas.cost_governance import CostGovernanceOut as CostGovernanceOut
from app.schemas.models import DiscoveredModelOut as DiscoveredModelOut
from app.schemas.models import ModelConfigCreate as ModelConfigCreate
from app.schemas.models import ModelConfigOut as ModelConfigOut
from app.schemas.models import ModelConfigUpdate as ModelConfigUpdate
from app.schemas.models import ModelDiscoveryOut as ModelDiscoveryOut
from app.schemas.models import ModelDiscoveryRequest as ModelDiscoveryRequest
from app.schemas.service_accounts import ApiTokenIssue as ApiTokenIssue
from app.schemas.service_accounts import ApiTokenIssueOut as ApiTokenIssueOut
from app.schemas.service_accounts import ApiTokenOut as ApiTokenOut
from app.schemas.service_accounts import ServiceAccountCreate as ServiceAccountCreate
from app.schemas.service_accounts import ServiceAccountOut as ServiceAccountOut
from app.schemas.service_accounts import ServiceAccountUpdate as ServiceAccountUpdate
from app.schemas.tenancy import MembershipCreate as MembershipCreate
from app.schemas.tenancy import MembershipOut as MembershipOut
from app.schemas.tenancy import MembershipUpdate as MembershipUpdate
from app.schemas.tenancy import OrganizationCreate as OrganizationCreate
from app.schemas.tenancy import OrganizationOut as OrganizationOut
from app.schemas.tenancy import ProjectCreate as ProjectCreate
from app.schemas.tenancy import ProjectOut as ProjectOut
from app.schemas.webhooks import WebhookInvokeOut as WebhookInvokeOut
from app.schemas.webhooks import WebhookTriggerCreate as WebhookTriggerCreate
from app.schemas.webhooks import WebhookTriggerIssueOut as WebhookTriggerIssueOut
from app.schemas.webhooks import WebhookTriggerOut as WebhookTriggerOut
from app.schemas.webhooks import WebhookTriggerUpdate as WebhookTriggerUpdate
from app.schemas.workflow_api import WorkflowApiInvokeOut as WorkflowApiInvokeOut
from app.schemas.workflow_api import WorkflowApiIssueOut as WorkflowApiIssueOut
from app.schemas.workflow_api import WorkflowApiPublicationCreate as WorkflowApiPublicationCreate
from app.schemas.workflow_api import WorkflowApiPublicationOut as WorkflowApiPublicationOut
from app.schemas.workflow_callbacks import WorkflowCallbackCreate as WorkflowCallbackCreate
from app.schemas.workflow_callbacks import (
    WorkflowCallbackDeliveryOut as WorkflowCallbackDeliveryOut,
)
from app.schemas.workflow_callbacks import WorkflowCallbackIssueOut as WorkflowCallbackIssueOut
from app.schemas.workflow_callbacks import WorkflowCallbackOut as WorkflowCallbackOut
from app.schemas.workflow_callbacks import WorkflowCallbackUpdate as WorkflowCallbackUpdate
from app.schemas.workflow_schedules import WorkflowScheduleCreate as WorkflowScheduleCreate
from app.schemas.workflow_schedules import WorkflowScheduleOut as WorkflowScheduleOut
from app.schemas.workflow_schedules import WorkflowScheduleUpdate as WorkflowScheduleUpdate


class WorkflowCreate(BaseModel):
    name: str = "未命名工作流"
    description: str = ""
    dsl: dict[str, Any]
    project_id: str | None = Field(default=None, min_length=1, max_length=32)


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    dsl: dict[str, Any] | None = None
    version: int | None = None
    change_summary: str = Field(default="", max_length=2000)


class WorkflowOut(BaseModel):
    id: str
    name: str
    description: str
    dsl: dict[str, Any]
    version: int
    is_archived: bool
    project_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CopilotDraftRequest(BaseModel):
    """A natural-language request for a workflow draft (AI Copilot).

    ``base_dsl`` is the current canvas when the user wants the draft to modify
    an existing workflow rather than start from nothing; ``model_config_id``
    selects which chat model does the planning.
    """

    prompt: str = Field(min_length=1, max_length=4_000)
    project_id: str | None = Field(default=None, min_length=1, max_length=32)
    base_dsl: dict[str, Any] | None = None
    model_config_id: str = Field(default="default", min_length=1, max_length=64)


class CopilotUsageOut(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class CopilotDraftOut(BaseModel):
    """A drafted workflow plus the validator's verdict on it.

    ``valid`` is the graph verdict, not the schema verdict: a document that
    matches the DSL schema but fails reachability/cycle checks still returns
    200 with ``valid=False`` and the errors, so the user can repair it instead
    of losing the draft.
    """

    dsl: dict[str, Any]
    name: str
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    attempts: int = 1
    provider: str
    model: str
    usage: CopilotUsageOut = Field(default_factory=CopilotUsageOut)


class DebugRunOptions(BaseModel):
    """C2-7 debug-run controls carried through start and resume.

    ``breakpoints`` is the set of node ids to pause after; ``single_step``
    treats every eligible linear node as a breakpoint. The editor supplies
    these on a debug run and again on each resume so the pause points persist
    across the whole run without a dedicated DB column.
    """

    breakpoints: list[str] = Field(default_factory=list, max_length=128)
    single_step: bool = False


class ExecutionCreate(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    debug: DebugRunOptions | None = None


class ExecutionResume(BaseModel):
    decision: dict[str, Any] = Field(default_factory=dict)
    debug: DebugRunOptions | None = None


class ExecutionRerun(BaseModel):
    node_id: str | None = Field(default=None, min_length=1, max_length=64)


class NodeDryRunRequest(BaseModel):
    """Mock-input single-node execution request (C2-6).

    The caller supplies the live node config from the canvas and mock input
    values. The run executes synchronously and is never persisted to the
    executions table or event log.
    """

    node_id: str = Field(min_length=1, max_length=64)
    node_type: str = Field(min_length=1, max_length=64)
    node_config: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)


class NodeDryRunEvent(BaseModel):
    event_type: str
    node_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class NodeDryRunResponse(BaseModel):
    node_id: str
    output: Any = None
    events: list[NodeDryRunEvent] = Field(default_factory=list)
    final_output: Any = None


class ExecutionOut(BaseModel):
    id: str
    workflow_id: str
    workflow_version_id: str | None = None
    workflow_version_number: int | None = None
    status: str
    trigger_source: Literal["manual", "webhook", "schedule", "api"] = "manual"
    input_json: dict[str, Any] = Field(default_factory=dict)
    output_json: dict[str, Any] | None = None
    error: str | None = None
    parent_execution_id: str | None = None
    rerun_from_node_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class MetaOut(BaseModel):
    app: str = "agentcanvas"
    version: str = __version__
    memory_backend: str = "none"
    vector_backend: str = "chroma"
    database: str = "sqlite"
    environment: str = "development"
    auth_enabled: bool = False
    oidc_enabled: bool = False
    sandbox_backend: str = "process_cleanup"
    sandbox_degraded: bool = True


McpTransport = Literal["stdio", "sse", "streamable_http"]


class McpCatalogPermissions(BaseModel):
    """Explicit outbound and local capabilities declared by a catalog version."""

    network: list[str] = Field(default_factory=list, max_length=32)
    filesystem: list[str] = Field(default_factory=list, max_length=32)
    commands: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_unique_nonblank_values(self) -> McpCatalogPermissions:
        for field_name in ("network", "filesystem", "commands"):
            values = getattr(self, field_name)
            if any(not value.strip() or len(value) > 500 for value in values):
                raise ValueError(f"catalog {field_name} permissions must be bounded and nonblank")
            if len({value.casefold() for value in values}) != len(values):
                raise ValueError(f"catalog {field_name} permissions must be unique")
        return self


class McpCatalogManifest(BaseModel):
    transport: McpTransport
    permissions: McpCatalogPermissions = Field(default_factory=McpCatalogPermissions)


CatalogVersionStatus = Literal["draft", "approved", "superseded", "revoked"]


class McpCatalogVersionCreate(BaseModel):
    version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
    source_ref: str = Field(min_length=1, max_length=500)
    manifest: McpCatalogManifest


class McpCatalogEntryCreate(McpCatalogVersionCreate):
    id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    source_url: str | None = Field(default=None, max_length=500)


class McpCatalogVersionOut(BaseModel):
    id: str
    entry_id: str
    version: str
    source_ref: str
    manifest: McpCatalogManifest
    status: CatalogVersionStatus
    approved_at: datetime | None = None
    approved_by: str | None = None
    created_at: datetime | None = None


class McpCatalogEntryOut(BaseModel):
    id: str
    name: str
    description: str = ""
    source_url: str | None = None
    versions: list[McpCatalogVersionOut] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class McpCatalogHistoryOut(BaseModel):
    id: str
    entry_id: str
    from_version: str | None = None
    to_version: str
    action: str
    actor_key: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class McpCatalogVersionDiffOut(BaseModel):
    entry_id: str
    from_version_id: str | None = None
    from_version: str | None = None
    to_version_id: str
    to_version: str
    source_changed: bool
    transport_changed: bool
    permission_added: McpCatalogPermissions = Field(default_factory=McpCatalogPermissions)
    permission_removed: McpCatalogPermissions = Field(default_factory=McpCatalogPermissions)
    changed_fields: list[str] = Field(default_factory=list)


class McpCatalogRolloutRequest(BaseModel):
    version_id: str = Field(min_length=1, max_length=64)
    server_ids: list[str] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def validate_server_ids(self) -> McpCatalogRolloutRequest:
        if any(not server_id.strip() for server_id in self.server_ids):
            raise ValueError("rollout server IDs must be nonblank")
        if len(set(self.server_ids)) != len(self.server_ids):
            raise ValueError("rollout server IDs must be unique")
        return self


class McpCatalogRolloutServerOut(BaseModel):
    server_id: str
    name: str
    project_id: str | None = None
    current_version_id: str | None = None
    current_version: str | None = None
    compatible: bool
    reason: str | None = None


class McpCatalogRolloutPreviewOut(BaseModel):
    entry_id: str
    version_id: str
    version: str
    servers: list[McpCatalogRolloutServerOut] = Field(default_factory=list)
    compatible_count: int
    incompatible_count: int


class McpCatalogRolloutOut(BaseModel):
    entry_id: str
    version_id: str
    version: str
    updated_server_ids: list[str] = Field(default_factory=list)
    unchanged_server_ids: list[str] = Field(default_factory=list)
    skipped: list[McpCatalogRolloutServerOut] = Field(default_factory=list)


class McpCatalogBindingUpdate(BaseModel):
    version_id: str | None = Field(default=None, min_length=1, max_length=64)


class McpServerCreate(BaseModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=120)
    transport: McpTransport = "stdio"
    command: str | None = Field(default=None, max_length=500)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = Field(default=None, max_length=500)
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_transport_target(self) -> McpServerCreate:
        if self.transport == "stdio" and not (self.command or "").strip():
            raise ValueError("stdio transport requires command")
        if self.transport != "stdio" and not (self.url or "").strip():
            raise ValueError(f"{self.transport} transport requires url")
        return self


class McpServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    transport: McpTransport | None = None
    command: str | None = Field(default=None, max_length=500)
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = Field(default=None, max_length=500)
    headers: dict[str, str] | None = None
    enabled: bool | None = None


class McpToolOut(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class McpServerOut(BaseModel):
    id: str
    project_id: str | None = None
    name: str
    transport: McpTransport
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    env_sources: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    headers_sources: dict[str, str] = Field(default_factory=dict)
    enabled: bool
    catalog_entry_id: str | None = None
    catalog_version_id: str | None = None
    tools: list[McpToolOut] = Field(default_factory=list)
    tools_cached_at: datetime | None = None
    last_status: str | None = None
    connected: bool = False
    created_at: datetime | None = None


class McpTestOut(BaseModel):
    status: Literal["connected"] = "connected"
    tools: list[McpToolOut] = Field(default_factory=list)


class KnowledgeBaseCreate(BaseModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    embedding_model_id: str = Field(default="default-embedding", min_length=1, max_length=64)
    chunk_size: int = Field(default=1000, ge=128, le=8000)
    chunk_overlap: int = Field(default=150, ge=0, le=2000)
    # C4-2 chunking strategy.
    split_strategy: SplitStrategy = "window"
    parent_chunk: bool = False
    # C4-1 hybrid retrieval / rerank configuration.
    retrieval_mode: RetrievalMode = "vector"
    rerank_enabled: bool = False
    rerank_model_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_chunk_window(self) -> KnowledgeBaseCreate:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self


RetrievalMode = Literal["vector", "hybrid"]
SplitStrategy = Literal["window", "recursive", "heading"]


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    embedding_model_id: str | None = Field(default=None, min_length=1, max_length=64)
    chunk_size: int | None = Field(default=None, ge=128, le=8000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=2000)
    # C4-2 chunking strategy.
    split_strategy: SplitStrategy | None = None
    parent_chunk: bool | None = None
    # C4-1 hybrid retrieval / rerank configuration.
    retrieval_mode: RetrievalMode | None = None
    rerank_enabled: bool | None = None
    rerank_model_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_chunk_window(self) -> KnowledgeBaseUpdate:
        if (
            self.chunk_size is not None
            and self.chunk_overlap is not None
            and self.chunk_overlap >= self.chunk_size
        ):
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self


class KnowledgeBaseOut(BaseModel):
    id: str
    project_id: str | None = None
    name: str
    description: str = ""
    embedding_model_id: str
    chunk_size: int
    chunk_overlap: int
    split_strategy: SplitStrategy = "window"
    parent_chunk: bool = False
    retrieval_mode: RetrievalMode = "vector"
    rerank_enabled: bool = False
    rerank_model_id: str | None = None
    document_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


DocumentStatus = Literal["pending", "processing", "ready", "failed"]


class KnowledgeDocumentOut(BaseModel):
    id: str
    kb_id: str
    filename: str
    mime_type: str
    size_bytes: int
    content_sha256: str
    status: DocumentStatus
    chunk_count: int = 0
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DocumentIngestOut(BaseModel):
    document: KnowledgeDocumentOut
    job_id: str | None = None
    job_status: Literal["queued", "running", "succeeded", "failed", "cancelled"] | None = None
    cache_hits: int = 0
    cache_misses: int = 0


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=20_000)
    top_k: int = Field(default=5, ge=1, le=50)
    score_threshold: float = Field(default=0.2, ge=0, le=1)
    # C4-1: return per-hit vector/keyword/fused/rerank score components.
    include_scores: bool = False


class RetrievalHitOut(BaseModel):
    id: str
    document_id: str
    filename: str
    chunk_index: int
    page: int | None = None
    text: str
    score: float
    citation: str


class ChunkPreviewRequest(BaseModel):
    """C4-2: preview how a sample would chunk without rebuilding the index."""

    text: str = Field(min_length=1, max_length=200_000)
    chunk_size: int = Field(default=1000, ge=128, le=8000)
    chunk_overlap: int = Field(default=150, ge=0, le=2000)
    split_strategy: SplitStrategy = "window"
    parent_chunk: bool = False


class RetrievalChunkPreviewOut(BaseModel):
    index: int
    text: str
    start_char: int
    end_char: int
    page: int | None = None
    parent_id: int | None = None
    parent_text: str | None = None


class ChunkPreviewOut(BaseModel):
    chunks: list[RetrievalChunkPreviewOut]
    chunk_count: int


OnlineSourceStatus = Literal["pending", "syncing", "ready", "failed"]


class OnlineSourceCreate(BaseModel):
    """Create an online (URL) knowledge source (C4-3)."""

    url: str = Field(min_length=1, max_length=1000)
    max_pages: int = Field(default=1, ge=1, le=50)
    depth: int = Field(default=0, ge=0, le=3)
    sync_interval_minutes: int | None = Field(default=None, ge=5, le=10080)


class OnlineSourceUpdate(BaseModel):
    max_pages: int | None = Field(default=None, ge=1, le=50)
    depth: int | None = Field(default=None, ge=0, le=3)
    sync_interval_minutes: int | None = Field(default=None, ge=5, le=10080)


class OnlineSourceOut(BaseModel):
    id: str
    kb_id: str
    url: str
    document_id: str | None = None
    max_pages: int
    depth: int
    content_sha256: str | None = None
    status: OnlineSourceStatus
    error: str | None = None
    last_synced_at: datetime | None = None
    sync_interval_minutes: int | None = None
    next_sync_at: datetime | None = None
    sync_started_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RetrievalScoreDetailOut(BaseModel):
    """Per-hit score components for the retrieval debug view (C4-1/C4-4)."""

    hit_id: str
    retrieval_mode: str
    vector_score: float | None = None
    keyword_score: float | None = None
    fused_score: float | None = None
    rerank_score: float | None = None
    rerank_applied: bool = False


class RetrievalOut(BaseModel):
    query: str
    hits: list[RetrievalHitOut] = Field(default_factory=list)
    retrieval_mode: str = "vector"
    rerank_applied: bool = False
    score_detail: list[RetrievalScoreDetailOut] | None = None


# ---- P5 Chat ----


class ChatSessionCreate(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=200)
    workflow_id: str | None = Field(default=None, min_length=1, max_length=32)
    model_config_id: str | None = Field(default=None, min_length=1, max_length=64)
    inputs: dict[str, Any] = Field(default_factory=dict)


class ChatMessageOut(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    node_ref: str | None = None
    execution_id: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    # End-user feedback rating on assistant messages ("positive"/"negative");
    # None when no feedback row exists (C3-4).
    feedback_rating: str | None = None
    created_at: datetime | None = None


class ChatSessionOut(BaseModel):
    id: str
    title: str
    workflow_id: str | None = None
    model_config_id: str | None = None
    app_id: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ChatSend(BaseModel):
    """A user turn in an existing chat session."""

    message: str = Field(min_length=1, max_length=20_000)
    inputs: dict[str, Any] = Field(default_factory=dict)


# ---- C3-4 Chat deepening ----


class ChatFeedbackUpsert(BaseModel):
    """End-user 👍/👎 feedback on an assistant message."""

    rating: str = Field(..., pattern="^(positive|negative)$")
    comment: str = Field(default="", max_length=4000)


class ChatFeedbackOut(BaseModel):
    id: str
    message_id: str
    session_id: str
    rating: str
    comment: str = ""
    promoted_dataset_version_id: str | None = None
    promoted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ChatSessionVariableUpsert(BaseModel):
    """Set or update a per-session variable from node config or UI."""

    name: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: Any = None


class ChatSessionVariableOut(BaseModel):
    name: str
    value: Any = None
    updated_at: datetime | None = None


class ChatSessionVariableMap(BaseModel):
    """Snapshot of all variables for a session (template-rendering friendly)."""

    variables: dict[str, Any] = Field(default_factory=dict)


class ChatRegenerateRequest(BaseModel):
    """Regenerate an assistant reply from the last user message before ``message_id``."""

    inputs: dict[str, Any] = Field(default_factory=dict)


class ChatMessageEditRequest(BaseModel):
    """Edit a previously sent user message and re-run the workflow.

    Replaces the message content and drops all later messages (assistant
    reply and any subsequent turns) before re-sending.
    """

    message: str = Field(min_length=1, max_length=20_000)
    inputs: dict[str, Any] = Field(default_factory=dict)


class ChatExportFormat(StrEnum):
    json = "json"
    markdown = "markdown"


class ChatPromoteToDatasetRequest(BaseModel):
    """Promote a negative-feedback assistant message into an evaluation dataset.

    Creates a new version on an existing dataset (or a new dataset when
    ``dataset_id`` is omitted). The case captures the user query as input
    and the assistant reply as the expected answer candidate.
    """

    dataset_id: str | None = Field(default=None, min_length=1, max_length=32)
    dataset_name: str | None = Field(default=None, min_length=1, max_length=200)
    case_name: str = Field(default="", max_length=200)
    expected: dict[str, Any] | None = None
    change_summary: str = Field(default="Promoted from chat feedback", max_length=500)


class ChatPromoteToDatasetOut(BaseModel):
    dataset_id: str
    dataset_version_id: str
    case_id: str
    feedback_id: str
