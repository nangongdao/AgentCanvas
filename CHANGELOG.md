# Changelog

All notable AgentCanvas product changes are documented here. Product releases
follow Semantic Versioning; the API, Workflow DSL, and execution-event contract
versions are tracked separately under `contracts/`.

## [1.0.0] - Unreleased (release candidate)

The v1.0.0 release has not been tagged yet, so its six-month support clock is
not active. The release commit must replace this marker with the publication
date and add `Support through: YYYY-MM-DD`, exactly six months later.

### Added

- Horizontal production roles for API, execution worker, lease scheduler, and
  durable Redis Streams event relay.
- PostgreSQL 17 and pgvector storage for tenant data, durable execution queues,
  LangGraph checkpoints, evaluations, audit history, quotas, and RAG vectors.
- Redis-backed collaboration, request limiting, circuit breakers, half-open
  probe leases, retry budgets, memory, and live SSE delivery with PostgreSQL
  fallback.
- Multi-user identity, OIDC, organizations, project RBAC, service accounts,
  workflow review, immutable versions, quotas, and append-only audit history.
- Checksummed PostgreSQL and application-data backup, isolated restore, and
  restore-drill commands.
- Offline `app.services.migrate_sqlite` relation copy plus dedicated checkpoint
  and Chroma-to-pgvector migration commands for v0.x cutovers.
- Immutable API `1.0.0`, Workflow DSL `1.0`, and execution-event `1.0`
  compatibility baselines and generated frontend types. The API baseline freezes
  compatibility shape; product runtime metadata such as `/api/meta.version` is
  release metadata and may change without a contract-major change.
- HTTP Request node (C2-3) for outbound integration calls. Methods GET/POST/
  PUT/PATCH/DELETE/HEAD, template-rendered URL/headers/query/body, secret-reference
  auth (bearer/basic/custom header) resolved through the project secret system,
  retry on configurable status codes, JSON extraction with dotted-path mapping,
  and status-code gating. Outbound requests are pinned to pre-resolved public IPs
  by default (DNS rebinding, redirect, and Unix-socket safe SSRF protection);
  `allow_private_network` is an explicit opt-in for on-prem targets. Shared
  `app.core.outbound_http` transport now backs both the HTTP node and the durable
  workflow callback dispatcher.
- Code node (C2-2) for sandboxed inline Python. Template-rendered source runs in
  a fresh subprocess wrapped by the C8-1 sandbox: deny-by-default network and
  filesystem, rlimit caps on memory/processes/CPU, and a wall-clock timeout.
  Named inputs are rendered from the template context and injected on stdin;
  the source sets an `output` binding serialized to stdout as the node output.
  `allow_network` / `allow_filesystem` are explicit opt-ins that relax the
  sandbox. The node ships with an official `demo-code` transform template.
- Switch node (C2-4) for multi-way branching with explicit fan-in merge
  semantics. Declares many named exits (first matching branch wins) and a
  `merge_strategy` (`last`/`first`/`error`/`collect`) recording how same-key
  outputs from rejoining branches are resolved. Routing reuses the existing
  `pick_branch` machinery, generalized to a structural config protocol so both
  `condition` and `switch` share one evaluator. Ships with an official
  `demo-switch` three-way routing template.
- Subworkflow node (C2-5) for embedding another workflow's published version
  as an inlined child graph. The referenced version is resolved at compile
  time through a runtime-bound loader and recursively compiled with a
  namespaced emitter, so child node events nest under the parent node
  (`sub.<child>`) and expand in the execution tree history. Declares
  `workflow_id` / `version_id` (empty resolves the current published version),
  `input_mapping` (child input → parent template expression), and
  `output_mapping` (parent key → dotted path into child output). Cyclic
  reference chains (A → B → A) are rejected during an async pre-resolution
  pass before compilation begins. Ships with an official `demo-subworkflow`
  embed template.
- Single-node dry-run (C2-6) for iterating on a node directly from the canvas
  Inspector without triggering a real run. `POST /api/workflows/{id}/dry-run`
  compiles a minimal `start → target → end` graph from the live node config and
  caller-supplied mock inputs, executes synchronously on an ephemeral event bus
  (`persist=None`, throwaway execution id), and returns the node output, final
  output, and lifecycle events. Nothing is written to the executions table or
  event log, no queue lease is acquired, and no project quota or model budget is
  reserved. Agent, tool, RAG, code, HTTP, condition, and switch nodes are
  supported; start/end/subworkflow/iteration are rejected with 422. The
  Inspector panel renders a mock-input form (inferred from the node's declared
  inputs or a free-form JSON fallback) and the output/event snapshot.
- Debug run mode (C2-7) for pausing a live run at selected nodes to inspect
  and rewrite intermediate state before continuing. A debug run carries
  `DebugRunOptions` (`breakpoints` node-id set and/or `single_step`) through
  the run-queue payload on both start and resume so pause points persist
  across the whole run without a dedicated DB column. The compiler injects a
  separate, side-effect-free breakpoint node on the linear edge leaving each
  flagged node (reusing the human-interrupt mechanism); the target runs once,
  the breakpoint pauses with a `node_outputs` snapshot, and on resume applies
  an optional `state_patch` overwriting node outputs before the graph
  continues. Only plain linear edges are eligible — condition/switch/start/
  end nodes are reached by single-stepping through the linear nodes around
  them. The run dialog exposes a debug toggle, breakpoint chip picker, and
  single-step switch; the execution console renders a debug-resume panel
  showing the interrupted snapshot as an editable JSON patch.
- Variable & data-flow pane (C2-8) for inspecting how data moves between
  nodes. The canvas Inspector now renders a Data Flow section for the
  selected node: parsed `{{input.x}}` / `{{vars.x}}` / `{{nodes.id.output.y}}`
  template references are classified into their source, listed alongside the
  referenced upstream nodes and workflow variables, and resolved against live
  run values. Each node type publishes a declarative `output_schema` through
  `/api/node-types` (agent, rag, http, tool, condition, switch, human, start,
  end, iteration, subworkflow) so the pane can preview the output shape; code
  nodes leave it empty and fall back to the live snapshot. The execution store
  persists per-node bounded output snapshots from `node_finished` events, and
  React Flow edges render a custom type that, on hover, shows the flowing
  data summary — both reuse the backend `bounded_json_snapshot` redaction so
  no secrets are surfaced.
- Workflow triggers and integrations (C1): published workflow versions can be
  started by signed webhooks (HMAC, IP allow-list, input-schema validation,
  rotation, optional synchronous mode), cron schedules (IANA time zones, misfire
  skip/catch-up, failure policies, multi-instance-safe transactional dispatch),
  published workflow APIs (project API keys with hashed tokens, generated
  OpenAPI fragments), and outbound event callbacks (durable outbox, per-row
  leases, exponential backoff, activation boundary). A trigger center manages
  all four and execution history records the trigger source.
- Application distribution (C3-1/C3-2/C3-3): apps bind a published workflow
  version and publish as `chatbot`/`completion`/`api` with project/link/public
  visibility. The standalone runtime page `/apps/p/{slug}` serves end users
  without a platform session (link apps require a `?t=` token compared by
  SHA-256), streams chat over SSE with persisted sessions, citations, and
  suggested questions, and is embeddable via a static floating-bubble script
  with per-origin `frame-ancestors` enforcement, an origin allow-list, and a
  configurable theme color.
- Chat deepening (C3-4): per-session variables act as configurable multi-turn
  memory — the runner injects the durable snapshot as the `{{session.<name>}}`
  template root and agent nodes can declare `session_writes` to persist
  rendered values after each reply. Assistant replies can be regenerated,
  user messages edited and resent (both stream over SSE and truncate later
  turns), and conversations export as JSON or Markdown. End users leave 👍/👎
  feedback on the platform chat and the public runtime (message lists embed
  `feedback_rating`); negative turns promote one-click into evaluation
  datasets as new cases with immutable versions, closing the loop with D2.
- Application usage views (C3-5): `GET /api/apps/{id}/usage` aggregates an
  app's sessions, messages, executions, token totals, estimated USD cost, and
  feedback rate over a trailing window with UTC daily buckets. Cost reuses the
  D2 estimator over the app sessions' executions (events + version DSL +
  model pricing); missing usage or rates report an explicit unknown total.
  End-user send budgets on the public runtime are enforced per chat session
  from durable message rows (`RATE_LIMIT_APP_RUNTIME_REQUESTS`), surviving
  restarts and holding across API replicas. The AppsPage renders a usage
  dialog with metric cards, a daily trend table, and window switching.
- Fixed in C3-4: the public runtime route no longer probes the platform
  session, so unauthenticated visitors never see the login dialog.
- Hybrid retrieval and rerank (C4-1): knowledge bases can switch to
  `retrieval_mode="hybrid"` to fuse vector ranking with BM25 keyword candidates
  via reciprocal-rank fusion. PostgreSQL ranks through `to_tsvector('simple')`
  with a GIN expression index; SQLite and embedded Chroma rank in process with
  a shared Latin/Han tokenizer and BM25 scorer. Per-hit score components
  (vector/keyword/fused/rerank) surface through a retrieval-debug contract for
  the future RAG console. A pluggable rerank stage backed by an OpenAI-
  compatible `/rerank` endpoint (configured as a `kind="rerank"` model row) is
  disabled by default and fails open. Switching retrieval mode never
  invalidates existing vectors.
- Retrieval debug console (C4-4): the knowledge-base page now requests and
  explains vector, keyword, fused, and rerank scores, highlights query terms in
  hit chunks, and compares a prior run with a debounced top-k/threshold tuning
  pass. Editors can capture a fresh query case into a new or immutable D2
  evaluation-dataset version with answerability and relevant-chunk labels.
  Stale results are hidden from capture when the query or settings change.
- Citation experience (C4-5): platform Chat and public app replies persist
  structured sources, expand the retrieved child chunk with its wider heading
  context, and expose source actions. Platform citations focus the exact
  knowledge document; public apps use a message-scoped endpoint that validates
  app, session, message, citation, document, knowledge-base, project, and link
  token ownership before serving the original file. Application usage now
  reports referenced/available citation counts and citation coverage.
- Workflow AI Copilot (backlog): `POST /api/workflows/copilot/draft` turns a
  natural-language request into a Workflow DSL draft. The prompt is assembled
  from the live node catalog and the DSL envelope, so a new node type reaches
  the copilot with no second edit; every reply is round-tripped through the same
  `validate_dsl` gate the editor and the compiler use, and a rejected draft is
  repaired in a bounded loop that shows the model the exact validator errors.
  A draft that is schema-valid but graph-invalid is returned with its verdict
  rather than dropped, and cannot be applied. Drafts are never persisted: the
  editor applies one through the normal save path as a single undoable canvas
  change. The canvas gains an "AI Copilot" panel with a preview, node list,
  validation report, model/usage readout, and an optional "modify the current
  canvas" mode. The demo (mock) Provider answers the copilot prompt with a
  canned but valid workflow, so the feature is demonstrable without an API key.
- Google Gemini provider: a native `gemini` adapter for the Generative Language
  API. Gemini is the one target the OpenAI-compatible client cannot cover — it
  needs its own request envelope (`contents` / `systemInstruction` /
  `generationConfig`), its own `x-goog-api-key` auth header, and its own SSE
  shape — so a Gemini model config previously had to masquerade as an OpenAI
  endpoint and silently lose the system prompt. The adapter maps the unified
  message shapes onto Gemini's, hoists system messages into
  `systemInstruction`, turns `json_mode` into
  `generationConfig.responseMimeType`, and translates `functionCall` parts back
  into the shared tool-call stream. Because Gemini repeats a cumulative
  `usageMetadata` on every chunk, usage is emitted once at end of stream rather
  than per chunk, which the shared merge step would otherwise sum into a
  multiple of the real total. DeepSeek, vLLM and other OpenAI-compatible
  servers keep using the `openai_compat` adapter with a custom base URL. The
  provider list served by `GET /api/models` and
  `/api/models/provider-capabilities` is derived from the registry, so no
  contract change accompanied it.
- Provider endpoint discovery: `POST /api/models/discover` lists the models an
  endpoint actually serves, so adding a model config no longer means recalling a
  vendor model id from memory. It speaks each adapter's own listing API — OpenAI
  `/models`, Anthropic `/v1/models`, Gemini `/v1beta/models`, Ollama `/api/tags` —
  and returns the ids with a chat/embedding hint inferred from the name, or from
  Gemini's declared generation methods. The model dialog gained a "test
  connection" action that probes the in-progress form and offers the result as a
  picker instead of a free-text field; when editing, the stored key is reused
  server-side so a secret never has to be retyped. Outbound calls reuse the
  SSRF-safe transport, so the destination is pinned to pre-resolved public IPs
  and a private target needs an explicit opt-in — which is what makes a locally
  hosted Ollama reachable. An inline API key is used for the probe only and is
  never persisted, and a vendor error that echoes the credential back is redacted
  before it reaches the operator. The endpoint is admin-gated, carries its own
  rate-limit budget, and records an audit event. The demo (mock) provider answers
  from a canned catalog, so discovery is demonstrable without an API key.

### Changed

- Product package, runtime metadata, and frontend versions are now `1.0.0`.
- Production Compose requires PostgreSQL, Redis, one-shot migrations, and an
  explicit 2 API + 2 worker topology.
- Tagged releases publish the exact fault-tested backend and frontend images
  with both the product version and source commit SHA; `latest` is not used.
- SQLite workflow schedule dispatch now retries transient database-lock
  conflicts with bounded fresh-session backoff while preserving exactly-once
  slot claims; PostgreSQL and non-lock failures keep their prior behavior.

### Security

- Production containers run as non-root with read-only filesystems, dropped
  capabilities, bounded resources, and private backend/Redis ports.
- Provider and MCP secrets remain encrypted at rest and support bounded secret
  references; authentication, rate limits, project isolation, and audit events
  fail closed on required shared-backend loss.
- Process-level sandbox for plugin and code-node subprocesses (C8-1). On Linux
  with nsjail or bubblewrap installed, declared plugin permissions
  (network/filesystem) become mandatory OS-level enforcement — a disconnected
  network namespace unless egress is declared, a read-only host rootfs with
  writable mounts only for declared paths, and rlimit caps on CPU, address
  space, file size, process count, and open files. Windows dev and stripped
  images degrade explicitly to environment cleanup and are surfaced as
  degraded in `/readyz` and `/api/meta`; `SANDBOX_BACKEND=none` is rejected in
  production. The escape test set (host filesystem write, outbound egress, fork
  bomb, OOM) is enforced at the sandbox argv layer.

### Upgrade Notes

- Read [the v1.0 upgrade guide](docs/v1.0-upgrade-guide.md) before deploying.
- v0.x SQLite installations must follow the dedicated
  [SQLite-to-PostgreSQL migration manual](docs/v1.0-sqlite-to-postgresql.md).
- Support windows and compatibility promises are defined in the
  [version support policy](docs/version-support-policy.md).
- Chunking strategy upgrade (C4-2): knowledge bases can switch
  `split_strategy` between `window` (default), `recursive` (markdown
  headings → paragraphs → sentences), and `heading` (per top-level section).
  Under `heading` with `parent_chunk`, child chunks keep a parent reference
  whose wider context can expand for citation display. A side-effect-free
  `POST /api/knowledge-bases/preview-chunks` runs the splitter against caller
  text so editors tune parameters before committing to a full re-index.
  Switching strategy or parent_chunk invalidates existing vectors.
- Online knowledge sources (C4-3): knowledge bases can ingest URL content.
  `fetch_page`/`crawl_source` reuse the shared SSRF-safe outbound transport
  (public-IP pinning, no redirects/proxies), strip HTML to searchable text
  in-house, and bound single-page/sitemap crawls by `max_pages` ≤ 50 and
  `depth` ≤ 3. `OnlineSourceSyncService` hashes the fetched body and skips
  re-embedding when the sha256 is unchanged (zero embedding quota for no-op
  syncs); changed content persists as a markdown document through the standard
  ingestion pipeline. CRUD + manual sync routes are editor-gated.
