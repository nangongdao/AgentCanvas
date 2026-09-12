# 项目思路与阶段进度

> 与 [`plan.md`](./plan.md) 配套的实施笔记。总体规划、架构决策、DSL/编译器/MCP/SSE 设计均以 `plan.md` 为准。

## 当前思路（一句话）

先用 **P0 骨架** 把两端服务跑通（健康检查 + 可拖拽画布），再用 **P1** 打通「画布 DSL → LangGraph 线性执行 → SSE 回传高亮/流式」这条灵魂链路；其后按编排加深 → MCP → RAG → 记忆/人工审批 → 部署打磨的顺序逐层加深，每阶段前后端都有可演示版本。

## 文档索引

| 文档 | 用途 |
|---|---|
| [`plan.md`](./plan.md) | 总体架构、技术决策、目录结构、DSL、编译器、MCP、SSE、表结构、性能清单、阶段验收 |
| [`project-audit-and-upgrade.md`](./project-audit-and-upgrade.md) | 当前能力审计、首页/工作台不足、风险分级与 P3.1→P6 升级路线 |
| [`upgrade-and-development-roadmap-2026-08-02.md`](./upgrade-and-development-roadmap-2026-08-02.md) | 当前仓库证据、发布阻断项、U0-U5 升级计划、I1 分布式路径与 D1-D4 后续开发路线 |
| `architecture.md`（待补） | P6 收口时补齐架构决策记录 ADR |
| `dsl-spec.md`（待补） | P6 收口时从当前 DSL 实现固化正式规范 |

## 阶段进度板

| 阶段 | 目标 | 状态 | 备注 |
|---|---|---|---|
| P0 | FastAPI /healthz + Vite/React Flow 空画布 + git + 本 docs | **已完成** | healthz ok；画布可拖节点/导出 DSL；docs 入仓 |
| P1 | DSL v1 + 线性编译器 + OpenAI Provider + ExecutionEngine + SSE + 运行高亮 | **已完成** | E2E 验证:创建→运行→节点事件→流式 token→重放含 workflow_finished;无 API Key 时自动降级 Mock Provider 演示 |
| P2 | condition / 并行 / supervisor / checkpointer / 历史页 | **已完成** | 编译器支持条件分支(edge_taken)/并行 join 屏障/supervisor Command 路由(防死锁单边回环);AsyncSqliteSaver checkpointer(aiosqlite<0.21);25 个单测通过;E2E 验证分支路由、并行 join 顺序、循环保护(21>20 终止);前端 edge_taken 高亮所选分支 + 执行历史弹层 + 条件节点动态 handle |
| P3 | mcphub + tool 节点 + agent 工具循环 + demo MCP servers | **已完成** | owner-task 请求队列保证 SDK 调用与上下文同 task；支持 stdio/SSE/streamable HTTP、懒连接/空闲回收/schema 缓存；tool 节点与 Agent 工具循环发出 tool_call/tool_result；3 个 demo server 自动注册；MCP 管理弹层与节点结构化绑定完成；API E2E 跑通 Calculator、热更新重连与终态 SSE 重放；29 个测试及全量质量门通过 |
| P3.1 | 迁移/恢复 + 产品闭环 + 安全边界 + 持续回归 | **已完成** | Alembic `0002_mcp_secrets`；启动清理遗留 running；工作流目录/深链/800ms 自动保存/冲突恢复；SchemaForm/RunDialog/输入 422；Token viewer/editor/admin RBAC；MCP secret 加密掩码与 stdio allowlist；GitHub Actions + 3 条 Playwright 主路径；45 个后端测试及全量门通过 |
| P4 | RAG ingest + Chroma + rag 节点 | **已完成** | Alembic `0003_rag`、安全上传、PDF/Markdown/Text loader、分块、批量 embedding、本地 fallback、sha256 缓存、Chroma、摄取状态机与检索/删除 API；rag 节点接入 NODE_REGISTRY 并发出 retrieval 类型的 NODE_STREAMING（含 citations）；Agent 节点通过 `_knowledge_context` 注入知识并带括号标签；前端知识库页面（摄取/探测/列表/删除）+ RAG kb_id 绑定 + Agent context_nodes 复选框；ExecutionDrawer 渲染引用；固定语料 top-1 检索评测通过；49+ 后端测试，Ruff、mypy(含 tests)、前端 tsc+build 全绿。 |
| P5 | 记忆降级 + human 审批 + anthropic/ollama + Chat | **已完成** | 后端检索已完成，进入 P5：MemoryStore（Redis 主体 + SQLite 降级）、human 节点 interrupt/resume、多 Provider（anthropic + ollama）、模型管理 CRUD、Chat 页与会话/消息持久化。 **P5-1 完成**：`0004_chat` 迁移新建 `chat_sessions`/`chat_messages`；`app/memory` 提供 `BaseMemoryStore`/`NullMemoryStore`/`SqliteMemoryStore`/`RedisMemoryStore` 与 `build_memory_store`（Redis ping 失败自动降级到 SQLite）；容器注入 memory_store → CompileContext → Agent 节点按 `memory.enabled/window` 加载并持久化对话（尽力而为、不影响运行）；`session_id` 经 `_session_id` 输入键穿透校验；`/api/meta` 返回真实 `memory_backend`。新增 4 个 memory 测试，56 个后端测试全绿，Ruff、mypy(83 文件)通过。 **P5-2 完成**：human 节点执行器调用 LangGraph `interrupt()` 暂停；executor 捕获 `GraphInterrupt`/checkpointer pending，置 `waiting_approval` 并发 `workflow_interrupted` 事件；新增 `POST /api/executions/{id}/resume` + `Command(resume=decision)` 续跑路径；前端 executionStore 增加 `pendingApproval`/`waiting_approval` 状态，ExecutionDrawer 渲染审批面板（批准/驳回 -> resumeExecution）。 **P5-3 完成**：新增 `anthropic`、`ollama` 两个 provider（注册到 PROVIDERS）；anthropic 使用原生 Messages API（system 提示符置顶、content_block_delta/text_delta/input_json_delta 映射、message_delta usage），ollama 使用原生 /api/chat NDJSON 流（done 帧携带 prompt_eval_count/eval_count）。**P5-4 完成**：模型管理 CRUD —— `POST/PUT/DELETE /api/models` + `GET /api/models/providers`，AdminDep RBAC 守卫，API key 经 Fernet 加密入库，is_default 切换时清空同 kind 旧默认；ModelConfigRepo 新增 `delete`/`unset_defaults`；前端 `ModelsPage`（列表/新建/编辑/删除对话框 + 顶栏「模型」入口）。 **P5-5 完成**：Chat 页 -- `ChatSessionRepo`/`ChatMessageRepo` + `/api/chat/sessions`（CRUD）与 `/sessions/{id}/send` SSE 流式端点；每轮将 `user_query` 注入 inputs 并以 `session_id` 触发 execution_engine.start，SSE 回传 token，结束后落库 assistant 消息；前端 `ChatPage`（会话侧栏 + 消息流 + 工作流选择器 + 中止/停止）。新增 4 个 chat 测试；**P5 全部完成**，73 后端测试、Ruff、mypy(92 文件)、前端 tsc/build 全绿。 |
| P6 | 性能清单 + Docker Compose + seeds + README 打磨 | **进行中** | U2/U3/U4 实现和本地门禁已完成；最新门为 186 个后端测试、12 条 Playwright，结构化日志/OTel/Prometheus/Grafana、bundle budget、100×500 画布与后端并发基准均通过。本机无 Docker，实际容器构建/扫描证据仍等待 CI。 |
| D1 | 工作流生命周期与复用 | **已完成** | 不可变版本、执行绑定、发布/diff/回滚/克隆、DSL 导入导出与 0.9→1.0 迁移、官方/用户模板和参数化实例化全部通过。 |
| D2 | 调试、评测与成本治理 | **已完成** | Phase 1-6 实现、Phase 7 全量门禁均完成：脱敏节点检查器、安全失败节点重跑、版本化数据集、基础评测、同数据集双已发布版本 A/B、版本化价格、费用覆盖率/误差边界与逐样本双执行证据、固定语料 RAG 回归指标（Recall@k / MRR / citation coverage / 无答案率）、`ModelCallBudget` 按 execution 累计 token/费用（Decimal `1e-12`）与并发/调用数/token/费用四类上限、durable `cost_alerts`（`0011_cost_alerts`）+ 确认、预算跨 human pause/resume 存活。门禁：227 tests、应用行 84.3%、分支 82.6%、mypy 119 文件、Playwright 全套 14/14 + 成本页 2/2；真实库升级到 `0011` 且 11/11/17 数据完整。 |
| D3 | 身份、多用户与协作 | **已完成（Phase 1-6）** | 身份、组织/项目/RBAC、服务账号、OIDC、presence/软锁、版本评论/审阅、三方合并、管理审计和五类项目配额全部完成。最终门禁：346/346 后端测试、应用行 85.00%、关键分支 84.86%、Ruff、mypy 243 文件、lock/audit；前端 types/build/bundle（初始 gzip 79.71 KiB）、Playwright 23/23 + 成本 2/2。真实库经校验备份/隔离恢复后升级到 `0019`，11/11/17/3871 数据完整且外键错误为 0；下一步为 D4。 |
| D4 | Provider/MCP 生态与平台治理 | **已完成** | Phase 1-6 已完成：Provider 能力与共享韧性、MCP catalog/rollout、隔离插件、Secret Provider 和版本化公共契约均通过完整门禁。 |
| I1 | 分布式执行与存储 | **进行中** | Phase 1-8 代码、本机 PostgreSQL/pgvector、2 API + 2 worker 接管及隔离恢复证据完成；真实 Redis Streams 与 Docker fault lane 仍由 CI 收口。 |
| C1 | 触发与集成 | **进行中** | C1-1 至 C1-5 功能、本地 API/前端/浏览器门禁完成；外部 MCP+RAG curl→callback 与水平容器验收仍未完成。 |
| C2 | 节点生态 | **进行中** | C2-1 Iteration 已完成；C2-2 Code 等待 C8-1 OS 沙箱，C2-3 HTTP Request 尚未实现。 |
| C3 | 应用发布与 Chat 产品化 | **已完成** | C3-1 至 C3-5（应用发布、独立运行时、嵌入分发、会话变量/反馈、用量视图）全部通过本地门禁。 |
| C4 | RAG 2.0 | **已完成** | C4-1 混合检索/rerank、C4-2 分块策略、C4-3 在线数据源（含定时重同步与变化分块重嵌证据）、C4-4 检索调试台、C4-5 引用体验全部通过本地门禁；下一阶段为 C5 UI/UX 产品化。 |
| C5 | UI/UX 产品化 | **已完成** | C5-1 信息架构、C5-2 命令面板、C5-3 Undo/Redo、C5-4 自动布局与对齐、C5-5 分组与注释、C5-6 子图复制粘贴、C5-7 节点 UX、C5-8 主题、C5-9 i18n、C5-10 新手引导、C5-11 可访问性巩固全部通过本地门禁。下一阶段为 C6 性能与规模。 |
| C6 | 性能与规模专项 | **已完成** | C6-1 事件生命周期（ts 索引 + retention job + 冷热分离 + 归档导出）、C6-2 画布规模化（dragPaintP95 132.8→59.5-67.5ms）、C6-3 SSE 通道优化（multiplex + 弱网 chaos e2e + 执行列表活跃轮询）、C6-4 后端热点（创建路径减 SQL + worker deferral 短退避 + ETag 条件 GET，c50 创建 p95 1856ms < 2s）、C6-5 前端加载（路由预加载提示 + 运行页 bundle 门 5.54 KiB < 40 KiB）全部通过本地门禁；性能门全部达成。下一阶段为 C7 计量、运营与管理台。 |
| C7 | 计量、运营与管理台 | **已完成** | C7-1 用量计量导出、C7-2 计划与套餐抽象、C7-3 平台管理台、C7-4 成员协作补全、C7-5 租户数据合规全部完成。C7-5：两阶段删除状态机 `none→requested→purging→purged`（迁移 `0046_org_deletion` 为 organizations 增加四列 + 复合索引，`_ensure_org_active` 冻结执法复用 C7-3）、`OrgDeletionScheduler` 按 grace 到期自动清除、`purge_organization_data` 按 FK 依赖序硬删全部租户行（触发器族→evaluation_runs→execution_events→executions→chat_sessions→workflow_versions→workflows→service_accounts→usage_facts→cost_alerts→projects→memberships→invitations）并清理外部态（pgvector/SQL 向量经 RagService、checkpoints 按 thread_id、Redis collaboration/stream 键），AuditLog 无 FK 故意保留；组织级 JSON 导出端点 `GET /api/organizations/{id}/export`；组织 admin 发起/取消、平台 admin 查看/发起/取消/立即清除。后端 `tests/test_org_deletion.py` 7 个（冻结 403 与取消恢复、重复请求 409、清除后 org/projects/workflows/executions/memberships/usage_facts 全部清零、checkpoint 线程清零、审计行存活、导出包结构与服务账号无凭证、迁移列就位）通过。下一阶段为 C8 安全硬化。 |
| C8 | 安全硬化 | **已完成** | C8-1 进程级沙箱（nsjail/bubblewrap OS 级隔离，2026-08-15）、C8-2 公开面防护（2026-08-24：公开应用运行时独立限流桶 + 请求体/并发/超时防线，prompt injection 数据围栏 + 随机 nonce）、C8-3 纵深补全（2026-08-24：全站安全头中间件含默认 CSP/X-Frame-Options/HSTS opt-in，STRIDE 威胁建模文档，渗透 checklist）全部完成；公开面每客户端桶从会话预算解耦为 `rate_limit_app_runtime_send_requests`。验收：`test_c8_security_headers.py` 6 个 + C8 全套回归 41 通过，Ruff/mypy 298 文件/契约全绿。下一里程碑 M10 收尾（C6 性能 + C8 安全已完工）。 |

### 2026-08-19 C5-1 信息架构与概览首页

- 平台入口改为持久全局顶栏 + 桌面左侧导航；390px 使用带焦点圈定、Escape 关闭和焦点归还的移动抽屉。概览、工作流、应用、知识库、会话、评测和成本保持一级入口，模型、MCP、项目配额、审计收编到 `/settings/*`，旧 `/models`、`/mcp/catalog`、`/quotas`、`/audit` 深链以保留 query/hash 的 replace redirect 兼容。公开应用运行时仍在平台 shell 外。
- 新增只读 `GET /api/overview`，沿用 Viewer/项目授权，按 1–30 天返回最近工作流、执行状态、成功率、每日费用趋势。执行以 `(started_at, id)` keyset 分批聚合，终态优先读取运行时不可变费用快照；无快照的历史执行才使用现有事件估算器，未知费用保持 `null`。模型调用累计快照在 Human 中断时持久化，跨进程 resume 恢复调用数/token/费用；暂停后直接取消也把最新快照提升为终态证据，避免模型改价重写历史费用。
- 概览项目选择以 URL 为持久来源，项目发现与选择解耦；generation fencing 丢弃迟到响应，overview/quota 独立呈现部分失败，五类配额完整展示且零限额按 100% 耗尽。`design-system/MASTER.md` 固化工业化、数据密集的布局/颜色/控件/响应式规则；全局 landmark、Settings 页面 axe、对比度、modal stacking 和移动横向溢出均有浏览器回归。
- 后端门禁：全量 `817 passed / 20 skipped`，应用行覆盖率 82.89%、关键模块分支 83.92%；Ruff、mypy 390 文件、lock、后端/前端契约和依赖审计通过。概览/模型预算/恢复/取消等聚焦回归 33/33。
- 已通过 Standards/Spec 双复审；前端 contracts、source/E2E TypeScript、E2E 数据目录隔离、生产构建与 bundle budget（初始 gzip 90.42 KiB）、moderate audit、默认 Playwright 42/42、成本专用 2/2。100 节点/500 边画布 drag/React commit p95 为 17.9/7.8 ms。C5 总阶段保持进行中，下一切片为 C5-2 全局命令面板。

### 2026-08-19 C5-2 全局命令面板

- 顶栏按钮与 `Ctrl/Cmd+K` 打开全局命令面板；共享导航注册表按角色展示页面，所有角色可复制当前链接，editor 可真正新建/重置工作流。对话框支持完整键盘选择、焦点圈定/归还、active option 滚动、加载/空/错误/重试和 390px 短视口。
- 新增 tenant-safe `GET /api/search`：一个有界 SQL 联合查询覆盖工作流、应用、知识库和文档，按标题精确/前缀/包含确定性排序，排除归档、转义 LIKE 字面量，并复用仓储项目范围谓词。前端中止旧请求并用 generation fencing 防迟到覆盖；应用可补取分页外目标，文档路由保留知识库上下文。
- Standards/Spec 双复审的具体发现均已修复。前端类型、契约、构建（初始 gzip 93.30 KiB）、audit、默认 Playwright 50/50、成本 2/2 和 serious axe 通过，性能 drag/commit p95 14.9/5.3 ms。后端搜索 3/3、覆盖率门 82.95%/84.08%、Ruff、mypy、lock、契约和 audit 通过。
- 后端全量为 `819 passed / 20 skipped / 1 failed`；残余失败是本切片未触及、可独立复现的 streamable HTTP MCP 重连断言 `reconnected.alive`。C5-2 功能与聚焦回归完成，C5 下一切片为 C5-3 Undo/Redo。

### 2026-08-13 C1-1 Webhook 触发器

C1-1/C1-2/C1-3 已完成：已发布工作流可配置 webhook、IANA 时区 cron 计划和项目级工作流 API。API 发布复用服务账号与哈希 token，发布记录绑定项目、服务账号、token 和 immutable published version；支持创建、读取、轮换、禁用，轮换/撤销后旧 token 立即失效。公开 `POST /api/workflow-apis/{workflow_id}/execute` 只接受绑定的服务账号 Bearer token，DSL 自动派生输入/输出 OpenAPI schema 与 curl/Python/JS 示例，执行复用现有 `start_version`、幂等、队列、项目 quota 和成本计量并返回 `202 + execution_id`。Webhook 继续校验 HMAC、IP 白名单和输入 schema；计划使用独立 `workflow_schedules` 持久化表，scheduler 通过精确槽位 CAS 和 PostgreSQL `FOR UPDATE SKIP LOCKED` 抢占，到期槽位的 execution、配额预留、queue 入队和下次运行时间在同一事务提交；支持 skip/catch_up misfire 与 skip/retry/alert 失败策略。重新发布时 API 与计划绑定最新 immutable version，不兼容计划输入会进入 `error` 暂停。新增迁移 `0025`/`0026`/`0027_workflow_api_publications`、OpenAPI/前端类型生成和专门回归测试。该段记录 C1-3 完成时的检查点；C1-4/C1-5 的完成证据见下方同日条目。

### 2026-08-07 D3 Phase 6 项目配额

- `0019_project_quotas` 为知识库和 MCP server 增加可空项目归属，并新增项目限额、实时计数、UTC 月度用量和幂等 reservation。并发执行、文档存储字节、embedding 输入字节、模型费用和项目 stdio MCP 进程五类计量均以原子条件更新或 reservation 防止并发超额，启动时从权威执行/文档状态修复漂移。
- 项目工作流只能使用同项目或全局知识/MCP 资源；跨租户 ID 在缓存、连接或 Provider 调用前失败。模型费用使用 `1e-12 USD` 整数单位，有限额度下缺失价格或 usage 时 fail closed；embedding 只对 cache miss 的 UTF-8 输入字节计费。执行、重跑、resume、评测、RAG、模型与 MCP 生命周期均接入 reserve/release/charge。
- `GET/PUT /api/projects/{project_id}/quotas` 允许项目成员查看、组织 admin 修改 nullable 限额，并写入不含秘密的审计事件。懒加载 `/quotas` 工作台提供项目选择、五类 usage/headroom meter、unlimited 状态和权限/错误反馈；375/768/1280/1920 四个宽度均无横向溢出、重叠或裁切。
- 完整门禁：346/346 tests，应用行 85.00%、关键分支 84.86%，Ruff、mypy 243 文件、lock 与依赖审计；前端 source/E2E 类型、production build/bundle（初始 gzip 79.71 KiB）、high audit、Playwright 23/23 与成本专用 2/2。性能 p95：执行 c1/c10/c50 284.731/977.208/10399.227 ms，10k 分页 63.919 ms，SSE 回放 107.146 ms，MCP discovery 1546.159/114.760/382.141 ms，RAG 28.177/270.712/849.032 ms。
- 真实库升级前归档 `pre-d3-phase6-20260807-233530.tar.gz`，SHA-256 `D1FB258DB1CB981210F2F9E7FB85E7A471C6BC12C4AC9C7F52C06F99262FEB5B`；隔离恢复保留 `0018` 和 11/11/17/3871，完整性 `ok`、零外键错误。实时库升级到 `0019_project_quotas` 后核心计数不变，三个历史 MCP server 保持全局，因尚无项目所以配额表为空。

### 2026-08-07 D3 Phase 5 管理审计日志

- `0018_audit_logs` 保存稳定的组织、项目、actor、action 和 resource 归属，并提供时间、租户、actor、action 与 resource 六组复合索引；迁移 downgrade/reapply、ORM 漂移及真实数据升级均通过。
- 模型/密钥、MCP、服务账号/Token、组织/项目/成员、工作流/版本/导入/克隆/合并/发布/回滚、审阅和 human 审批变更在成功事务中写审计。详情只允许 8 KiB 内的有限 JSON，递归拒绝秘密字段，不保存凭据、Token 明文/哈希/前缀、MCP env/header、工作流 DSL、审阅正文或原始审批载荷。
- `GET /api/audit-logs` 只允许全局 admin，游标绑定全部筛选条件；懒加载 `/audit` 支持相同过滤和分页，viewer 无入口且直接访问会重定向。桌面与 390x844 浏览器路径覆盖权限、分页、过滤和秘密不泄露。
- 完整门禁：315/315 tests，应用行 84.52%、关键分支 85.06%，Ruff、mypy 226 文件、锁与后端依赖审计；前端 source/E2E 类型、build/bundle（初始 gzip 79.48 KiB）/high audit、Playwright 22/22 与成本专用 2/2。画布拖拽/React commit p95 72.1/6.8 ms；后端 c1/c10/c50 101.1/472.4/3510.9 ms，10k 分页 39.9 ms，SSE 回放 81.3 ms。
- 真实库归档 `pre-d3-phase5-20260807-211600.tar.gz` 的 SHA-256 为 `31575FEB0F83D99DA639E9313730B8FB4C0DFE5042195AA901C8A862387C43CF`；隔离恢复保留迁移前 `0017` 和 11/11/17/3871，实时库升级到 `0018` 后计数不变、审计表为空且外键错误为 0。

### 2026-08-07 D3 Phase 4b 版本评论、审阅与冲突合并

- `0017_workflow_reviews` 增加版本评论、回复/解决状态和一版本一审阅；actor key/subject 适配用户、服务账号、静态 token 和开发模式。工作流/版本、父评论归属均由复合外键约束，迁移支持 downgrade/reapply 且模型漂移为零。
- viewer 可评论/回复，editor 可解决线程、发起和决定审阅；requester 不可自审，终态审阅由原子条件更新保护。所有列表/变更先授权所属项目，游标绑定工作流/版本且面板会读取全部分页。
- 冲突恢复以不可变 version ID 解析 base，对 local/remote 做结构化三方合并；字典和稳定 ID 的节点、边、变量支持离散合并，重叠修改/删除返回精确路径且不写入。前端在覆盖前提供自动合并并保留未解决本地状态。
- 完整门禁：310 tests，应用行 84.29%、关键分支 85.06%，Ruff、mypy 214 文件、锁与依赖审计；前端 source/E2E 类型、build/bundle（初始 gzip 79.44 KiB）/high audit、Playwright 20/20 与成本专用 2/2。画布拖拽/React commit p95 57.9/5.6 ms；后端 c1/c10/c50 80.3/619.5/3801.9 ms，10k 分页 35.5 ms，SSE 回放 65.9 ms。
- 真实库归档 `pre-d3-phase4b-20260807-202251.tar.gz` 的 SHA-256 为 `F0913A33D5B817A8236A2F3DD92A234FC44002662489298B5E6A8AD6154C509C`；隔离恢复保留迁移前 `0016` 和 11/11/17/3871，实时库升级到 `0017` 后计数不变且外键错误为 0。

### 2026-08-07 D3 Phase 4a 工作流 presence 与软锁

- `WorkflowCollaborationHub` 用可注入时钟维护按工作流隔离的在线状态和软锁；参与者与租约均按 TTL 失效，锁令牌只返回给持有者，viewer 只可观察/心跳，editor 可获取、续租、释放和明确接管。
- 所有 snapshot、heartbeat、lock、leave 和 SSE 接口先复用工作流项目授权；跨租户负向测试覆盖全部表面。SSE 授权完成即释放请求数据库事务，避免长连接占用连接池。
- 前端在协作状态确认前保持只读，只在角色允许、SSE 在线且持有自己的 lease 时开放画布、配置、保存、运行和自动保存。React StrictMode 下 client ID 按页面稳定，延迟 leave 可被重挂载取消；登出等待协作清理后再撤销凭据。
- 双编辑器浏览器回归证明：第二页被锁阻止、明确接管立即转移编辑权、离开释放 presence/lock，旧页面重新接管后仍由 version 409 进入冲突恢复。1440×900 与 390×844 工具栏无横向页面溢出。
- 完整门禁：295 tests，应用行 84.15%、关键分支 85.06%、Ruff、mypy 204 文件、锁与依赖审计；前端 source/E2E 类型、build/bundle（初始 gzip 79.43 KiB）/high audit、Playwright 19/19 与成本专用 2/2。协作状态有意保持单进程，I1 前不得水平扩展 API 副本。

### 2026-08-02 安全与可靠性升级（U0/U1 起步 + R-03/R-05/R-07/R-08）

在 P6 基础上按 `upgrade-and-development-roadmap-2026-08-02.md` 关闭了第一批上线阻断与可靠性缺口，全部通过本地质量门（94 后端测试、Ruff、mypy 含 tests、前端 typecheck/build/audit）：

- **R-03 / U2-1 修复 Dockerfile Alembic 复制路径**：`COPY alembic.ini mcp_servers ./mcp_servers`（把 alembic.ini 错复制进 mcp_servers）拆为 `COPY alembic.ini ./alembic.ini` + `COPY alembic ./alembic` + `COPY mcp_servers ./mcp_servers`；容器启动迁移改用应用自身 `upgrade_database()`（尊重 `APP_DATA_DIR`/`DATABASE_URL` 环境、可接管旧 schema），不再依赖硬编码 SQLite 路径的 `alembic upgrade head`。
- **R-08 / U2-5 就绪探针**：新增 `app/api/routes/health.py`——`/livez`（进程存活）+ `/readyz`（DB 往返、Alembic 迁移头、checkpointer、配置安全 4 项阻断 + 向量库信息项），未就绪返回 503 并给出可操作原因，Redis 降级经 `memory_backend` 字段可见；compose healthcheck 由 `/healthz` 切到 `/readyz`，nginx 增加 livez/readyz 反代。
- **R-05 / U1-3 checkpointer fail-closed**：生产模式 AsyncSqliteSaver 初始化失败不再静默回退 MemorySaver，`build_container` 启动即抛 `CheckpointerUnavailable`；`/readyz` 通过 `checkpointer_status()` 暴露 ready/degraded/unavailable。
- **R-07 / U3-1 ExecutionEngine 优雅关闭**：`shutdown(grace_period)` 先拒绝新任务（`EngineShuttingDown`→HTTP 503）→ 宽限期内等现有执行自然终态 → 超时取消并保证每个取消任务写入一致终态事件 → flush EventBus 后再关 checkpointer/DB。
- **U1 LangGraph 1.x + 严格 serializer**：依赖升级 `langgraph 0.3.34 → 1.2.10`、`checkpoint 2.1.2 → 4.1.1`、`checkpoint-sqlite 2.0.11 → 3.1.1`（关闭 PYSEC-2026-83 / CVE-2025-67644 等 11 条公告）；checkpointer 显式构造 `JsonPlusSerializer(allowed_json_modules=("core",), allowed_msgpack_modules=("core",))` 白名单序列化器并关闭 pickle 回退；`compiler.py` 两处 `add_node` 因 langgraph 1.x 更严的类型桩加 `# type: ignore[call-overload]` 并注明原因。新增 `tests/test_checkpoint_persistence.py`：human 节点中断 → 关闭并重开同一 checkpoints.db 模拟进程重启 → 重新编译图 resume，验证严格 serde 下审批状态跨重启可恢复。
- **U1-5 Vite 高危修复**：前端 `vite 5.4.21 → 6.4.3`（Windows 共享 dev server 文件边界高公告）；`react-router-dom ^6.28.0 → ^6.30.4`。前端 build 555.46 kB（gzip 168.34 kB）仍触发 >500k 警告，拆包留给 U4-4。
- **U1-7 依赖审计入 CI**：后端加 `pip-audit`、前端加 `pnpm audit --audit-level high`。
- 新增 `tests/test_checkpoint.py`（fail-closed/failback/status）、`tests/test_executor.py`（优雅关闭 5 例）、`tests/test_health.py`（probe 4 例）。

**已接受风险（临时，带到期日）**：

| 公告 | 现状 | 可达性 | 到期 |
|---|---|---|---|
| chromadb `PYSEC-2026-311` | 唯一剩余后端公告；为 Chroma HTTP `/api/v2` 未认证远程代码注入 | 本项目只用嵌入式 `PersistentClient`，从不启动 HTTP server、不传 `trust_remote_code` | 升级/替换 chromadb adapter 或 U1-6 关闭（≤ v0.7.0） |
| react-router 6.x 3 条 moderate（2026-08-10 已关闭） | 已升级至 7.18.2，当前锁文件在 `--audit-level moderate` 下无已知漏洞 | deep link、登录重定向、刷新、测试 URL 与未保存导航保护均有浏览器回归 | 已关闭；CI 持续执行 moderate 审计门 |

### 2026-08-04 U3 API 边界与请求速率片段

- U3-5 已完成统一 HTTP 分页契约：工作流、执行、知识库、文档、会话及会话消息均返回 `items/next_cursor/has_more`，`limit` 限制为 1-200，并支持绑定资源过滤条件的 keyset 游标、搜索、排序和升降序。
- 游标使用稳定的 `(sort value, id)` 组合，绑定排序、搜索和父资源/过滤范围；跨 `workflow_id`、知识库、执行工作流或会话复用游标会返回 422。
- 前端 API 类型和现有页面已切换到分页 envelope；当前页面仍只展示首批有限记录，`has_more` 的增量加载列为后续 UX 工作。
- U3-6 已完成单实例运行时片段：登录、执行启动、Chat 发送、上传/摄取、检索和 MCP 探测各有独立请求窗口、请求体上限与并发上限；非流式操作提供请求 timeout，429/413/504 返回结构化错误。Chat SSE 不设总响应时限，由工作流和 Provider timeout 约束实际执行。上传的 multipart 请求上限与文件上限分离。
- 执行引擎持有真实运行槽位；Agent 普通流、Agent MCP tool loop、Supervisor 决策均经 `ModelCallGate`，具备进程滑窗、进程并发上限、单次 Provider timeout，以及在 human pause/resume 间复用的每执行调用预算。
- U3-6 未完成项保持明确：跨多 worker 的共享计数器、分布式并发协调、前端大列表增量加载和真实性能基准。

验证：此片段在当时通过 `backend` pytest 118 passed、Ruff、mypy、`uv lock --check`；随后 U3 全部验收和 U2 发布实现把全量门提升到 170 个后端测试与 9 条 Playwright。Docker 未安装，本地未宣称容器验收。

### 2026-08-04 U3 完成与 U2 发布闭环实现

- **U3 已完成**：幂等启动/resume/cancel/ingest、事件批量持久化、耐久摄取 job、统一游标分页、单实例请求与 Provider 保护、P5/MCP E2E、`0001`-`0004` 迁移矩阵、覆盖率门，以及 Dialog focus trap/归还、键盘、ARIA、axe 和 reduced-motion 均已进入自动化测试。
- 浏览器门为 9/9：角色权限、工作流/SSE、Calculator MCP、RAG/citations、human pause/resume、模型 CRUD、Chat 持久化、Redis fallback 和 accessibility。
- **U2 实现完成**：后端多阶段固定 uv 的非 root 镜像、nginx unprivileged 前端镜像、独立一次性迁移、dev/prod Compose、安全默认值、资源/日志限制、带 SHA-256 manifest 的 SQLite/checkpoint/Chroma/uploads/workspace/Redis AOF 备份恢复，以及生产运维手册。
- CI 新增 production Compose build/up、Trivy HIGH/CRITICAL OS 扫描、readyz、跨 P3-P5 容器 smoke 和备份作业；本地在 production 配置下通过同一公开 HTTP smoke。由于本机没有 Docker CLI，镜像运行证据必须由 CI Linux runner 提供后才能关闭 G2。
- 最新全量后端门：170 tests，应用行覆盖率 82.37%（门槛 75%），关键模块分支覆盖率 84.35%（门槛 80%），Ruff、mypy 121 个源文件和 uv lock 均通过。
- 后续 U4 已完成；多 worker 共享状态仍严格留在 I1。

### 2026-08-05 U4 可观测性与性能工程完成

- 请求边界统一注入/回传 `X-Request-ID`，接受 W3C `traceparent`；JSON 日志携带 request/execution/workflow/node/provider/error_code 上下文，并对 token、密钥、认证头、URL 凭据与查询秘密集中脱敏。
- 应用内 OpenTelemetry tracer/meter 覆盖 HTTP、workflow、node、Provider（含 TTFT/token）、MCP、ingest、retrieval、SSE 与事件队列；`/metrics` 提供独立 Prometheus registry，可选 OTLP HTTP 导出。仓库加入指标词典、告警、Prometheus 和 Grafana dashboard/provisioning。
- SQLite 连接统一 WAL/busy timeout/synchronous 参数，执行创建在 SQLite 下串行化短事务；Chroma `to_thread` 通过可配置 semaphore 限流并显式关闭，避免 Windows 句柄泄漏。
- 前端路由与 vendor 手动拆包，build 强制初始 gzip `<120 KiB`、任一 raw chunk `<350 KiB`；实测初始 76.04 KiB、最大 raw 208.15 KiB。
- 100 节点/500 边真实浏览器拖拽门实测绘制 p95 40.5 ms、React commit p95 8.0 ms；React Flow 对屏外节点/边进行虚拟化，测试以 API 精确规模和可见画布共同证明场景。
- 后端完整基准通过：mock 执行创建 p95 c1/c10/c50 为 64.527/879.137/11896.058 ms，10k 分页 p95 58.108 ms，1000 事件重放 117.753 ms 且连续无重复终态，MCP/RAG 并发基准均通过预算。SQLite 单实例在 50 并发下的写入增长明确保留为 I1/PostgreSQL 触发信号。
- 大文件按职责拆分：执行 runner、RAG ingestion、Chat 子组件、ModelDialog、E2E support/operations 均独立，相关文件不再集中到数百上千行的单体模块。
- 最新门禁：177 tests；应用行覆盖率 82.65%、关键模块分支 84.54%；Ruff、mypy 128 文件、`uv lock --check`、前端 typecheck/build/bundle、Playwright 10/10、YAML/JSON 与 `git diff --check` 均通过。仅保留上游 Starlette `httpx2` 警告；Docker 本地不可用的约束不变。

### 2026-08-05 U5 平台现代化（部分完成）

- **React 19 完成**：React/React DOM 19.2.8 通过全量浏览器路径；修复知识绑定编辑器不稳定 Zustand 派生 selector 在 StrictMode 下的重复渲染。
- **Vite 8 完成**：Vite 8.2.0 与 plugin-react 6.0.5 保留路由动态加载、vendor 拆包和 preload 边界；bundle budget 继续强制执行。
- **Python 3.12/3.13 完成**：项目最低版本升至 3.12，本地由 uv 使用 3.13.14，CI 建立 3.12/3.13 矩阵，并采用 PEP 695 与标准 tar extraction filter。
- **Tailwind CSS 4 完成**：迁移至 4.3.3 的 CSS-first `@theme` 与 `@tailwindcss/postcss`；桌面/移动视觉基线无漂移，源码/E2E 类型、构建、high audit 和 Playwright 10/10 全绿。
- **MCP SDK 2 完成**：升级至 2.0.0，`Client(mode="auto", cache=None)` 在 owner task 内协商 2026-07-28 并兼容旧服务器；MCPServer、snake_case schema/result、httpx2 与 Streamable HTTP v2 错误语义均完成迁移。21 条聚焦测试覆盖三传输、schema 热更新、工具错误、超时、重连和服务退出。
- 当前全量证据：177 个后端测试，应用行覆盖率 82.64%、关键分支 84.87%，Ruff、mypy 132 文件、锁与依赖审计通过；初始 JS gzip 78.58 KiB、最大 raw chunk 242.03 KiB；Playwright 10/10，100×500 稳态拖拽绘制 p95 33.5 ms、React commit p95 16.9 ms。
- **当时仍待完成**：2026-08-05 的 Router 7.18.2 依赖图产生 high 审计结果，因此该独立切片回退到 6.30.4；此历史阻断已在 2026-08-10 的重新评估中关闭。

### 2026-08-05 D1 工作流生命周期与复用完成

- `0006_workflow_versions` 为历史工作流回填不可变快照并让执行引用精确 `workflow_version_id`；恢复 human interrupt 时从该快照重新编译，不再读取当前草稿。
- 发布、版本 diff、回滚为新草稿、从快照克隆、变更说明与执行历史版本号均已进入 API 和画布版本抽屉；发布版本在后续编辑后保持不变。
- DSL 提供带格式版本的 JSON 导出/导入；集中迁移器支持 0.9→1.0 节点/坐标/handle 兼容并拒绝未知版本，固定夹具验证语义一致。
- `0007_workflow_templates` 与模板库支持三条官方种子、用户模板、分类/标签/搜索、`${parameter.*}` 参数化实例化和权限边界。
- 真实库升级前创建 checksummed 备份；升级后 11 个工作流、11 个快照和 17 个执行全部保留，执行版本缺失数为 0。
- 最新门禁：186 tests，应用行覆盖率 82.91%、关键模块分支 85.20%，Ruff、mypy 139 文件、锁与迁移漂移通过；前端 typecheck/build/high audit，Playwright 12/12，桌面/移动视觉无溢出；画布拖拽/React commit p95 为 60.7/31.9 ms。
- D1 已关闭，下一实施流为 D2 调试、评测与成本治理。P6 容器运行证据仍只等待外部 Linux CI。

### 2026-08-05 D2 Phase 1-3 调试与评测闭环

- 节点检查器从持久事件重建带输入、输出、耗时、token、费用和错误的尝试记录，并在 API 边界集中脱敏；失败节点重跑创建精确版本的子执行，复用可验证的上游快照并阻断副作用路径。
- `0009_evaluations` 提供不可变数据集版本、固定工作流版本的评测运行和逐样本执行证据；exact、contains、Draft 2020-12 JSON Schema 为确定性评测，LLM Judge 和副作用路径分别要求显式授权，Human 节点不允许无人值守评测。
- `/evaluations` 工作台覆盖数据集版本编辑、已发布工作流选择、评测配置、状态轮询、汇总和逐样本报告；列表沿用统一游标契约，桌面/移动视觉无溢出。
- 真实库在 checksummed 备份后升级到 `0009`，保留 11 工作流、11 版本和 17 执行，外键检查为 0。当前完整证据为 205 后端测试、82.81% 应用行覆盖率、84.87% 关键分支、Playwright 14/14；下一项为 Phase 4 A/B 对比。

### 2026-08-05 D2 Phase 4 已发布版本 A/B

- `0010_evaluation_comparisons` 将同一不可变数据集版本、两个原子创建的评测运行及其不同已发布工作流版本绑定；聚合质量、时延、执行失败率、token、费用覆盖率、价格版本、误差上界和 B-A 差值。
- 样本报告并排保留 A/B 状态、分数、时延、费用和执行 ID，可继续通过脱敏节点检查器定位输入与失败节点。缺失 usage 或价格时费用为 unknown，不按 0 计算；单样本 USD 只舍入一次到 `1e-12`。
- `/evaluations` 和模型管理页已覆盖 A/B 启动/报告与输入输出价格配置。1440×900、390×844 截图无重叠或横向溢出。
- 工作流版本和页面数据加载均以请求代次阻止旧响应覆盖新选择/新建数据集；Playwright 专用通用请求容量提高到 1000，生产默认值及专项限流不变。
- 完整门为 208 tests、83.38% 应用行、84.87% 关键分支、Ruff/mypy 157 文件、迁移/锁、前端 types/build/bundle。A/B 压力路径 5/5、完整 Playwright 14/14，画布拖拽/React commit p95 为 59.0/31.6 ms；真实归档 `post-d2-phase4-20260805-215825.tar.gz` 恢复为 11/11/17 核心行、空评测表和 0 个外键错误。

### 2026-08-07 D2 Phase 5 固定语料 RAG 回归指标

- 新增 `rag` 评测器：从持久化执行事件而非模型自述文本重建证据——RAG 节点的 `node_streaming`（`kind=retrieval`）提供有序命中，Agent 节点的 `node_finished` 输出提供答案与引用清单。截断的引用证据直接判失败，不按缺失计 0。
- 指标为 Recall@k、MRR、citation coverage 与无答案率族（`no_answer_rate`、`no_answer_accuracy`、`correct_abstention_rate`、`false_no_answer_rate`、`false_answer_rate`）。citation coverage 只统计答案正文里真正出现的 `[n]` 标签所对应的文档，杜绝"检索到即算引用"。`retrieval_k` 在创建评测时即校验不得超过 RAG 节点自身 `top_k`，避免报告出现工作流不可能达到的 k。
- 数据集用例的 `expected` 由 `RagExpectation` 约束：可回答用例必须且只能给出文档或分块之一的相关列表，不可回答用例不得声明相关来源，故"无答案"是被断言的语义而不是空结果的副产品。
- 固定语料通过 `snapshot_rag_corpus` 落到评测配置：知识库、embedding 模型（含 provider/base_url/params 与凭据指纹）、分块参数和全部 ready 文档的 `content_sha256` 一起生成 SHA-256 指纹；每个用例执行前重新比对，语料在排队后发生变化即失败，不产出无法复核的分数。
- 补齐使指纹有意义的前置约束：embedding 模型被知识库引用时不可删除、不可改 kind；改动 provider/model_name/base_url/api_key/params 会把依赖文档重置为 `pending` 并清空对应 Chroma 集合，避免用新模型的查询向量检索旧模型的索引。
- 运行汇总新增 `rag` 块，A/B 对比新增 `delta.rag` 八项差值；`/evaluations` 报告区渲染检索证据与语料指纹，A/B 表增加 Recall@k / MRR / Citation / No-answer accuracy 四行。
- 完整门禁：212 后端测试、83.44% 应用行覆盖率、84.87% 关键模块分支、Ruff、mypy 164 文件、`uv lock --check`；前端 typecheck/typecheck:e2e/build（初始 gzip 78.62 KiB、最大 raw chunk 242.03 KiB）、Playwright 14/14。浏览器路径在真实摄取的固定语料上跑通 `Recall@3=1.000, MRR=1.000`，并在 390×844 校验报告无横向溢出。
- 修复 U4 性能 smoke 的版本化 schema 漂移：旧合成 SSE execution 未写 D1 后强制的 `workflow_version_id`，现绑定 `demo-linear` 最新快照并由持久化查询测试保护。最终 c1/c10/c50 创建 p95 75.922/399.645/2428.986 ms、10k 分页 p95 40.716 ms、1000 事件回放 59.187 ms 且连续无重复终态。

## 开发命令速查

```bash
# 后端
cd backend
python -m uv sync
python -m uv run uvicorn app.main:app --reload --port 8000

# 前端
cd frontend
pnpm install
pnpm dev
```

## 实施原则

1. **计划文档进仓库**：所有规划以 `docs/plan.md` 为单一事实源，不散落在会话外路径。
2. **每阶段可演示**：不攒大招，阶段结束必须有可点可跑的验收。
3. **简历亮点深挖、基础设施够用**：编译器 / MCP owner-task / SSE 精确重放 / 多 Provider / 流式与图状态解耦 值得深写；CRUD、Toast 够用即可。
4. **Windows 友好**：`python -m uv`、pathlib、stdio MCP 用 `cmd /c npx`、不在 Win 上强制 uvloop。

### 2026-08-08 D4 Phase 1 Provider 能力治理

- 新增 `0020_provider_capabilities`：Provider 适配器声明 stream、tools、vision、JSON mode、reasoning、usage、cost 的不可变默认能力；模型只可关闭适配器已声明的能力，cost 还要求 usage 与完整的输入/输出价格。
- `GET /api/models/provider-capabilities`、模型 CRUD 和模型页能力矩阵已落地；能力覆盖值不进入 Provider 出站参数，也不暴露密钥。Agent DSL 的工具、JSON、推理、usage/cost 要求在工作流创建、更新、导入、模板实例化、合并、发布、回滚和克隆前校验。
- 执行与 resume 按工作流中的精确模型 ID 建立 Provider 映射，缺失自定义模型不会回退到默认模型；JSON 请求参数由 OpenAI-compatible、Ollama 和 demo Provider 自己整形，ExecutionEngine 不含 Provider 分支。
- 真实库已从 `0019` 升至 `0020`，11/11/17/3871 核心数据保持不变；迁移前备份为 `backend/data/backups/pre-d4-phase1-20260808-121500.tar.gz`，SHA-256 为 `B3122B4AC37E556A4E65BB9A645EAA44B1217DA1EF699C66F3A0D7B40D786708`，隔离恢复返回 `0019` 且数据一致。
- 验收：后端 362/362、Ruff、mypy 250 文件、前端 typecheck/typecheck:e2e/build/bundle（79.72 KiB 初始 gzip）、默认 Playwright 23/24、隔离性能 1/1（drag p95 72.4 ms）、成本 2/2；默认套件中的一次 133 ms 性能抖动经独立运行复核通过，阈值保持 100 ms。Phase 1 已交付，后续 Phase 2/3a 证据见下节。

### 2026-08-08 D4 Phase 2 resilience 与 Phase 3a MCP catalog

- Phase 2 新增 Provider/MCP 共用 resilience registry、健康快照、熔断/半开、有限 retry budget、首 chunk 前安全 fallback 和可控 fault injection；`/api/resilience` 与 MCP health API 只返回脱敏运行状态，ExecutionEngine 保持 Provider-agnostic。
- Phase 3a 以 `0021_mcp_catalog` 建立 entry/version/history 三层目录模型和 MCP server 可空绑定。manifest 声明 transport、network、filesystem、commands 权限；审批、撤销、superseded 版本与升级历史均有管理员 API 和安全审计。绑定及每次运行时加载都重新校验 stdio 命令/脚本目录或 HTTP host 权限。
- MCP 管理面板可加载 approved 目录版本并绑定/解绑，三个内置 demo MCP 服务已补齐 approved v1 元数据；旧自定义服务器可保持未绑定以兼容迁移。
- 验收：focused catalog/API/manager/migration/quota 集合 42/42，完整后端 381/381，Ruff、mypy 256 文件、前端 typecheck/typecheck:e2e/build/bundle（79.72 KiB 初始 gzip）、focused MCP 浏览器 1/1、成本治理 2/2。默认套件的功能路径通过，但画布拖拽性能在整套和隔离复测中为 179.6/163.4 ms，React commit p95 18.5 ms，100 ms 阈值未放宽，作为后续性能风险保留。
- 真实库已从 `0020` 升至 `0021`，11/11/17/3871 核心数据不变；catalog 为 3 entries / 3 versions / 3 history rows / 3 bound servers。迁移前备份 `backend/data/backups/pre-d4-phase3-20260808-170218.tar.gz`，SHA-256 `06865E79302A35A08F0BAA4749DF4F1E58BC5B5D5153BE473BD91F6A25C80E2A`。

### 2026-08-08 D4 Phase 3b catalog administration and rollout

- Added catalog version diff, administrator-only rollout preview and rollout. Preview scopes targets to servers bound to the selected entry, reuses MCP policy checks, reports incompatible targets without mutation, and supports explicit one-server or batch rollout.
- Changed targets disconnect MCP sessions and clear tool caches; repeated targets are unchanged. Aggregate and per-server audit/history records contain only safe IDs, versions, counts, and reasons; no secrets, command payloads, env, or headers.
- The lazy `/mcp/catalog` workspace covers immutable version creation, approval/revocation, permission diff, preflight, and rollout. Direct-predecessor diff defaults, editor RBAC denial, policy-incompatible skips, and idempotency are covered.
- Verification: full backend 383/383, Ruff, mypy 258 files, frontend source/E2E typecheck, production build/bundle 79.78 KiB initial gzip, Playwright 26/26, cost 2/2, `uv lock --check`, and `git diff --check`. Dependency audit retains pypdf CVEs and transitive nanoid high advisory.

### 2026-08-08 D4 Phase 4 versioned node plugin SDK

- `backend/app/plugins/protocol.py` defines API/protocol version, manifest, JSON Schema, UI hints, permission and request/response/event models. `registry.py` scans bounded plugin directories and registers `plugin.*` via the existing `BaseNodeExecutor` factory.
- `runner.py` starts a fresh process with no shell and a minimal environment. Relative entrypoints, request IDs, protocol versions, timeout, input/output bytes, event count, exit status, and cleanup are fail-closed. Dynamic registries clear stale external executors when a new app/test root is loaded.
- `executor.py` passes bounded workflow snapshots, emits plugin version/permission metadata and redacted bounded plugin events, and returns plugin output through the generic node contract. Unknown plugin types and invalid plugin config fail before compile/save; the built-in `backend/plugins/echo` worker is the reference implementation.
- `/api/node-types` and the Node Library dynamically expose plugin label, description, schema, UI hints, and declared permissions. Existing built-in nodes and DSL remain compatible. Permission declarations are auditable claims, not OS-level sandbox grants.
- Verification: plugin focus 6/6, full backend 389/389, Ruff, mypy 187 app files, frontend source/E2E typecheck, production build/bundle 79.77 KiB initial gzip, default Playwright 27/27, cost 2/2, `uv lock --check`, `git diff --check`, and secret-pattern scan passed. Dependency audit retains the recorded `pypdf` CVEs and transitive `nanoid` high advisory; the next slice is secret-provider adapters and rotation.

### 2026-08-08 D4 Phase 5 secret-provider references and rotation

- Added `SecretResolver` and a fixed provider chain. `env://UPPER_CASE_NAME` reads only the named process environment variable; `docker://relative-file` is confined below absolute `DOCKER_SECRET_DIR` and uses bounded UTF-8 reads; `external://path` requires an explicitly injected resolver through `create_app`, with no implicit network fallback.
- Model API keys and MCP env/header maps remain encrypted in the existing Fernet fields. API listing/display returns only masked values and safe source labels. Runtime model, embedding, and MCP connection paths resolve references only at the outbound boundary.
- Masked MCP updates now retain raw stored references instead of resolving them, so a temporarily unavailable provider cannot block safe editing or copy a rotated secret into the database. Default model/demo seeds use the same resolver chain.
- Frontend model/MCP editors show the supported reference forms and source labels. Focused coverage includes bounded syntax/path/size checks, missing provider behavior, legacy migration, external injection, list-without-resolve, and rotation-safe masked update. Final gates pass: backend 400/400, Ruff, mypy 188 application files, frontend source/E2E typecheck, production build/bundle 79.78 KiB initial gzip, default Playwright 27/27, cost 2/2, `uv lock --check`, `git diff --check`, and secret-pattern scan. Dependency audit retains the pypdf CVEs and transitive nanoid advisory.

### 2026-08-09 D4 Phase 6 versioned public contracts

- OpenAPI publishes API `1.0.0` plus DSL/event `1.0` metadata. Backend-owned deterministic artifacts live under `contracts/current`; an immutable `1.0.0` major baseline protects OpenAPI, Workflow DSL and execution-event compatibility.
- Workflow DSL rejects arbitrary versions. Persisted and live events expose `schema_version: "1.0"`; generated frontend types are consumed by workflow/execution DTOs, canvas DSL, SSE and the execution store.
- `python -m scripts.contracts check` rejects generated drift, removed paths/operations/parameters/responses/fields/enum values, newly required inputs and narrowed schema constraints. `pnpm contracts:check` rejects stale TypeScript; both run in CI. Existing baselines cannot be force-overwritten.
- Final gates: backend 413/413, 85.11% application lines, 84.27% critical branches, Ruff, mypy 275 files, lock/contract checks and performance smoke; frontend types/build at 79.84 KiB initial gzip, Playwright 27/27 and cost 2/2. Performance p95: execution c1/c10/c50 107.2/749.0/7158.5 ms, pagination 42.9 ms, SSE 82.2 ms; canvas drag/React commit 60.2/3.6 ms.
- `pypdf 6.15.0`, `js-yaml 4.3.1` and `nanoid 3.3.17` close the recorded high advisories. Backend audit has no unignored findings; frontend high audit is clean. D4 is complete; I1 is next.

## 2026-08-09 I1 Phase 1-2 PostgreSQL foundation

- ADR 0001 fixes API/control-plane, worker, scheduler, event-relay, PostgreSQL/Redis ownership and the lease-generation fencing, at-least-once, cancellation, retry, and side-effect rules before distributed execution code is introduced.
- Runtime database validation now accepts only `sqlite+aiosqlite` and `postgresql+asyncpg`. PostgreSQL engines use bounded pool size/overflow/timeout/recycle settings and pre-ping; readiness reports the active backend and startup logs redact database passwords.
- Alembic upgrades and schema checks share a bounded PostgreSQL session advisory lock. The new PostgreSQL 17 CI job runs two concurrent upgrades, checks the database against ORM metadata, and exercises organization/project tenancy CRUD on the real driver.
- SQLite behavior remains supported and its migration matrix stays green. PostgreSQL backup/restore is deliberately not claimed: the current archive flow remains SQLite-only until a `pg_dump`/`pg_restore` recovery slice is implemented.
- Verification: backend 427 passed/1 PostgreSQL skip, 85.00% application line and 84.27% critical branch coverage, Ruff, mypy 277, dependency/lock/contract checks, and performance budget; frontend source/E2E typecheck, production build at 79.84 KiB initial gzip, default Playwright 28/28 and cost 2/2. Canvas drag/React commit p95 is 71.9/6.8 ms. This machine has no Docker/PostgreSQL service, so the real PostgreSQL test result remains a CI obligation.
- A browser regression found that a full login reload generated a second collaboration client ID for the same tab, leaving a transient duplicate participant. The ID now survives reload in session storage while remaining tab-scoped; the deterministic reload test and two-editor flow pass in the full suite.
- Next: move the LangGraph checkpointer and waiting approvals to PostgreSQL with explicit compatibility and recovery tests.

## 2026-08-09 I1 Phase 3 PostgreSQL durable checkpointer

- `make_checkpointer()` selects strict-serde `AsyncSqliteSaver` for SQLite and bounded Psycopg `AsyncPostgresSaver` for PostgreSQL. Saver setup uses a session advisory lock; production fails closed on initialization or missing waiting-approval checkpoints.
- Explicit `python -m app.services.migrate_checkpoints` copies legacy SQLite parent chains and pending writes into PostgreSQL and re-validates every `waiting_approval` row before success.
- Verification: backend 430 passed / 3 PostgreSQL skipped, 84.83% application lines, 84.27% critical branches, Ruff, mypy 278 files, dependency/contract/lock/performance gates; frontend types/build at 79.84 KiB initial gzip, Playwright 28/28 + cost 2/2. Real PostgreSQL setup/import/restart remains a CI obligation on this machine.

## 2026-08-10 I1 Phase 4 durable execution queue and fenced worker leases

- Migration `0022_execution_queue` adds `execution_queue_items` with kind `start|resume|rerun`, status `queued|leased|retry_wait|dead_letter|done`, owner/lease generation fencing, availability, cancel, payload, and claim index.
- `ExecutionQueueRepo` implements enqueue, PostgreSQL `FOR UPDATE SKIP LOCKED` / SQLite CAS claim, heartbeat, complete, fail/retry/dead-letter, cancel, approval release, and durable lease metadata. Create defaults to `queued`; claim flips execution to `running`.
- `InProcessExecutionWorker` originally supplied the single-host poll/kick loop. Phase 8 now reuses it inside dedicated worker processes and assigns expired-lease classification to a dedicated scheduler.
- Human interrupt (`waiting_approval`) frees the active queue lease in the same transaction as the status write so resume cannot race worker post-run complete. Phase 8 startup recovery leaves all leases untouched and only fails untracked legacy running rows. Project quota reconcile counts `queued` + `running`.
- Settings/env knobs: `EXECUTION_WORKER_POLL_SECONDS`, `EXECUTION_LEASE_SECONDS`, `EXECUTION_LEASE_MAX_ATTEMPTS`.
- Focused evidence: queue/fencing/worker/recovery tests green; migration head assertions follow `0022_execution_queue`; ruff/mypy on touched modules green. Local full backend suite: 435 passed / 3 PostgreSQL skipped after fixing stale `0021` head asserts in older migration contracts.

## 2026-08-10 I1 Phase 5 shared durable EventBus / multi-source SSE

- Migration `0023_event_relay` adds `execution_event_relay_cursors` with `stream_key` PK and monotonic `last_event_id` (global `execution_events.id` cursor).
- `EventRelay` reads committed `execution_events` after the durable cursor, XADDs execution-specific Redis Streams (`agentcanvas:exec-events:{id}`), then advances the cursor. Persist batches kick the relay for low-latency publish; without `REDIS_URL` the relay stays idle.
- `ExecutionEventStream` carries `(execution_id, seq, event_type, node_id, ts, payload, event_row_id)`; consumers de-duplicate by `(execution_id, seq)`.
- `iter_execution_sse` implements ADR order: local subscribe + flush → PostgreSQL replay → Redis backlog + PostgreSQL gap fill → live local bus / Redis tail / bounded PG poll, with heartbeat pings and terminal stop.
- Settings/env: `EVENT_RELAY_POLL_SECONDS`, `EVENT_RELAY_BATCH_SIZE`, `EVENT_STREAM_MAXLEN`. SSE reuses the shared stream adapter from the container.
- Focused evidence: relay cursor/migration, stream encode/decode, FakeRedis publish/filter, and SSE replay+dedupe tests green; migration head contracts follow `0023_event_relay`; ruff/mypy on touched modules green. Next: Phase 6 shared collaboration coordination and worker-owned MCP.

## 2026-08-10 I1 Phase 6 shared collaboration + worker-owned MCP

- Without `REDIS_URL`, collaboration stays process-local memory (`backend=memory`) for single-instance development.
- With `REDIS_URL` and a reachable Redis, presence/soft-lock/revision share via Redis hashes/strings and atomic revision counters so peer API processes observe the same lock and participants.
- When `REDIS_URL` is configured but Redis is unavailable, collaboration fails closed to a read-only hub (`backend=unavailable`) instead of splitting process-local locks; mutating endpoints return HTTP 503.
- Snapshot/API payloads expose `backend` and `read_only`; editors compute `can_edit &= not read_only`.
- MCP live sessions belong to the worker holding the execution lease via `McpManager.for_execution(execution_id)`. Control-plane test/discover/health probes release live sessions after the request.
- Resume reconnects from durable MCP server configuration under a fresh worker-owned manager; live session objects are never transferred between workers.
- Focused collaboration Redis and MCP isolation/control-plane release tests pass on SQLite. Multi-API live Redis collaboration remains a multi-instance CI obligation.

## 2026-08-10 I1 Phase 7 multi-instance-safe RAG vectors

- `VectorStore` now owns the complete async index/query/delete/health boundary. SQLite `auto` remains embedded Chroma; PostgreSQL `auto` resolves to shared pgvector, while explicit SQLite `sql` remains a portable migration-test path.
- Migration `0024_document_chunks` uses `BLOB` on SQLite and dimensionless `VECTOR` on PostgreSQL, enables extension `vector`, constrains dimensions/chunk identity, and keeps a direct knowledge-base/document ownership index.
- PostgreSQL retrieval casts to the query dimension and orders by pgvector cosine distance in the database. It no longer fetches every knowledge-base embedding into an API process. Same-document writers lock the durable `documents` row; all shapes, finite values, non-zero norms, and dimensions validate before old rows are deleted.
- `python -m app.services.migrate_vectors --source ...` performs a complete no-write Chroma preflight before idempotent per-document replacement. Missing vectors or KB/filename/chunk-count mismatches fail closed.
- `/api/meta` and `/readyz` expose `chroma|sql|pgvector`; PostgreSQL health includes the installed extension version. `VECTOR_MAX_CONCURRENT` and `CHROMA_MAX_CONCURRENT` are independent.
- Local evidence: the full backend gate passes 458 tests with 4 real-PostgreSQL skips, 83.93% application line coverage, and 82.09% critical branch coverage. Ruff, mypy 295 files, lock/contracts, audits, frontend types/build/bundle (79.84 KiB initial gzip), Alembic drift, diff check, and performance budgets pass. Performance p95 is 135.81/884.022/5890.293 ms at execution concurrency 1/10/50, 36.773 ms for 10k pagination, and 76.089 ms for contiguous 1000-event SSE replay.
- The live SQLite database was checksummed and upgraded from `0021` to `0024`: 11 workflows, 11 versions, 17 executions, and 3871 events remain intact, with zero KB/document/chunk rows and zero foreign-key errors. The pre-upgrade archive SHA-256 is `1B208A659F1BCDE98D9E8484B571C2E1FB6BE537ACCF37972D39ADD4978E3751`; a recovery copy created during an accidental restore was retained after immediately re-applying migrations and rechecking drift/counts.
- Four PostgreSQL tests are collected, including real pgvector ranking/cross-store visibility. This host has no Docker and its PostgreSQL 18 service has no vector extension or configured test credentials, so the real test is required in CI using `pgvector/pgvector:0.8.6-pg17-bookworm` and is not claimed locally.
- Next: Phase 8 2 API + 2 worker fault injection, real-data/checkpoint/vector migration drill, PostgreSQL backup/restore, full release gates, and documentation closeout.

## 2026-08-10 I1 Phase 8 distributed release and disaster recovery

- `APP_PROCESS_ROLE=all` preserves SQLite single-host behavior. Horizontal
  `api|worker|scheduler|relay` roles require PostgreSQL, Redis, and
  `STARTUP_MIGRATIONS=false`; release-owned migrate/bootstrap steps run before
  any long-lived process.
- Dedicated workers seed event sequence from durable history and fence state and
  event writes with the same owner/generation lease. Startup never steals a
  lease. The scheduler locks expired rows and requeues only replay-safe work;
  ambiguous tool, human, MCP/memory agent, and dynamic-plugin work is
  dead-lettered.
- API-only cancellation is durable and worker-owned. The event relay uses a
  locked PostgreSQL cursor, while API stream consumers fall back to PostgreSQL
  during Redis loss and reconnect to Redis without restart.
- `compose.prod.yml` now defines PostgreSQL 17/pgvector, Redis, two API replicas,
  two workers, one scheduler, one relay, frontend, one-shot migrate/bootstrap,
  and backup/restore/restore-drill profiles. Responses expose
  `X-AgentCanvas-Instance` for cross-replica evidence.
- PostgreSQL backup uses `pg_dump` custom format with a checksummed manifest and
  no credentials in command arguments. Restore validates the entire archive,
  refuses the configured source, nested/non-empty data directories, and
  non-empty target databases, restores file members to an isolated volume, and
  invokes `pg_restore --exit-on-error` without clean/drop. Restore drill compares
  Alembic head, every application/checkpoint table, waiting approvals, events,
  pgvector metadata/rows, a database-side distance probe, and restored API
  readiness.
- CI now orchestrates cross-API query/SSE, worker outage and recovery, Redis
  fallback/reconnection, full-runtime human approval resume, multi-owner claim
  and stale fencing, and isolated PostgreSQL restore. Local evidence is
  `491 passed, 5 skipped` (all real-PostgreSQL-only), 83.28% application lines,
  82.09% critical branches, Ruff, mypy across 309 files, contracts/lock/audits,
  frontend build at 79.84 KiB initial gzip, Playwright 28/28 plus cost 2/2, and
  backend performance c1/c10/c50 p95 122.0/1189.0/10224.2 ms. This was the
  pre-runtime baseline; the later host-native continuation below supplies real
  PostgreSQL/pgvector, 2+2 failure-recovery, and isolated-restore evidence.

## 2026-08-10 U5 Phase 6 Router 7 complete

- `react-router-dom` and its Router core are upgraded from 6.30.4 to 7.18.2. The current lock satisfies React 19 and Node 24, and `pnpm audit --audit-level moderate` reports no known vulnerabilities; CI now enforces that stricter clean baseline.
- Existing data-router imports remain on the supported v7 `react-router-dom` re-export. The slice does not mix in the separate Router 8 package/import migration.
- A focused Playwright regression proves a protected deep link survives authentication and that dirty workflow navigation can stay on the current URL or discard and continue. Existing coverage continues to prove reload, role changes, login redirects, and test URLs.
- Frontend contracts, source/E2E TypeScript, production build, and bundle budget pass. Initial JS gzip is 88.31 KiB and the largest raw chunk is 270.81 KiB, below the unchanged 120/350 KiB budgets.
- Browser evidence is 29/29 default tests plus 2/2 cost-governance tests. Canvas drag-paint/React-commit p95 is 78.2/5.8 ms, below the unchanged 100 ms interaction budget.

## 2026-08-10 I1 Phase 8 host-native verification continuation

- Built an ignored, isolated PostgreSQL 16.14 + pgvector 0.8.3 runtime on `127.0.0.1:55432` without changing the system PostgreSQL 18 service. The complete real integration marker passes 5/5: concurrent migrations and ORM drift, approval across app/checkpointer restart, legacy checkpoint import, database-side vector ranking/peer visibility, and multi-owner claim/scheduler/stale fencing.
- Ran two API replicas, two dedicated workers, one scheduler, and one relay against a fresh PostgreSQL database. Cross-API enqueue/query/SSE passed; two slow jobs were exclusively leased by different workers; terminating worker 1 caused worker 2 to reclaim and finish its job at lease generation 2 / attempt 2 with one terminal event.
- Used Garnet 2.1.2 only for supported Redis-compatible capabilities. Cross-API collaboration state, Lua coordination, outage fail-closed behavior, and reconnect passed. During outage, API SSE completed 518 ordered events through PostgreSQL fallback. Garnet rejects `XADD`/`XREAD`, so real Redis Streams live relay and duplicate-delivery evidence remain open.
- The outage drill exposed collaboration snapshot GET returning 500 when the shared backend failed. A regression test now proves `CollaborationUnavailable` maps to HTTP 503 on snapshots as it already did on mutations; both endpoints recover without API restart when the backend returns.
- Stopped all source roles before backup, restored the checksummed custom PostgreSQL archive and files into a distinct empty database/data directory, and passed the restore drill. Evidence includes Alembic `0024`, all application/checkpoint counts, 9 executions, 3167 events, 2 pgvector chunks, restored API readiness, the same RAG document ranked first, and waiting-approval resume at generation 2 / attempt 2.
- Final backend gate: 494 passed / 5 skipped, 83.17% application line coverage, 82.09% critical branch coverage, Ruff, format, and mypy over 309 source files. The default-suite skips are the opt-in PostgreSQL marker, which separately passes 5/5 against the isolated runtime. I1 Phase 8 remains open only for Redis >= 5 Streams duplicate delivery/live relay and Docker container fault orchestration.

## 2026-08-12 C0 release artifacts and verification

- Product, backend, and frontend metadata are aligned at `1.0.0`. The tagged CI path exports the tested backend/frontend images and publishes immutable product-version and full-commit-SHA tags; it never publishes `latest`.
- Added the v1.0 changelog, upgrade guide, SQLite-to-PostgreSQL relational/checkpoint/vector migration manual, and six-month support/SemVer policy. The migration manual explicitly limits relational copying to Chroma-backed sources with an empty SQLite `document_chunks` table; SQL-vector legacy data requires a separate export or rebuild.
- Release CI now refuses a tag while `CHANGELOG.md` still contains `Unreleased` and requires an exact `Support through: YYYY-MM-DD` date, ensuring the support clock starts only at publication.
- Available verification is green for 40 focused backend release/MCP/migration/request tests, 31 execution tests, backend full coverage (511 passed/9 skipped before the final execution-create optimization), Ruff, mypy, generated contracts, frontend contract/typecheck/build/audit, actionlint, `uv lock --check`, and whitespace checks.
- C0 is not declared closed: no `v1.0.0` tag has been created, the worktree is intentionally pending commit, Docker is unavailable on this host, real-calculator MCP browser discovery still times out locally, and drag-paint p95 measured 264.2 ms in the contaminated browser run. Redis Streams, Docker/Trivy fault orchestration, release tagging, and stable browser performance require CI or an idle runner.

## 2026-08-13 C1-4 outbound workflow callbacks

- Migration `0028_workflow_callbacks` adds encrypted per-workflow callback configuration, a uniquely keyed durable delivery outbox, and independent execution-event/cost-alert scanner cursors. Migration `0030_callback_activation_boundary` records first/reactivation time so scanner cursors can stay idle with no active callbacks without later backfilling historical events.
- The callback dispatcher runs in local `all` and horizontal `scheduler` roles. It claims one delivery immediately before sending, extends that lease beyond the bounded attempt, signs `<timestamp>.<raw-body>` with HMAC-SHA256, sends stable delivery/idempotency headers, disables redirects and proxy inheritance, applies exponential retry, and retains terminal delivery dead letters.
- Execution success/failure/cancellation, ambiguous worker-loss dead letters, budget alerts, and API/worker project quota rejection share the same channel. Cursor plus source uniqueness prevents duplicate delivery creation across restarts and replicas.
- Callback lifecycle APIs support editor create/update/secret rotation/disable, viewer reads/delivery history, Fernet persistence, event selection, retry settings, and audit events. Any non-public DNS answer is rejected; the outbound transport connects only to the validated literal addresses while retaining the original Host/TLS SNI, closing DNS rebinding. Dispatcher shutdown is bounded and cancels a stuck attempt after its grace period.
- Local evidence: callback lifecycle/signing/retry/lease/pinned-DNS/shutdown/activation-boundary tests 9/9, combined execution scheduler/project quota/model cost slice 16/16, focused activation migration/callback slice 13/13, plus Ruff and mypy across 340 files. C1 remains open for the external MCP+RAG curl/callback acceptance proof and horizontal/container evidence.

## 2026-08-13 C1-5 trigger center UI

- A right-side workflow trigger center now manages Webhook, schedules, workflow API, and outbound callbacks from one dense operational surface. It supports creation, activation/disable, credential rotation, one-time copying, callback event subscriptions, and recent delivery state.
- Migration `0029_execution_trigger_source` records `manual|webhook|schedule|api` on every execution. Public webhook/API and scheduler paths set the source explicitly; execution history renders a compact source label.
- Generated OpenAPI types own the frontend DTO boundary. The trigger center is keyboard-modal via the existing focus trap and maintains zero horizontal overflow at a 390x844 viewport.
- Local evidence: trigger/migration backend slice 40/40, source assertions for webhook/API/schedule, full app Ruff and mypy, frontend contract/typecheck/E2E types/build/bundle, and Playwright trigger-center interaction 1/1 with reviewed desktop/mobile screenshots. C1 acceptance remains open for the external MCP+RAG callback and horizontal/container proof.

## 2026-08-14 C2-1 Loop / Iteration node

- Added a validated Iteration node for array inputs with bounded scheduling waves, concurrency limits, stable item/index bindings, ordered aggregation, and abort/skip/collect-error policies.
- Child graphs compile once and execute with isolated state. Abort cancels pending calls; nested lifecycle and route events carry occurrence IDs plus stable node paths for correct cost and Inspector attribution.
- Recursive workflow traversal now includes child Agent capability/model discovery, evaluation pricing, replay side-effect classification, and scheduler recovery safety.
- Added the canvas node, Iteration editor, public node schema, generated contract support, 100-item public HTTP acceptance, dual loop-guard tests, Playwright autosave coverage, and `demo-iteration` / `official-iteration` templates.
- Review-driven focused gates pass. Final full backend/browser gates remain in progress; the complete C2 “Iteration + Code + HTTP” acceptance stays open until C2-2/C2-3 are implemented.

## 2026-08-14 post-review security hardening

- Project-scoped service-account API tokens can no longer enter generic viewer/editor/admin management dependencies, even after token login stores the credential in a cookie. Global service-account management tokens remain backward compatible; workflow API execution still requires the exact publication token/account/project binding.
- Callback delivery now avoids pre-claiming a slow batch, renews only the active row, pins the validated DNS result into the actual TCP transport, and bounds dispatcher shutdown. A dedicated activation boundary prevents first-enable and re-enable history backfill while preserving the no-active-callback SQLite fast path. Focused evidence is 9/9 service-account tests and 9/9 callback tests; migration roundtrip, Ruff, and focused mypy pass.

## 2026-08-16 C3-3 embed distribution

- Completed the C3-3 slice on top of the started schema work: migration `0033_apps_embed_config` (theme color + embed origin allow-list), `frame-ancestors` CSP on every public runtime response (`'none'` unless origins are configured), and per-app theming on the standalone runtime page.
- Added `GET /api/apps/p/{slug}/embed.js`, a floating-bubble bootstrap whose body is fully static: it derives slug and platform origin from its own script src, reads `data-token`/`data-color`/`data-title`/`data-position` attributes, lazily mounts a bubble button plus a 380x600 dialog iframe with `?embed=1`, and never interpolates server data into JavaScript. The server only gates existence/runnability/embed-enablement (404/403) and serves `no-referrer`/`nosniff`/`no-store` headers so disable/rotate take effect immediately.
- The Apps embed dialog now offers copyable iframe and bubble snippets (link apps marked with a TOKEN placeholder) alongside the theme picker and origin allow-list editor.
- Fixed inherited C3-3 defects: `AppOut.embed_allowed_origins` serialized an explicit empty list back as `null` (create and update paths), `schemas/app.py` exported a nonexistent `MAX_EMBED_ORIGINS` in `__all__`, and `AppRepo.get_runtime_by_slug` had a duplicated return statement.
- Regenerated contracts; the refresh also catches up the C2 node types and app schema fields previous sessions had left unregenerated (`switch`/`iteration`/`http`/`code`/`subworkflow` in the DSL schema, `array` variable type, apps paths).
- Local gates: app backend slice 38 passed, Ruff and mypy clean on touched files, frontend typecheck/build/contracts/e2e types pass, initial gzip 88.43 KiB and runtime-page chunk ~4.6 KiB gzip stay within budget.
- Browser evidence: a new `app-embed` Playwright path serves a real cross-origin host page from a second loopback origin (`127.0.0.1:5174`), includes the bubble script from the platform origin, and proves the data-title bubble mounts, lazily opens the runtime iframe (`?embed=1`), renders the welcome message and chat input inside the iframe without any platform session, and closes via Escape/toggle. Chromium private-network-access blocks synthetic public hosts from loading loopback scripts, so the external page must share the loopback address space in the local harness — a test-harness constraint, not a product restriction.
- Full backend suite after the C3-3 fixes: 721 passed + 20 skipped with two pre-existing failures from the previous session's migration head bump (`test_event_relay`, `test_provider_capability_migration` pinned revision `0032`); both assertions now pin `0033_apps_embed_config` and their files pass 6/6.

## 2026-08-17 C4-4 retrieval debug console

- The knowledge-base workspace now exposes a retrieval probe with per-hit vector, keyword, fused, and rerank score details plus safe query-term highlighting. After an initial run, top-k and threshold edits trigger a 250 ms debounced rerun and retain the previous result for A/B overlap/add/remove comparison.
- Editors can capture only a fresh, settings-matched result into D2: a portal dialog creates a dataset or appends an immutable version, supports a workflow input variable, answerable/unanswerable labeling, and relevant chunk selection. Query edits hide the old result and close/disable capture so a stale query cannot be persisted.
- Browser acceptance creates and ingests a real document, verifies score details/highlighting and automatic A/B tuning, captures `relevant_chunk_ids`, then runs the generated dataset against a published RAG workflow and proves Recall@3/MRR 1.0.
- A c50 performance regression found during the repository gate was traced to per-event empty callback scans plus SQLite durable-checkpoint contention. Empty callback kicks are now coalesced; ordinary non-interrupting runs skip the shared saver, while human/debug/resume runs—including a human node in any recursively resolved subworkflow—retain it.
- Both Standards and Spec reviews completed. Non-blocking module-shape smells are recorded for later cleanup; all correctness findings were fixed and the Spec follow-up found no remaining blocker.
- Final evidence: backend 795 passed/20 skipped, 83.11% application lines and 82.19% critical branches; Ruff, mypy 382 files, lock/contracts/dependency audit; performance p95 c1/c10/c50 181.064/430.358/1321.423 ms, pagination 70.815 ms, SSE 118.499 ms; frontend contracts/types/build/audit at 88.47 KiB initial gzip; Playwright 34/34 + cost 2/2, canvas drag/commit p95 57.3/4.2 ms. C4-5 citation experience remains next.

## 2026-08-17 C4-5 citation experience

- Parent heading context now survives ingestion into both Chroma metadata and SQL `document_chunks` through migration `0038_citation_context`; RAG citations include the knowledge-base ID and parent text, and both platform/runtime assistant messages replay those citations durably.
- A shared accessible citation disclosure renders the hit and wider parent context in platform Chat and public apps. Platform source actions open the exact knowledge base/document and focus its row; public apps use a message-scoped source endpoint with full token and ownership validation before the original upload is served inline.
- Application usage now reports available/referenced citations and coverage. The production definition counts unique stored citation labels present in the answer and returns no percentage for non-RAG traffic.
- The real browser path uses isolated global/project RAG corpora to respect current Chat/app scoping, verifies expansion, document focus, source bytes and `1 / 1 = 100.0%`, and captures reviewed desktop/mobile states with no horizontal overflow. Visual QA also fixed end-only RAG runtime answers reconciling as empty bubbles.
- Standards/Spec review corrections now preserve message-wide citation identity across multiple RAG nodes, count complete citation tokens, reconcile end-only runtime answers, navigate documents beyond the first page, invalidate legacy parent-enabled vectors, enforce the full public-source ownership chain, and filter non-ready documents before vector/keyword top-k ranking.
- The first post-review full gate exposed an intermittent SQLite lock in the 1,000-slot two-scheduler stress test. SQLite dispatch now retries only transient lock errors through bounded fresh sessions; a deterministic injected-lock regression plus a 10-run stress loop protect exactly-once behavior.
- Final evidence: backend 803 passed/20 skipped, 83.20% application lines and 83.60% critical branches; Ruff, mypy 384 files, lock/contracts/dependency audit/diff checks; performance p95 c1/c10/c50 209.477/302.081/1153.594 ms, pagination 45.786 ms, SSE 291.317 ms; frontend contracts/types/build/audit at 88.50 KiB initial gzip; Playwright 35/35 plus cost 2/2, canvas drag/commit p95 44.5/4.4 ms. C4-5 is complete; C4-3 scheduled re-sync and explicit changed-chunk embedding-call evidence remain before C4 can be closed.

## 2026-08-19 C4-3 scheduled online-source completion

- Migration `0039_online_source_schedule` adds a nullable 5–10080 minute interval, `next_sync_at`, `sync_started_at`, and a monotonic `sync_generation` with a due index and a check constraint; the live dev database migrated cleanly from `0030` (after backup `pre-c43-schedule-20260819-120432.db`) to `0039` with core counts intact and zero foreign-key errors.
- Manual and scheduled syncs claim the same atomic fence: `OnlineSourceRepo.claim` flips status to `syncing`, stamps the owner window, and advances the generation; stale claims expire after the configured lease. Replacement ingestion stays `pending` and non-retrievable until fenced `finish_success` publishes it and retires the prior document in the same transaction. Cancellation reconciles the source's authoritative document binding before cleanup, including the uncertain-commit window and a later generation already holding the claim.
- `OnlineSourceScheduler` runs in `all`/`worker` roles (they own the RAG stack and upload volume), scans due sources in bounded batches, and retries only retryable SQLite lock errors; the lightweight coordination-only scheduler service stays unchanged. Settings expose poll/batch/lease knobs documented in `.env.example`.
- Change-aware replacement now retires the old document: the new body is ingested first, the bound document and sha are swapped atomically, and only then are the prior document's vectors/storage removed. The unchanged case still skips embedding entirely. A regression pins the embedding provider call counts at `[2, 1]` for a two-page corpus where only one page changes.
- The knowledge workspace gains an online-sources panel (create with schedule, edit, manual sync with no-change toast, delete with confirmation and document cleanup, last/next stamps, and error display); configuration and deletion of a running sync are rejected with 409. Non-overlapping 5-second polling refreshes scheduled status and document state. Split files stay within the 200–400-line guideline.
- Final evidence: focused RAG/online-source/migration suites 69 passed; full backend 810 passed/20 skipped with 82.88% application lines and 84.08% critical branches; Ruff, mypy 386 files, lock, contracts, audits, diff checks; performance smoke p95 c1/c10/c50 148.507/296.088/1030.851 ms, pagination 33.531 ms, SSE replay 65.14 ms; frontend contracts/types/build/audit at 88.50 KiB initial gzip; Playwright 36/36 plus cost 2/2, canvas drag/commit p95 25.4/2.9 ms. Standards and Spec reviews have no remaining blockers. C4-3 periodic re-sync and changed-chunk embedding evidence are complete; C4 can close.

## 2026-09-12 Provider 切片收口:Agent 模型链显式负载均衡

- 关闭 Backlog Provider 行的最后一项「显式负载均衡策略」。`AgentConfig` 新增 `load_balance`(`LoadBalanceStrategy = Literal["failover", "round_robin"]`,默认 `failover` 保持既有严格首选语义),贯通全部四个链调用点:Agent 流式(`agent.py`)、Agent 工具循环两处(`agent_tools.py` 首轮与末轮)、supervisor 决策(`supervisor.py`)。
- `round_robin` 在 `CompileContext.model_chat_chain` / `model_stream_chat_chain` 内经进程级原子游标(`itertools.count` + `threading.Lock`)按调用轮转起点,把连续请求分散到整条模型链;失败仍在轮转后的剩余序中按序故障转移并照发脱敏 `provider_fallback` 事件;首 chunk 已发出的流不切换(沿用既有语义)。轮转刻意为进程级,与 U3/I1 的进程级模型调用限制口径一致,多 worker 各自轮转。
- 字段由 `config_model.model_json_schema()` 自动进入 `/api/node-types`、SchemaForm(enum select,LABELS 增「负载均衡」)与 Copilot 提示词;`workflowStore.defaultConfig` 新建 Agent 节点带显式默认值。节点 `config` 在公共契约中是开放对象,`python -m scripts.contracts check` 通过,**契约无变更**。
- 测试:新增 `tests/test_provider_load_balance.py` 7 个(默认 failover 三连调用全部命中链首 / round_robin 三链逐次轮转分布 / 轮转中瞬态故障转移到下一家且事件含脱敏 reason / 流式轮转 + 首 chunk 后失败不换流不惊动第三家 / DSL 校验拒绝未知策略并接受两种合法值);autouse fixture 重置游标保证确定性。既有 `test_resilience` 链回归 15 个全过。
- 门禁:后端全量 pytest、Ruff、mypy 全库、`scripts.contracts check`;前端 source/E2E typecheck、生产构建与 bundle 预算(初始 gzip 108.36 KiB < 120 KiB,与本切片无关的既有水位)。CHANGELOG 与商业路线图 Backlog 行已同步标注完成。

## 2026-09-12 附带修复:SQLite create_all 不再被 tsvector 表达式索引击穿

- 全量回归暴露 3 个预存失败(HEAD 基线同样失败,来源 `5fc5075`):`test_subworkflow_node` ×2 与 `test_templates`。根因是该提交为 PostgreSQL autogenerate 对齐把 `ix_document_chunks_text_tsv`(`to_tsvector('simple', text)` GIN 表达式索引)加进 `DocumentChunk` ORM,但未按方言门控——0035 迁移本身有 `postgresql` 守卫,而 `Base.metadata.create_all` 走的是元数据 DDL,SQLite 上直接 `no such function: to_tsvector`。
- 修复:模型上挂 `before_create`/`after_create` 监听,非 PostgreSQL 方言在建表 DDL 期间摘除该索引、完成后原样恢复;元数据本身不变,PG autogenerate 对齐保持。应用自身的建表路径(Alembic)本就不受影响。
- 验证:3 个原失败测试通过;迁移/方言回归集(test_migrations、test_database_backends、test_mcp_catalog)47 passed;Ruff/mypy 干净。修复后全量后端套件应完全绿(952 passed / 20 skipped,以最终运行为准)。

## 2026-09-12 切片:迁移头一致性门(C9 审计建议落地)

- 关闭 C9 审计 13.6 的建议项:CI 增加 `CURRENT_REVISION == alembic head` 断言。新增 `backend/scripts/check_migration_head.py`(经 `ScriptDirectory.get_heads()` 纯文件系统取头,与 `app.db.migrations.CURRENT_REVISION` 比对;多头直接拒绝,无需数据库),`quality` job 在契约检查后新增「Migration head parity」步骤。
- 同步新增 `tests/test_migration_head.py` 4 个:真实图谱一致、多头失败、陈旧 revision 失败(含修复指引文案)、匹配通过(monkeypatch 隔离,不依赖真实迁移目录状态)。
- 背景:`CURRENT_REVISION` 同时把守 `/readyz` 迁移检查、scheduler 与 event relay 启动门;`5fc5075` 缺这道门时三处同时静默失效。此门使该类缺陷在 CI 与常规套件都被拦截。
- 验证:脚本本机 `parity ok: 0047_evaluation_policy` 退出 0;4 测试过;Ruff/mypy 干净;ci.yml 经 PyYAML 解析校验(本机无 actionlint)。CHANGELOG 与 C9 文档 13.6 已同步标注。

## 2026-09-12 附带修复:评测页两个 e2e 预存超时(C5-1 壳重构遗留)

- `agentcanvas.spec.ts` 的 dataset 与 knowledge 两用例自 C5-1 信息架构重构起持续超时:全局持久侧栏引入后,页面出现两个 `complementary` 地标,测试的 `getByRole("complementary").first()` 命中平台导航而非评测页数据集列表,永远等不到数据集按钮(上一提交已确认为预存失败)。
- 修复:给评测页两个 `<aside>` 补可访问名称(`数据集列表` / `评测报告列表`,同时是无障碍改进),两处测试定位器改为 `getByRole("complementary", { name: "数据集列表" })`,不再依赖 DOM 顺序。
- 验证:两用例 2/2 通过(8.7s / 29.8s);完整 `agentcanvas.spec.ts` 8/8(C5-1 以来首次全绿);前端 typecheck ×2、构建与 bundle 预算(108.37 KiB < 120 KiB)通过。

## 2026-09-12 附带修复:应用嵌入脚本被 C8-3 CORP 头跨源阻断

- 默认 Playwright 全量暴露 `app-embed` 预存失败。经临时诊断 spec 抓包定位:`embed.js` 响应带 `Cross-Origin-Resource-Policy: same-origin`(C8-3 全站安全头中间件默认值),外部宿主页加载时浏览器直接 `ERR_BLOCKED_BY_RESPONSE.NotSameOrigin`,气泡永不挂载——C3-3 嵌入功能自 2026-08-24 起在真实浏览器全坏,e2e 因「Failed to load resource」被错误捕获过滤器忽略而长期未暴露为产品缺陷。
- 修复:embed.js 端点显式声明 `Cross-Origin-Resource-Policy: cross-origin`(中间件尊重已存在头);`test_app_runtime` 增断言钉住。安全边界不变:谁能拿到脚本仍由 403/404 门控,谁能内嵌运行时仍由 frame-ancestors allow-list 决定,CORP 仅表示「本资源设计上允许跨源读取」。
- 同片把命令面板两个预存失败结构化:C7-3/C7-4 往导航注册表尾追加平台管理与成员目的地后,测试硬编码的「末项=审计日志/项目配额」失效(焦点环与 End/Home 行为本身正常)。改为动态取最后一项(admin 用 toBeFocused,viewer 用选项稳定 id 的 toHaveAttribute),不再随注册表增长失效。
- 验证:app_runtime 23/23、C8 回归 11/11、Ruff/mypy 干净;command-palette 8/8、app-embed 1/1(2.9s)。workflow-history 的偶发失败经旧树对照与单独复跑确认为全量负载下的画布拖拽抖动,单独跑 5/5,不属持续回归。

## 2026-09-12 收口:默认浏览器门 87/88,残余 1 例为负载抖动

- 本轮四笔提交(9014d60 迁移头门、d9eb0f1 评测页地标、6ad3038 嵌入 CORP、490ba7a 面板断言结构化)后,默认 Playwright 全量从 84 过/4 挂提升到 **87 过/1 挂**;此前持续失败的 agentcanvas×2、command-palette×2、app-embed 全部转绿。
- 残余失败 `citation-experience`(16.8s 单独复跑通过)与 `workflow-history`(5/5 单独通过)同属全量并发下的画布/RAG 重负载时序抖动,旧树对照确认非本轮引入。后续如需绝对全绿,可考虑给这两个重 spec 提高超时或拆分并发,不在本片范围。
- 本轮后端改动均为聚焦验证(迁移头 4 测试、app_runtime 23/23、C8 回归 11/11、Ruff/mypy);952 全绿基线出自 f293694,其后两笔后端提交逐文件验证,全量由 CI 兜底。

## 2026-09-12 收口验证:tip 全绿 + C6 性能门复核 + 抖动用例超时补齐

- 提交 tip(490ba7a + 本片)重立完整基线:后端全量 **956 passed / 20 skipped / 0 failed**(952 + 4 个迁移头测试)。
- 独占复跑 C6 性能门全部守住:执行创建 p95 c1/c10/c50 207.3/447.1/1860.7ms(门 <500/1500/15000)、10k 分页 76.1ms(门 <300)、1000 事件 SSE 重放 110.8ms 连续无重复终态、500 节点/2000 边画布 dragPaint p95 **35.2ms** / React commit p95 21.8ms / maxLongTask 0ms(门 <100)。壳重构与近六个提交无性能回退。
- Copilot 与 model_discovery 服务代码走查:修复环、错误回喂、用量累计、SSRF 安全出站与脱敏实现均正确,无发现。
- chromadb PYSEC-2026-311 复核:本机 1.5.9 已是 PyPI 最新,上游无修复版本,按既定风险接受继续挂账(C9 桌面版将整棵排除 chromadb)。
- 抖动治理:workflow-history 拖拽用例缺自定义超时(默认 30s,全量负载下被击穿),补 75s 与同文件软锁用例一致;citation-experience 已有 150s 仍两轮中偶败一次,无失败上下文可查,先记录不盲修。spec 5/5 复跑通过。

## 2026-09-12 C9-1 Spike 完成:SSE 自定义协议透传被源码级否决,定案 B'

- 按 C9 计划 4.1 的要求执行了先行 Spike,但以**源码级取证**替代运行时实验,直接核对项目将采用的确切发布线 `tauri-v2.11.5` 与 `wry-v0.57.0`(WebView2 后端),两层独立证据:
  1. Tauri 公开 API `UriSchemeResponder.respond<T: Into<Cow<'static, [u8]>>>` 为 `FnOnce` 单次调用,响应体必须一次性物化——API 面不存在 partial-write/chunk(`crates/tauri/src/app.rs` L2455-2462);
  2. wry 在 WebView2 经 `AddWebResourceRequested` + deferral 处理自定义协议,`prepare_web_request_response` 用 `SHCreateMemStream` 把完整 body 物化后才 `SetResponse` + `Complete()`(`src/webview2/mod.rs` L1179-1200)——`EventSource` 只能在上游流结束后一次性收到全部数据。
- 两层相加是构造性结论:经 `register_(a)synchronous_uri_scheme_protocol` 的响应**不可能**增量到达页面;这不是可修 bug 而是公开 API 不表达该能力。wry 的 `ProxyConfig` 只是 HTTP-CONNECT/SOCKS 网络代理,与协议桥接无关。理论出路是 fork wry 自实现活 `IStream`,超出成本边界,不做。
- **定案:方案 B 不可行,采用 B'**——REST 继续走协议代理(JSON 缓冲语义恰好正确,fetch 零改动);三处 SSE 直连 `http://127.0.0.1:<动态端口>`(端口由 Rust 初始化脚本注入);后端 CORS 显式放行 `tauri://localhost` 与 Windows 实际 origin `http://tauri.localhost`,拒绝 `*`,桌面模式不启用 HSTS;CSP 的 `connect-src` 需纳入直连目标。
- 文档回写:C9 计划 3.2 增加结论块、4.1 Spike 标记完成(正式实现无需再建验证工程)、5.2 CORS/CSP 条款按定案改写、11 节风险表该行关闭。
- 佐证方式说明:源码级证明强于单次运行实验——实验只能证明某一次缓冲,源码证明不存在另一条路径;取证均锚定确切 git tag 与文件行号,可复核。

## 2026-09-12 C9-1 片 1:后端 desktop 配置 profile

- 落实 C9 §5.1:`APP_PROFILE=desktop` 固化单机降级组——强制 `app_host=127.0.0.1`、`process_role=all`、`redis_url=""`、`vector_backend=sql`(13.4:桌面包整棵排除 chromadb),`APP_PORT`/`APP_DATA_DIR` 保持环境注入(Rust 侧只传这两个变量);CORS 追加 `tauri://localhost` 与 `http://tauri.localhost`(Windows WebView2 实际 origin),供 B' 的 SSE 直连使用。
- `validate_runtime_settings` 对 desktop profile fail-fast:拒绝 PostgreSQL、非 all 角色、外置 Redis、非回环绑定;未知 profile 值在 load_settings 即拒绝。
- 测试:`tests/test_desktop_profile.py` 6 个(强制默认/端口与数据目录注入/拒绝 PG/拒绝外置 Redis 含 fail-closed 直构/未知值/standard 不受影响);release_contract + health 回归 17 过;Ruff/mypy 干净。

## 2026-09-12 C9-1 片 2:桌面外壳骨架 + sidecar 打包实证

- **后端 desktop profile**(aa44214):`APP_PROFILE=desktop` 固化单机降级组(见上一条目),validator fail-fast。
- **src-tauri 骨架**:`frontend/src-tauri/` 新增 Tauri 2.11 壳。supervisor/job 拆为独立子 crate `crates/supervisor`(不依赖 tauri,可单测):空闲端口申请、`/readyz` 轮询(250ms 间隔、可配超时)、sidecar 注入 `APP_PROFILE/APP_HOST/APP_PORT/APP_DATA_DIR/STARTUP_MIGRATIONS`、Windows Job Object `KILL_ON_JOB_CLOSE` + 显式 kill 的双保险回收、单实例插件、`AGENTCANVAS_BACKEND_URL` 复用已跑后端的 dev 模式。**cargo test 7/7**(含 job object kill-on-drop 实测、env 注入断言)、clippy -D warnings 全绿。
- 工具链定案(ADR 0003):`rust-toolchain.toml` 钉 MSVC(CI windows-latest 预装 VS 零配置);本机无 VS(C9 计划 §2.1 的判断与实情不符,2022 目录为空),本地用 `RUSTUP_TOOLCHAIN=stable-x86_64-pc-windows-gnu` 覆盖 + WebView2Loader.dll 放入 `target/debug/deps` 即可跑测试。GNU 下 cdylib 会触发 mingw ld "export ordinal too large",crate-type 固定 rlib(纯桌面壳无需 cdylib)。lib 测试 harness 无 tauri-winres manifest 会因 comctl32 v6 `TaskDialogIndirect` 入口点缺失而加载失败——这正是 supervisor 必须拆出 tauri 依赖树的直接原因。
- **PyInstaller onedir 实证**:spec 按 §13.4 整棵排除 chromadb 树,onesir 产物 **78.8 MiB**(目标 <300MB 大幅低于预估);SPECPATH 锚定路径、`--python 3.13` 对齐嵌入版本。**打包产物冒烟通过**:全新数据目录 → 47 个迁移自动执行 → `/readyz` 200,复现 2/2。
- **发现并解决 uvicorn 冻结缺陷**:冻结包内 `uvicorn.run()` 在应用启动阶段静默 exit 1(无 traceback、faulthandler 无输出、uvicorn 自身 ERROR 日志缺失);改用 `uvicorn.Config` + `uvicorn.Server.run()` 后正常,复现稳定。debug_entry 分步定位证明 build_container 本身无恙,差异锁定在 uvicorn.run 的启动路径。此发现已写入入口脚本文档注释供后续切片追溯。
- CI:`desktop-shell` job(windows-latest,cargo check/clippy/test --workspace + 前端 dist 构建)入主工作流;§9.1 修订:PR 检查因 MSVC 钉定改在 Windows 而非 Linux。
- 文档:ADR 0003(三项决策 + Spike 判决)新建;C9 计划状态改为「C9-1 实施中」。
- 剩余(正式 C9-1 收尾):启动画面(渲染 /readyz checks 分项)、alembic 失败可见/重试面、安装包 NSIS 构建 + 体积门、干净机验收清单、桌面 OIDC 深链接与 embed 取舍。
- 收口门禁:全量后端 **962 passed / 20 skipped / 0 failed**(18 分钟完整跑,含 aa44214 config 变更);Ruff/mypy 干净;ci.yml PyYAML 校验过;工作树干净,tip = c3edf80。

## 2026-09-12 C9-1 片 3:启动画面与失败可见化

- supervisor 子 crate 扩展:`ReadyzSnapshot`(解析 `/readyz` 的 `status` + 逐项 `checks` 状态)、`wait_until_ready_with_progress`(进度回调 + 子进程存活监控——迁移失败等导致 sidecar 提前退出时**立即快失败**,不再空转到超时)、`StartupFailure::{Timeout, ChildExited}` 可显示错误、子进程 stdout/stderr 重定向到 `<data>/logs/sidecar.log`(`SidecarHandle.log_path` 供失败面展示)。**9/9 测试**(新增:进度快照序列、子进程退出快失败、日志文件内容断言),clippy `-D warnings` 全绿,完整 shell 二进制链接通过。
- 壳侧:`splash.html` 经 `include_str!` 内嵌二进制,由自定义 `splash` 协议伺服(不依赖后端与前端资产);Tauri 事件驱动(`splash:progress` 流式渲染 readyz 分项检查、`splash:ready` 后关 splash 开主窗、`splash:failure` 展示错误 + 日志路径 + 复制诊断按钮)。**失败时不再打开主窗**——splash 即失败面,避免用户看到连不上后端的死 UI。`withGlobalTauri` + capabilities 覆盖 `main`/`splash` 两窗。
- 本地验证注意:GNU 工具链下跑测试需 `WebView2Loader.dll` 在 `target/debug/deps`(从 webview2-com-sys registry 源复制);CI windows-latest 的 MSVC 无此步骤。
- C9-1 剩余:安装包 NSIS 构建 + 体积门进 CI、干净机人工验收清单、桌面 OIDC 深链接与 embed 取舍(§13.5)。
