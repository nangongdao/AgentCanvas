"""Workflow DSL schema — single source of truth for canvas export JSON."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NodeType(StrEnum):
    START = "start"
    AGENT = "agent"
    TOOL = "tool"
    CONDITION = "condition"
    SWITCH = "switch"
    RAG = "rag"
    HUMAN = "human"
    ITERATION = "iteration"
    HTTP_REQUEST = "http"
    CODE = "code"
    SUBWORKFLOW = "subworkflow"
    END = "end"


class VariableType(StrEnum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"


class Position(BaseModel):
    x: float
    y: float


class CanvasGroup(BaseModel):
    """A presentation-only frame that can contain workflow nodes."""

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="未命名分组", max_length=128)
    position: Position = Field(default_factory=lambda: Position(x=0, y=0))
    width: float = Field(default=480, gt=120, le=4_000)
    height: float = Field(default=280, gt=80, le=4_000)
    node_ids: list[str] = Field(default_factory=list, max_length=200)
    collapsed: bool = False
    color: str = Field(default="#22d3ee", min_length=4, max_length=32)


class CanvasNote(BaseModel):
    """A presentation-only sticky note attached to a workflow canvas."""

    id: str = Field(min_length=1, max_length=64)
    text: str = Field(default="", max_length=4_000)
    position: Position = Field(default_factory=lambda: Position(x=0, y=0))
    width: float = Field(default=240, gt=120, le=1_200)
    height: float = Field(default=140, gt=64, le=1_200)
    color: str = Field(default="#fbbf24", min_length=4, max_length=32)


class CanvasMetadata(BaseModel):
    """Persisted editor-only objects; execution ignores this section."""

    groups: list[CanvasGroup] = Field(default_factory=list, max_length=100)
    notes: list[CanvasNote] = Field(default_factory=list, max_length=200)


class WorkflowVariable(BaseModel):
    name: str
    type: VariableType = VariableType.STRING
    required: bool = False
    default: Any = None


class WorkflowSettings(BaseModel):
    max_loop_iterations: int = 20
    timeout_seconds: int = Field(default=300, gt=0, le=86_400)
    recursion_limit: int = 50


class InputField(BaseModel):
    name: str
    type: VariableType = VariableType.STRING
    required: bool = True
    default: Any = None


class StartConfig(BaseModel):
    input_schema: list[InputField] = Field(default_factory=list)


class AgentToolRef(BaseModel):
    server_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)


class AgentRequirements(BaseModel):
    usage: bool = False
    cost: bool = False


LoadBalanceStrategy = Literal["failover", "round_robin"]


class AgentConfig(BaseModel):
    model_config_id: str = Field(default="default", min_length=1, max_length=64)
    fallback_model_config_ids: list[str] = Field(default_factory=list, max_length=5)
    load_balance: LoadBalanceStrategy = Field(
        default="failover",
        description=(
            "failover: 始终优先首选模型,其余仅作故障转移;"
            "round_robin: 按调用轮转起点,把请求分散到整条模型链,失败仍按序转移"
        ),
    )
    agent_mode: Literal["simple", "react", "supervisor"] = "simple"
    system_prompt: str = "你是一个有帮助的助手。"
    user_prompt: str = "{{input.user_query}}"
    tools: list[AgentToolRef] = Field(default_factory=list)
    max_tool_rounds: int = 5
    context_nodes: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=lambda: {"temperature": 0.3})
    workers: list[str] = Field(default_factory=list)
    memory: dict[str, Any] = Field(default_factory=lambda: {"enabled": False, "window": 10})
    output: dict[str, Any] = Field(default_factory=lambda: {"format": "text"})
    requirements: AgentRequirements = Field(default_factory=AgentRequirements)
    # C3-4: multi-turn session memory. Maps a chat-session variable name to a
    # template rendered against this node's own output after the reply is
    # produced, then persisted for later turns (read via {{session.<name>}}).
    session_writes: dict[str, str] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def validate_model_chain(self) -> Self:
        chain = [self.model_config_id, *self.fallback_model_config_ids]
        if any(not model_id.strip() or len(model_id) > 64 for model_id in chain):
            raise ValueError("fallback model IDs must contain 1 to 64 characters")
        if len(set(chain)) != len(chain):
            raise ValueError("model fallback chain cannot contain duplicate IDs")
        for var_name, template in self.session_writes.items():
            if not var_name or len(var_name) > 128:
                raise ValueError("session variable names must contain 1 to 128 characters")
            if not template.strip():
                raise ValueError("session variable templates cannot be empty")
        output_format = str((self.output or {}).get("format") or "").strip().lower()
        schema = (self.output or {}).get("schema")
        if schema is not None:
            if not isinstance(schema, dict):
                raise ValueError("output.schema must be a JSON Schema object")
            if output_format not in ("json", "json_schema"):
                raise ValueError(
                    "output.schema requires output.format to be 'json' or 'json_schema'"
                )
        return self


class ToolConfig(BaseModel):
    server_id: str = Field(default="", min_length=1)
    tool_name: str = Field(default="", min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    retry: dict[str, Any] = Field(default_factory=lambda: {"max_attempts": 2})


class ConditionRule(BaseModel):
    left: str
    operator: Literal[
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "contains",
        "not_contains",
        "regex",
        "is_empty",
        "not_empty",
    ]
    right: Any = None


class ConditionGroup(BaseModel):
    op: Literal["and", "or"] = "and"
    rules: list[ConditionRule] = Field(default_factory=list)


class ConditionBranch(BaseModel):
    id: str
    label: str = ""
    group: ConditionGroup = Field(default_factory=ConditionGroup)


class ConditionConfig(BaseModel):
    branches: list[ConditionBranch] = Field(default_factory=list)
    default_branch: str = "else"


class SwitchMergeStrategy(StrEnum):
    """How a switch fan-in resolves same-key outputs from concurrent branches."""

    LAST = "last"  # later writes win (default merge_dicts behaviour)
    FIRST = "first"  # earliest branch's output is kept
    ERROR = "error"  # a collision raises instead of silently overwriting
    COLLECT = "collect"  # all branch outputs gathered into a list per key


class SwitchConfig(BaseModel):
    """Multi-way branch with explicit fan-in merge semantics (C2-4).

    Unlike ``condition`` (binary by convention), ``switch`` declares many named
    exits and an explicit ``merge_strategy`` for how same-key outputs are
    resolved when branches rejoin. The compiler routes via the same
    ``pick_branch`` machinery; the merge strategy is recorded on the node and
    surfaced in events so downstream fan-in is auditable.
    """

    branches: list[ConditionBranch] = Field(
        default_factory=list,
        max_length=32,
        description="Ordered branches; the first whose group matches is taken.",
    )
    default_branch: str = Field(default="else", min_length=1, max_length=64)
    merge_strategy: SwitchMergeStrategy = Field(
        default=SwitchMergeStrategy.LAST,
        description="How same-key outputs from rejoining branches are resolved.",
    )


class RagConfig(BaseModel):
    kb_id: str = ""
    query: str = "{{input.user_query}}"
    top_k: int = Field(default=5, ge=1, le=50)
    score_threshold: float = Field(default=0.2, ge=0, le=1)
    output_format: Literal["merged_text", "chunks"] = "merged_text"


class HumanConfig(BaseModel):
    title: str = "人工审批"
    instruction: str = "请确认是否继续"
    form_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_hours: int = 24


class EndConfig(BaseModel):
    output_template: dict[str, Any] = Field(
        default_factory=lambda: {"answer": "{{nodes.prev.output}}"}
    )


class PluginConfig(BaseModel):
    """Fallback config model for dynamically registered plugin node types."""

    model_config = ConfigDict(extra="allow")


class NodeSpec(BaseModel):
    id: str
    # Known built-ins remain ``NodeType`` values; dynamically loaded plugins
    # are retained as strings so old clients can round-trip them unchanged.
    type: NodeType | str
    name: str | None = None
    position: Position = Field(default_factory=lambda: Position(x=0, y=0))
    config: dict[str, Any] = Field(default_factory=dict)

    def typed_config(self) -> BaseModel:
        model = CONFIG_MODELS.get(self.type) if isinstance(self.type, NodeType) else PluginConfig
        if model is None:
            model = PluginConfig
        return model.model_validate(self.config or {})


class EdgeSpec(BaseModel):
    id: str
    source: str
    target: str
    source_handle: str | None = None
    target_handle: str | None = None
    label: str | None = Field(default=None, max_length=256)


class IterationSubgraph(BaseModel):
    """A self-contained graph executed once for each item."""

    nodes: list[NodeSpec] = Field(default_factory=list)
    edges: list[EdgeSpec] = Field(default_factory=list)

    def to_workflow(self, *, name: str = "iteration-item") -> WorkflowDSL:
        return WorkflowDSL(name=name, nodes=self.nodes, edges=self.edges)


class IterationConfig(BaseModel):
    items: str = Field(
        default="{{input.items}}",
        description="Template expression that must resolve to an array.",
    )
    item_variable: str = Field(default="item", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    index_variable: str = Field(default="index", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    batch_size: int = Field(
        default=10,
        ge=1,
        le=1_000,
        description="Items scheduled in one wave; the child graph still receives one item per call.",
    )
    concurrency_limit: int = Field(
        default=4,
        ge=1,
        le=100,
        description="Maximum child graph calls running concurrently within a wave.",
    )
    failure_strategy: Literal["abort", "skip", "collect_error"] = Field(
        default="abort",
        description="Abort pending work, omit failed results, or retain structured failures.",
    )
    recursion_limit: int = Field(default=50, ge=2, le=1_000)
    subgraph: IterationSubgraph

    @model_validator(mode="after")
    def validate_variable_names(self) -> Self:
        if self.item_variable == self.index_variable:
            raise ValueError("item_variable and index_variable must be different")
        return self


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"


class HttpAuthConfig(BaseModel):
    """Outbound authentication wired through the project secret reference system.

    ``token_ref`` / ``password_ref`` are secret references (``env://``,
    ``docker://``, ``external://``, ``plain:``) resolved by the runtime
    SecretResolver — never literal secrets. ``username`` is a non-secret
    template expression rendered like any other node string.
    """

    type: Literal["none", "bearer", "basic", "header"] = "none"
    token_ref: str = Field(default="", description="Secret reference for the bearer/header token.")
    header_name: str = Field(
        default="Authorization",
        min_length=1,
        max_length=64,
        description="Header name written when type='header'.",
    )
    value_prefix: str = Field(
        default="Bearer ",
        description="Prefix prepended to the resolved token before writing the header (e.g. 'Bearer ').",
    )
    username: str = Field(default="", description="Template-rendered username for HTTP Basic auth.")
    password_ref: str = Field(default="", description="Secret reference for the HTTP Basic password.")


class HttpResponseMapping(BaseModel):
    """How an HTTP response is reduced into node output."""

    extract_json: bool = Field(
        default=True,
        description="Parse a JSON response body into structured output.",
    )
    json_path: str = Field(
        default="",
        description="Optional dotted path into the parsed JSON (e.g. 'data.items').",
    )
    include_headers: bool = Field(
        default=False,
        description="Attach response headers (lower-cased) to the node output.",
    )
    text_fallback: bool = Field(
        default=True,
        description="Fall back to the raw text body when JSON extraction fails or is disabled.",
    )


class HttpRequestConfig(BaseModel):
    method: HttpMethod = HttpMethod.GET
    url: str = Field(
        default="",
        min_length=1,
        max_length=2048,
        description="Template-rendered absolute HTTP(S) URL.",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Header name → template expression. Rendered at run time.",
    )
    query: dict[str, str] = Field(
        default_factory=dict,
        description="Query parameter → template expression. Rendered at run time.",
    )
    body: Any = Field(
        default=None,
        description="Template-rendered request body. Dict/list become JSON; strings are sent verbatim.",
    )
    auth: HttpAuthConfig = Field(default_factory=HttpAuthConfig)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    retry: dict[str, Any] = Field(
        default_factory=lambda: {"max_attempts": 2, "retry_on_status": [408, 429, 502, 503, 504]}
    )
    expected_status: list[int] = Field(
        default_factory=lambda: [200, 201, 202, 204],
        description="Status codes treated as success; others fail the node.",
    )
    response: HttpResponseMapping = Field(default_factory=HttpResponseMapping)
    allow_private_network: bool = Field(
        default=False,
        description="When false (default), outbound requests are pinned to public IPs (SSRF protection).",
    )

    @model_validator(mode="after")
    def validate_auth(self) -> Self:
        auth = self.auth
        if auth.type == "none":
            return self
        if auth.type in {"bearer", "header"} and not auth.token_ref:
            raise ValueError(f"auth.type='{auth.type}' requires auth.token_ref")
        if auth.type == "basic" and not auth.password_ref:
            raise ValueError("auth.type='basic' requires auth.password_ref")
        if auth.type == "header" and not auth.header_name.strip():
            raise ValueError("auth.type='header' requires a non-empty auth.header_name")
        return self


class CodeLanguage(StrEnum):
    PYTHON = "python"


class CodeConfig(BaseModel):
    """Sandboxed user code executed in an isolated subprocess (C2-2).

    Source is rendered with the standard template context and executed in a
    fresh process wrapped by the C8-1 sandbox: deny-by-default network and
    filesystem, rlimit caps. The rendered code reads a JSON object of named
    inputs from stdin and writes a JSON value to stdout, which becomes the
    node output. ``allow_network`` / ``allow_filesystem`` are explicit opt-ins
    that relax the sandbox — they default off and surface in execution events.
    """

    language: CodeLanguage = CodeLanguage.PYTHON
    source: str = Field(
        default="",
        min_length=1,
        max_length=32 * 1024,
        description="Template-rendered source. Reads JSON inputs from stdin, writes JSON to stdout.",
    )
    inputs: dict[str, str] = Field(
        default_factory=dict,
        max_length=64,
        description="Input name → template expression. Rendered and injected as the subprocess stdin payload.",
    )
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    memory_limit_mb: int = Field(
        default=256,
        gt=0,
        le=2048,
        description="Address-space rlimit cap for the sandboxed process.",
    )
    process_count: int = Field(
        default=8,
        gt=0,
        le=64,
        description="Process-count rlimit cap (fork-bomb bound).",
    )
    allow_network: bool = Field(
        default=False,
        description="Opt-in network egress. Defaults off; the sandbox runs a disconnected netns otherwise.",
    )
    allow_filesystem: list[str] = Field(
        default_factory=list,
        max_length=16,
        description="Writable filesystem roots to expose to the sandbox. Defaults to none (read-only rootfs).",
    )


class SubworkflowConfig(BaseModel):
    """Reference another workflow's published version as a node (C2-5).

    The referenced version is loaded at compile time via the ctx
    ``subworkflow_loader`` and recursively compiled into an inlined child
    graph (like the iteration node). The child graph runs once with the
    rendered ``input_mapping`` and its ``final_output`` is mapped back as the
    node output. Cycle detection rejects a subworkflow chain that revisits a
    workflow_id already on the ancestor path.
    """

    workflow_id: str = Field(
        default="",
        min_length=1,
        max_length=64,
        description="The referenced workflow whose published version is embedded.",
    )
    version_id: str = Field(
        default="",
        max_length=64,
        description="Immutable published version id to pin. Empty resolves the workflow's current published version at compile time.",
    )
    input_mapping: dict[str, str] = Field(
        default_factory=dict,
        max_length=64,
        description="Child input name → parent template expression (rendered from the parent state).",
    )
    output_mapping: dict[str, str] = Field(
        default_factory=dict,
        max_length=64,
        description="Parent node output key → dotted path into the child final_output (e.g. 'answer'). Defaults to {'result': ''} (whole output).",
    )
    recursion_limit: int = Field(default=50, ge=2, le=1_000)


CONFIG_MODELS: dict[NodeType, type[BaseModel]] = {
    NodeType.START: StartConfig,
    NodeType.AGENT: AgentConfig,
    NodeType.TOOL: ToolConfig,
    NodeType.CONDITION: ConditionConfig,
    NodeType.SWITCH: SwitchConfig,
    NodeType.RAG: RagConfig,
    NodeType.HUMAN: HumanConfig,
    NodeType.ITERATION: IterationConfig,
    NodeType.HTTP_REQUEST: HttpRequestConfig,
    NodeType.CODE: CodeConfig,
    NodeType.SUBWORKFLOW: SubworkflowConfig,
    NodeType.END: EndConfig,
}


class WorkflowDSL(BaseModel):
    version: Literal["1.0"] = "1.0"
    name: str = "未命名工作流"
    variables: list[WorkflowVariable] = Field(default_factory=list)
    settings: WorkflowSettings = Field(default_factory=WorkflowSettings)
    nodes: list[NodeSpec] = Field(default_factory=list)
    edges: list[EdgeSpec] = Field(default_factory=list)
    canvas: CanvasMetadata = Field(default_factory=CanvasMetadata)

    def node_map(self) -> dict[str, NodeSpec]:
        return {n.id: n for n in self.nodes}
