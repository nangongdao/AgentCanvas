# AgentCanvas — 基于 MCP 协议的可视化多 Agent 编排与执行平台 · 总体规划

> 本文档是项目的总体设计与阶段规划(单一事实源),实施过程中如有架构调整需同步更新此文档。

## 1. 项目背景与目标

在 Web 画布上拖拽节点编排多 Agent 工作流 → 后端 LangGraph 动态执行 → Agent 调用 MCP 协议工具 → 执行过程 SSE 实时回传(节点高亮 + 流式输出)。

**定位**:简历作品 + 实际可用两者兼顾——架构有技术深度可讲,核心链路稳定可跑。

**已确认约束**:
- 技术栈基线:React 19 + TS + Vite 8 + @xyflow/react v12 + Zustand + Tailwind CSS 4 / Python 3.12+ + FastAPI + LangGraph 1.x + 官方 mcp SDK 2.x / Chroma / SQLAlchemy(SQLite 单机基线，PostgreSQL 归 I1)/ Redis(可降级)/ Docker Compose
- LLM 资源:OpenAI 兼容协议 API Key(.env 配 base_url + api_key)
- 推进方式:全栈均衡推进,每阶段各层都有最小可用版本
- 开发环境:Windows 11(PowerShell),Python 3.13.14（CI 覆盖 3.12/3.13）/ Node 24 / pnpm / uv(经 `python -m uv` 调用);Docker 运行验收由 Linux CI 承担
- 编码规范:单文件 200-400 行、工厂+注册器模式、config 驱动、frozen dataclass、全类型注解

## 2. 总体架构

```
Browser: React Flow 画布 ─ Zustand ─ SSE Client(重连+seq 重放)
   │ REST(JSON)                ▲ SSE 事件流
FastAPI(全异步)
   api/routes ─ services ─ ExecutionEngine ─ EventBus ─ SSE
                              │
                    WorkflowCompiler(DSL JSON → LangGraph StateGraph)
                              │
                    LangGraph(AsyncSqliteSaver checkpointer)
                      ├ agent 节点 → Provider Registry(openai兼容/anthropic/ollama)
                      ├ tool 节点  → McpConnectionManager(stdio/sse/streamable-http)
                      ├ rag 节点   → Chroma + embedding 缓存
                      └ human 节点 → interrupt()/Command(resume)
   SQLAlchemy(SQLite/PG) ── Redis(可选,ping 失败自动降级 SQLite)
外部进程: 内置演示 MCP servers(calculator/filesystem/websearch, stdio)
```

## 3. 关键技术决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 实时通信 | **SSE**(sse-starlette),不用 WebSocket | 执行事件是纯单向流;SSE 原生 Last-Event-ID 断线重放;上行操作(审批)走 POST |
| 事件重放 | execution_events 表 seq 自增 + SSE `id:` 字段 | 重连自动续传:先订阅实时队列→DB 补历史→按 seq 去重,精确一次 |
| checkpointer | AsyncSqliteSaver 默认 / AsyncPostgresSaver 可切;**不用 RedisSaver** | checkpoint 需持久性,不能建在可降级组件上 |
| 会话记忆 | MemoryStore Protocol:Redis 首选,ping 失败降级 SQLite | 满足降级约束,降级结果暴露在 /api/meta |
| 节点配置表单 | 后端 /api/node-types 返回 JSON Schema,前端 SchemaForm 渲染 | 节点类型单一事实源在后端注册器,前后端不漂移 |
| 流式 token | agent 节点内直接把 Provider StreamChunk 发 EventBus,**不依赖 LangGraph astream 内部协议** | SDK 升级不脆断 |
| API Key 存储 | model_configs 表 Fernet 加密,SECRET_KEY 来自 .env | 不明文入库 |
| React Flow | @xyflow/react v12 | v11 已停更 |
| 依赖管理 | 后端 uv + pyproject.toml,前端 pnpm | 锁文件保证快速演进的 SDK 版本可复现 |

**版本锚点**:`langgraph>=1.0.10,<2`、`langgraph-checkpoint-sqlite>=3.0.1,<5`（checkpointer 显式 strict serde 白名单，关闭 pickle 回退）、`mcp>=2.0,<3`、`httpx2>=2.5,<3`（MCP transport）、`fastapi>=0.115`、`sqlalchemy[asyncio]>=2.0`、`chromadb>=0.5`（仅嵌入式 PersistentClient）、`sse-starlette`、`redis>=5`、`cryptography`、`pypdf`、`httpx`（应用 Provider）、`aiosqlite`。前端 `vite>=8,<9`、`react-router-dom>=7.18.2,<8`。

## 4. 目录结构(核心文件)

```
E:\xiangmu\
├── README.md / docker-compose.yml / .env.example / docs\
├── backend\
│   ├── pyproject.toml / uv.lock / alembic\
│   ├── data\                    # app.db / checkpoints.db / chroma\ / uploads\ (gitignore)
│   ├── mcp_servers\             # MCPServer 演示服务器:calculator.py filesystem.py websearch.py
│   └── app\
│       ├── main.py              # create_app() + lifespan
│       ├── core\{config,logging,security,container}.py   # frozen Settings / Fernet / 手写 DI
│       ├── db\{base.py, models\*, repositories\*}        # 按域拆分 ORM + Repo
│       ├── schemas\{dsl.py, events.py, api\*}            # DSL pydantic 单一事实源
│       ├── engine\              # ★ 核心
│       │   ├── state.py         # WorkflowState TypedDict + reducers(并行安全)
│       │   ├── validation.py    # DSL 静态校验(可达性/句柄/环检测)
│       │   ├── templates.py     # {{input.x}}/{{nodes.id.output}} 解析(无 eval)
│       │   ├── conditions.py    # 规则求值器
│       │   ├── compiler.py      # WorkflowCompiler: DSL→StateGraph
│       │   ├── executor.py      # ExecutionEngine: start/resume/cancel
│       │   ├── events.py        # EventBus(内存版,接口预留 Redis 版)
│       │   ├── checkpoint.py    # make_checkpointer() 工厂
│       │   └── nodes\           # NodeRegistry + @register_node
│       ├── providers\           # ★ PROVIDERS 注册器 + BaseChatProvider(统一流式+工具调用归一)
│       ├── mcphub\              # ★ 不可命名 mcp(遮蔽官方包);owner-task 连接管理
│       ├── rag\{loaders,splitter,embedder,store,ingest}.py
│       ├── memory\{base,redis_store,sqlite_store,factory}.py
│       ├── services\            # 业务层 + seeds.py
│       └── api\routes\          # workflows executions(SSE) node_types mcp models knowledge sessions meta
└── frontend\src\
    ├── api\{client.ts, sse.ts(指数退避重连+lastSeq), endpoints\*}
    ├── types\{dsl,events,api}.ts
    ├── stores\{workflowStore,executionStore,resourceStore,uiStore}.ts
    ├── features\
    │   ├── canvas\{FlowCanvas, nodes\registry+各节点, panels\{NodePalette,ConfigPanel,SchemaForm,RunDialog}}
    │   ├── execution\{ExecutionDrawer,EventTimeline,StreamingText,useExecutionSSE,streamBuffer}
    │   ├── workflows\ mcp\ models\ knowledge\ chat\
    └── components\ utils\
```

## 5. 工作流 DSL Schema

顶层:`{version, name, variables[], settings{max_loop_iterations,timeout_seconds,recursion_limit}, nodes[], edges[]}`

- 节点类型:start / agent(simple|react|supervisor 模式) / tool / condition / rag / human / end
- **分支语义走边的 `source_handle`**(condition 每个 branch id = 一个 Handle id,`else` 兜底)
- **并行** = 同 source 多条默认边;**汇聚** = 编译器按 target 分组生成 `add_edge([a,b], join)`
- 模板语法:`{{input.x}}` `{{nodes.<id>.output}}` `{{vars.x}}`,templates.py 解析,无 eval
- condition operator:eq/ne/gt/gte/lt/lte/contains/not_contains/regex/is_empty/not_empty
- supervisor:`agent_mode:"supervisor"` + `workers:[...]`,LLM 输出 JSON `{"next":"<worker>|FINISH"}` → `Command(goto=...)`,loop_counts 限最大轮数

DSL 示例:

```json
{
  "version": "1.0",
  "name": "客服工单分流",
  "variables": [{ "name": "user_query", "type": "string", "required": true }],
  "settings": { "max_loop_iterations": 20, "timeout_seconds": 300, "recursion_limit": 50 },
  "nodes": [
    { "id": "start_1", "type": "start", "position": {"x": 0, "y": 100},
      "config": { "input_schema": [{"name": "user_query", "type": "string", "required": true}] } },
    { "id": "agent_1", "type": "agent", "position": {"x": 440, "y": 100},
      "config": { "model_config_id": "default", "agent_mode": "react",
                  "system_prompt": "你是客服。", "user_prompt": "{{input.user_query}}",
                  "tools": [{"server_id": "mcp_calc", "tool_name": "eval_expr"}],
                  "max_tool_rounds": 5, "params": {"temperature": 0.3} } },
    { "id": "end_1", "type": "end", "position": {"x": 1100, "y": 100},
      "config": { "output_template": {"answer": "{{nodes.agent_1.output}}"} } }
  ],
  "edges": [
    { "id": "e1", "source": "start_1", "target": "agent_1" },
    { "id": "e2", "source": "agent_1", "target": "end_1" }
  ]
}
```

## 6. 编译器核心设计

- `WorkflowState` TypedDict:`node_outputs: Annotated[dict, merge_reducer]`(并行分支写入必须有 reducer,否则 InvalidUpdateError)、`loop_counts`、`messages`、`route`、`error`
- `BaseNodeExecutor.build(node, ctx) -> NodeFn` 闭包工厂;CompileContext(frozen)持有 providers/mcp_manager/rag_service/emitter
- 所有 NodeFn 经 `instrument()` 包装:发 node_started/finished/failed 事件、计时、token 统计、循环计数检查(双层保护:loop_counts + recursion_limit)
- 校验:恰一个 start、≥1 end、边端点存在、handle 匹配、start 全可达、纯静态环报错(仅允许经 condition/supervisor 的环)
- 执行:`graph.astream()` 驱动;GraphInterrupt → waiting_approval + workflow_interrupted 事件;运行 Task 登记 dict 支持 cancel
- checkpointer:单机 SQLite 使用 `AsyncSqliteSaver`,I1 PostgreSQL 使用 `AsyncPostgresSaver` + 有界 Psycopg pool,两者均配显式 strict serde 白名单(`JsonPlusSerializer(allowed_json_modules=("core",))`,无 pickle 回退);生产初始化或 waiting approval checkpoint 完整性失败 fail-closed 抛 `CheckpointerUnavailable`,`/readyz` 暴露实际 durable backend;旧 `checkpoints.db` 可通过 `python -m app.services.migrate_checkpoints --source ...` 幂等迁移到 PostgreSQL;`ExecutionEngine.shutdown(grace_period)` 先拒新任务、宽限等待、超时取消并保证一致终态

## 7. MCP 集成要点

- **owner-task 模式**:mcp SDK 的 anyio 上下文必须同 task 进出 → 每连接一个 owner task,内部 `async with Client(mode="auto")` 自动协商 2026-07-28 并回退旧服务器,ready/shutdown Event 协调；list/call 请求经队列回到 owner task 执行
- 懒连接 + 后台 reaper(60s 扫描,空闲>10min 断开)+ 工具 schema 缓存入库(tools_cache_json)
- 两种用法:tool 节点(确定性调用+超时重试)/ agent 绑定工具(input_schema→OpenAI function 格式,tool-call 循环上限 max_tool_rounds,轨迹发 node_streaming kind:"tool_call")
- Windows:npx 型 server 用 `cmd /c npx`;子进程 env 补 PYTHONUTF8=1;不用 uvloop
- 内置 3 个 MCPServer demo server;filesystem 沙箱限定 data/workspace(resolve 后校验前缀防穿越)

## 8. SSE 事件协议

- 类型:workflow_started / node_started / node_streaming / node_finished / node_failed / edge_taken / workflow_interrupted / workflow_finished / workflow_failed / workflow_cancelled
- 写路径:instrument → EventBus.publish → 内存队列分发 + 50ms 攒批落 execution_events 表；终态关闭订阅前等待当前持久化批次排空
- 读路径:先 subscribe 防丢 → Last-Event-ID 从 DB 重放 → 实时队列按 seq 去重
- 心跳:15s 注释行;前端指数退避重连(1s→30s),收终态事件即关闭

## 9. 数据库表

| 表 | 关键列 |
|---|---|
| workflows | id, name, dsl_json, version(乐观锁), is_archived, created_at, updated_at |
| executions | id, workflow_id, status(running/succeeded/failed/cancelled/waiting_approval), input_json, output_json, error, thread_id, session_id, started_at, finished_at |
| execution_events | id, execution_id, seq, event_type, node_id, payload_json, ts;唯一索引(execution_id, seq) |
| mcp_servers | id, name, transport(stdio/sse/streamable_http), command, args_json, env_json, url, headers_json, enabled, tools_cache_json, tools_cached_at, last_status |
| model_configs | id, name, provider, model_name, base_url, api_key_encrypted, params_json, kind(chat/embedding), is_default |
| knowledge_bases | id, name, embedding_model_id, chunk_size, chunk_overlap |
| documents | id, kb_id, filename, file_path, mime, size_bytes, status(pending/processing/ready/failed), chunk_count, error |
| embedding_cache | hash(sha256) pk, model, dim, vector(BLOB) |
| sessions / messages | 会话与消息(含 node_id/execution_id 关联) |
| evaluation_datasets / evaluation_dataset_versions | 数据集元数据与不可变样本快照 |
| evaluation_runs / evaluation_case_results | 固定工作流/数据集版本的评测汇总、逐样本执行与评分证据 |
| evaluation_comparisons | 同一数据集快照上两个已发布工作流版本的 A/B 汇总与运行引用 |
| users / sessions / refresh_tokens / oidc_identities | 本地或 OIDC 用户、短期访问会话、单次轮换刷新令牌族与外部身份绑定 |
| organizations / projects / memberships | 组织、项目和项目级 viewer/editor/admin 成员关系 |
| service_accounts / api_tokens | 服务账号及仅保存 SHA-256 的可撤销 API Token |

## 10. 性能优化清单(简历可写)

**后端**:全链路异步(aiosqlite/httpx 连接池复用/chroma to_thread)、LLM 流式零缓冲转发(记录 TTFT)、LangGraph superstep 原生并发+reducer、MCP 连接复用(冷启动数百 ms→<5ms)、embedding 64/批+Semaphore(4)+sha256 缓存、SQLite WAL+事件攒批

**前端**:节点 React.memo + **流式文本不进 node.data**(存 executionStore Map,节点 selector 细订阅)、onlyRenderVisibleElements 视口裁剪、rAF 批量 flush 流式文本、自动保存 debounce 800ms+版本乐观锁、执行路径 CSS 动画、长列表虚拟滚动

## 11. 阶段划分

| 阶段 | 交付物 | 验收标准 | 状态 |
|---|---|---|---|
| **P0 骨架** | uv+FastAPI /healthz+Settings;Vite+RF 空画布;git init | 两端起服务互通,画布可拖节点 | **已完成** |
| **P1 最小闭环** | DSL v1(start/agent/end)+线性编译器+openai Provider+ExecutionEngine+SSE+前端 palette/配置/Run/高亮/流式 | 画 start→agent→end 运行:节点变色、文字逐字出;刷新页面重放完整时间线 | **已完成** |
| **P2 编排加深** | condition/并行 join/循环保护/supervisor;checkpointer;edge_taken 高亮;历史页 | 三种模式种子工作流可跑;死循环被拦截;compiler 单测绿 | **已完成** |
| **P3 MCP** | mcphub 三传输+懒连接+缓存;tool 节点;agent 工具循环;3 个 demo server;管理页 | calculator 发现→调用成功;agent 自主调用工具轨迹上时间线 | **已完成** |
| **P3.1 稳定化** | Alembic+重启恢复;工作流产品闭环;SchemaForm/RunDialog;Token RBAC;MCP secret/命令策略;CI+Playwright | 迁移/恢复可回归;深链与自动保存稳定;API 不泄密;角色边界与主路径浏览器测试持续通过 | **已完成** |
| **P4 RAG** | 上传→ingest 状态机→Chroma;批处理+缓存;rag 节点;KB 页 | pdf/md 上传 ready;rag 命中注入 agent 显示来源;重复上传命中缓存 | **已完成** |
| **P5 记忆+human+多 Provider** | MemoryStore 降级;Chat 页;human interrupt/resume;anthropic+ollama;模型页 | 停 Redis 仍多轮对话;审批暂停→续跑;切 ollama 跑通 | **已完成** |
| **P6 性能+部署** | 优化清单落地;Dockerfile×2+dev/prod compose+独立迁移+备份恢复+nginx;seeds;README | 容器发布 smoke;100 节点与后端基准;观测门 | **进行中**（U2/U3/U4 实现和本地门禁已完成；当前后端 494 passed / 5 skipped、真实 PostgreSQL marker 5/5，bundle/画布/API/MCP/RAG/SSE 基准通过；实际容器构建/扫描仍等待 CI runner） |
| **D1 生命周期+复用** | 不可变版本;发布/diff/回滚/克隆;DSL 导入导出/迁移;模板库 | 历史执行可还原;发布版本不被草稿覆盖;旧 DSL 语义一致 | **已完成**（`0006`/`0007`、版本/模板 UI、真实数据迁移及完整门禁通过） |
| **D2 调试+评测+成本治理** | 节点检查器;失败重跑;版本数据集;A/B;RAG 指标;预算与成本告警 | 报告可追溯到版本/节点/输入;质量/时延/费用可比较 | **已完成**（Phase 1-7 与全量门禁完成） |
| **D3 身份+多用户+协作** | 本地/OIDC 身份;刷新令牌;组织/项目/RBAC;服务账号;协作;审计;配额 | 跨租户资源不可越权;身份凭据可轮换撤销;协作冲突可见且可恢复 | **已完成**（Phase 1-6 全部关闭；五类项目配额及隔离、管理界面、竞态/恢复均有完整门禁） |
| **D4 Provider/MCP 生态与平台治理** | Provider 能力矩阵;保存/发布前能力校验;健康检查、熔断、重试与 fallback;MCP catalog;插件 SDK;secret provider;版本化契约 | 不兼容能力在保存/发布前阻止;新增 Provider/节点不改 ExecutionEngine 主流程;权限可审计 | **已完成**（Phase 1-6 与完整质量/性能/依赖门禁通过；下一路线为 I1） |
| **I1 分布式执行与存储** | PostgreSQL;耐久 checkpointer;队列/租约;共享事件;worker-owned MCP;多实例向量存储 | 任意 API 可查询/订阅;worker 故障可恢复且 stale owner 被 fencing;审批可跨重启恢复 | **进行中**（Phase 1-8 实现及宿主机 PostgreSQL/pgvector、2 API + 2 worker、故障接管、全栈重启和隔离恢复证据完成；真实 Redis Streams 重复投递及 Docker 故障编排仍等待 CI） |

每阶段结束打 git tag(v0.1…)。

### D4 Phase 1 Provider 能力矩阵（2026-08-08）

`0020_provider_capabilities` 为 Provider 和模型配置建立能力治理边界。适配器默认能力保持不可变，模型级覆盖只能收窄；工作流的 Agent DSL 在八个写入边界前批量检查精确模型、模型类型和所需能力。模型管理页提供能力矩阵和桌面/移动端收窄控件，执行/resume 按精确模型 ID 加载 Provider，JSON 参数整形归属 Provider。

真实库迁移前备份为 `backend/data/backups/pre-d4-phase1-20260808-121500.tar.gz`（SHA-256 `B3122B4AC37E556A4E65BB9A645EAA44B1217DA1EF699C66F3A0D7B40D786708`），从 `0019` 升至 `0020` 后保留 11 工作流、11 版本、17 执行和 3871 事件；隔离恢复验证回到 `0019` 且计数一致。完整后端 362/362，前端构建初始 gzip 79.72 KiB，默认 Playwright 23/24，独立性能和成本门分别 1/1、2/2。下一实施流为 D4 Phase 2。

### D4 Phase 2 Provider/MCP 弹性治理（2026-08-08）

Provider 与 MCP runtime 现在共享注入式 resilience registry，提供健康快照、熔断状态、有限重试预算和可控故障注入；`/api/resilience` 与 MCP health API 暴露脱敏状态。fallback chain 在候选模型缺失、重复或能力不满足时 fail closed，并且流式响应只允许在首个 chunk 前切换，避免重复输出。Provider/MCP 的连接、列工具、stream retry、熔断半开和故障恢复均有确定性测试覆盖，ExecutionEngine 没有新增 Provider-specific 分支。

后端完整门为 381/381，Ruff 与 mypy（256 个文件）通过；前端 typecheck、E2E typecheck、production build 与 bundle budget 通过，初始 gzip 79.72 KiB；focused MCP 浏览器 1/1、成本治理 2/2 通过。默认浏览器套件功能路径通过，但画布性能用例在整套运行及隔离复测中分别记录 `dragPaintP95=179.6/163.4 ms`，React commit p95 为 `18.5 ms`，保留 100 ms 阈值并作为后续性能风险处理。

### D4 Phase 3a Approved MCP catalog（2026-08-08）

迁移 `0021_mcp_catalog` 新增 catalog entry/version/history，以及 MCP server 的可空绑定。manifest 声明 transport 与 network/filesystem/commands 权限；版本只能由管理员批准或撤销，旧 approved 版本转为 superseded，历史与安全审计不保存 secret。绑定与运行时加载会再次校验 stdio 命令/脚本根目录或 HTTP host 权限，revoked、错 entry 或未批准版本不能建立连接。MCP 面板已能读取 approved 版本并绑定/解绑；三个内置 demo MCP 服务已 seeded 为 approved v1。

真实库从 `0020` 升至 `0021`，11/11/17/3871 核心数据保持不变，catalog 为 3 entries / 3 versions / 3 history rows / 3 bound servers；备份 `backend/data/backups/pre-d4-phase3-20260808-170218.tar.gz` 的 SHA-256 为 `06865E79302A35A08F0BAA4749DF4F1E58BC5B5D5153BE473BD91F6A25C80E2A`。Phase 3b 已补充 catalog 管理、升级 diff/rollout 控制、幂等和安全审计；Phase 4 首个插件 SDK 切片也已完成，下一步进入 secret provider 与契约版本化。

### D4 Phase 3b Catalog 管理、diff 与 rollout（2026-08-08）

Phase 3b 在现有 approved MCP catalog 上增加管理员工作区、版本权限 diff、升级预检和显式 rollout。预检只枚举目标 entry 已绑定的服务器，先按当前 MCP policy 判断兼容性；不兼容目标以原因返回且不发生 mutation。rollout 支持单个或批量兼容目标，变更目标会断开连接并清理 tool cache，重复目标保持 `unchanged`，同时写入聚合与逐服务器安全审计/历史记录。diff 未指定 base 时默认取目标版本的直接前驱，editor 对预检和 rollout 均返回 403。

Phase 3b 验收：catalog 管理页和 API、focused RBAC/idempotency/policy/audit 回归、后端 383/383、Ruff、mypy 258 文件、前端 source/E2E typecheck、production build/bundle 初始 gzip 79.78 KiB、默认 Playwright 26/26、成本套件 2/2、`uv lock --check` 与 `git diff --check` 均通过；画布性能 p95 为 84.8 ms，React commit p95 为 11.0 ms。依赖审计仍有已知风险：`pypdf 6.14.2` 的两个 CVE 已在 6.15.0 修复，transitive `nanoid <3.3.17` 仍有 high advisory。

### D4 Phase 4 Versioned Node Plugin SDK（2026-08-08）

Phase 4 首个切片沿用 `BaseNodeExecutor` + `NODE_REGISTRY` 工厂边界，新增 `backend/app/plugins` 中的版本化 manifest/protocol、JSON Schema 配置校验、UI hints、权限声明和插件事件契约。`plugin.*` 节点可在 DSL 中 round-trip；未知插件或不合法配置会在保存/编译前失败，ExecutionEngine 主流程无需增加插件分支。`/api/node-types` 返回插件版本、协议、schema、UI hints 和 network/filesystem/command 权限，前端 Node Library 会动态发现并添加插件节点。

运行时每次调用启动一个新 Python 子进程，通过单请求/单响应 JSONL 通信；entrypoint 必须位于插件目录内，禁止 shell，环境仅保留 UTF-8/非缓冲变量，输入/输出字节数、超时、事件数、request ID 和 protocol version 均有边界校验并在 finally 中清理进程。内置 `backend/plugins/echo` 用于演示和回归。权限字段是可审计声明，并不冒充 Windows/Linux OS 级网络或文件系统沙箱；更强的系统级隔离留作后续硬化。

Phase 4 首个切片验收：插件专项 6/6、全量后端 389/389、Ruff、mypy 187 个应用文件、前端 source/E2E typecheck、生产构建/预算（初始 gzip 79.77 KiB）、默认 Playwright 27/27、成本浏览器 2/2、`uv lock --check`、`git diff --check` 和敏感信息扫描均通过。既有 `pypdf 6.14.2` 两个 CVE 与 transitive `nanoid <3.3.17` high advisory 仍未宣称清零；Phase 5 secret provider 已在下一节完成。

### D4 Phase 5 Secret Provider 引用与轮换首个切片（2026-08-08）

新增 `SecretResolver` 与固定 provider chain：`env://NAME` 解析进程环境，
`docker://relative-file` 仅在 `DOCKER_SECRET_DIR` 下进行路径约束和有界 UTF-8
读取，`external://path` 通过 `create_app(...,
external_secret_resolver=...)` 显式注入。引用仍使用既有 Fernet 字段加密，不新增明文列；
模型/MCP 列表不解析 provider，只返回掩码和 `stored/env/docker/external` 来源。
MCP 掩码更新保留原始引用，避免 secret provider 暂时不可用时把轮换值复制进数据库。
执行、embedding、MCP 连接和默认 seed 均在真正运行边界解析，缺失 provider fail closed。

首个切片的聚焦覆盖包括引用格式/路径逃逸/大小限制、环境缺失时列表与掩码更新、
外部解析器注入、旧值迁移和 API/运行时 secret 不泄露；最终门禁为后端 400/400、
Ruff、mypy 188 个应用文件、前端 source/E2E typecheck、生产构建/预算（初始 gzip
79.78 KiB）、默认 Playwright 27/27、成本浏览器 2/2、`uv lock --check`、
`git diff --check` 和敏感信息扫描。依赖审计仍保留已记录的 pypdf CVE 与 transitive
nanoid high advisory；这些依赖风险已在 Phase 6 收口。

### D4 Phase 6 Versioned Public Contracts（2026-08-09）

API `1.0.0`、Workflow DSL `1.0` 和 execution event `1.0` 现在由后端源码定义并写入 OpenAPI 扩展元数据。`contracts/current` 的 OpenAPI、DSL JSON Schema、event JSON Schema 和 manifest 均确定性生成；execution event envelope 新增固定 `schema_version`，现有持久化重放与 live SSE 字段保持不变。前端 OpenAPI/DSL/event 类型从这些产物生成，并用于 workflow/execution DTO、canvas DSL、SSE 与 execution store 边界。

`python -m scripts.contracts check` 同时检查生成漂移和不可覆盖的 major baseline，拒绝 path/operation/parameter/response/schema 删除、输入必填化、enum 值删除以及类型/范围/长度/pattern/union 等收窄；破坏性变化必须显式提升 contract major 并建立新基线。`pnpm contracts:check` 检查前端生成类型漂移，两项均已进入 CI。

最终门禁：后端 413/413、应用行覆盖率 85.11%、关键分支 84.27%、Ruff、mypy 275 文件、锁/契约检查和性能 smoke；前端 source/E2E typecheck、生产 build/bundle 初始 gzip 79.84 KiB、默认 Playwright 27/27、成本 2/2。性能 p95 为执行创建 c1/c10/c50 107.2/749.0/7158.5 ms、10k 分页 42.9 ms、连续 1000-event replay 82.2 ms；画布 drag/React commit 60.2/3.6 ms。`pypdf 6.15.0`、`js-yaml 4.3.1` 和 `nanoid 3.3.17` 关闭已记录 high 风险，后端无未豁免漏洞，前端 high 审计为 0。D4 全部关闭，下一本地路线为 I1。

### I1 Phase 1-2 ADR 与 PostgreSQL 基础（2026-08-09）

ADR 0001 将 PostgreSQL 定义为耐久事实源、Redis 定义为共享低延迟协调层，并明确 API/control plane、worker、scheduler、event relay 的所有权。队列采用 at-least-once 与 lease generation fencing；非幂等 MCP/plugin 副作用不被默认自动重放。数据库 URL 仅支持异步 SQLite/PostgreSQL driver，PostgreSQL 使用显式 pool size/overflow/timeout/recycle 与 pre-ping；并发 Alembic release step 由带超时的 session advisory lock 串行化，健康检查返回实际数据库 backend，日志只显示脱敏 URL。

PostgreSQL 17 CI lane 会从空库并发升级到 head、执行 Alembic schema drift 检查并覆盖组织/项目租户 CRUD。SQLite 迁移矩阵、开发模式与 SQLite-only 备份行为保持兼容。本地完整门禁为后端 427 passed/1 PostgreSQL skip、85.00% 应用行、84.27% 关键分支、Ruff、mypy 277 文件、依赖/锁/契约与后端性能预算；前端 source/E2E typecheck、生产 build/bundle 79.84 KiB、默认 Playwright 28/28、成本 2/2，画布 drag/React commit p95 71.9/6.8 ms。本机无 Docker/PostgreSQL，真实数据库测试需由 CI 完成。下一切片为 PostgreSQL 耐久 checkpointer 与 waiting approval 迁移。

### I1 Phase 3 PostgreSQL durable checkpointer（2026-08-09）

`make_checkpointer()` now selects `AsyncSqliteSaver` for `sqlite+aiosqlite` and
`AsyncPostgresSaver` for `postgresql+asyncpg`. PostgreSQL uses Psycopg's
explicit `dict_row`/autocommit connection settings, a bounded pool derived from
`DATABASE_POOL_SIZE`, and a session advisory lock around idempotent saver setup.
The strict LangGraph serializer remains enabled on both backends. Startup checks
every `waiting_approval` row against the active saver; a missing thread fails
closed and points operators to the migration command, so a database cutover
cannot silently strand an approval.

The migration command copies checkpoint parent chains and pending writes from a
legacy SQLite file into the configured PostgreSQL saver, then re-runs the
waiting-approval integrity check. It is intentionally an explicit release
operation; PostgreSQL backup/restore remains a separate later I1 gate. Focused
SQLite evidence covers copy-and-resume plus missing-checkpoint rejection, while
the PostgreSQL CI lane covers real setup, import, and full application restart.

### I1 Phase 4 耐久执行队列与 fenced worker lease（2026-08-10）

`0022_execution_queue` 引入 `execution_queue_items`：kind 为 `start|resume|rerun`，status 为 `queued|leased|retry_wait|dead_letter|done`，并用 `owner_id` + `lease_generation` 做 fencing。PostgreSQL claim 使用 `FOR UPDATE SKIP LOCKED`；SQLite 使用 CAS update。执行创建默认进入 `queued`，worker 领取后翻为 `running`；运行中 heartbeat 只续当前 fenced lease。

`InProcessExecutionWorker` 作为可嵌入或专用进程的队列消费者：poll/kick、并发槽、heartbeat、fenced complete/fail/cancel，以及 settings 中的 `EXECUTION_WORKER_POLL_SECONDS` / `EXECUTION_LEASE_SECONDS` / `EXECUTION_LEASE_MAX_ATTEMPTS`。start/resume/rerun 只入队并 kick，不再 `create_task` 直跑。`waiting_approval` 与队列释放在同一事务完成，避免 resume 与 worker post-run complete 竞态；启动恢复不触碰任何队列租约，仅对无队列跟踪的遗留 running 行 fail closed。项目配额 reconcile 计入 `queued` + `running`。

本地证据：queue/fencing/worker/recovery 与相关集成测试通过；全量后端 435 passed / 3 PostgreSQL skipped；ruff/mypy 触及模块通过。真实多 owner claim 与 PostgreSQL 17 证据仍由 CI 执行。

### I1 Phase 5 共享 durable EventBus 与多源 SSE（2026-08-10）

`0023_event_relay` 增加 `execution_event_relay_cursors`，以全局 `execution_events.id` 作为 relay 游标。`EventRelay` 在事件持久化后 kick，把已提交行按批发布到执行级 Redis Stream，并推进 cursor；无 `REDIS_URL` 时 relay 空闲。`iter_execution_sse` 按 ADR 顺序：本进程 subscribe + flush → PostgreSQL replay → Redis backlog + PostgreSQL gap → local bus / Redis tail / 有界 PG 轮询，并以 `(execution_id, seq)` 去重。相关 knobs：`EVENT_RELAY_POLL_SECONDS` / `EVENT_RELAY_BATCH_SIZE` / `EVENT_STREAM_MAXLEN`。

本地证据：relay/cursor/stream/SSE 测试通过；迁移 head 契约跟随 `0023_event_relay`；ruff/mypy 触及模块通过。

### I1 Phase 6 共享协作与 worker-owned MCP（2026-08-10）

无 `REDIS_URL` 时协作保持进程内 memory（`backend=memory`）。配置且可达 Redis 时，presence/soft-lock/revision 经 Redis 共享，多 API 副本观察同一锁与参与者。配置了 Redis 但不可达时 fail closed 为只读 hub（`backend=unavailable`），变更接口返回 HTTP 503，不回退分裂本地锁。快照暴露 `backend`/`read_only`；`can_edit &= not read_only`。

MCP live session 归持有 execution lease 的 worker：`McpManager.for_execution(execution_id)` 隔离连接表；控制面 test/discover/health 请求后释放 live session；resume 从持久配置在新的 worker-owned manager 上重连，不在 worker 间转移 live session。

本地证据：协作 Redis 与 MCP isolation/control-plane release 聚焦测试在 SQLite 上通过。多 API live Redis 协作仍属多实例 CI 义务。

### I1 Phase 7 多实例安全 RAG 向量存储（2026-08-10）

RAG 仅依赖异步 `VectorStore` 协议。SQLite `auto` 保持 embedded Chroma；PostgreSQL `auto` 使用 `0024_document_chunks` 的 pgvector `VECTOR` 列和数据库侧 cosine 排序。同一文档写入先锁 durable document row；chunk identity、1-16000 维、有限值、非零范数和单文档一致维度全部在替换旧索引前校验。`/api/meta`、`/readyz` 暴露实际 backend，pgvector health 同时报告 extension version。

`app.services.migrate_vectors` 从旧 Chroma 做两遍迁移：第一遍只验证所有 ready 文档的 KB/文件名/chunk 数，完整通过后第二遍才按文档幂等写入；缺失或不一致 fail closed。全量本地门为后端 458 passed / 4 PostgreSQL skipped、应用行 83.93%、关键分支 82.09%、Ruff、mypy 295 文件、锁/契约/审计、前端类型/构建/bundle、迁移漂移和性能预算。真实 SQLite 数据已从 `0021` 升到 `0024` 且 11/11/17/3871 行及外键完整。CI PostgreSQL 服务固定为 pgvector 0.8.6/PostgreSQL 17，并收集真实 server-side ranking 与跨 store 可见性测试；本机没有 vector 扩展/测试凭据，因此不以 skip 冒充通过。下一步 Phase 8 做 2 API + 2 worker 故障注入、真实数据迁移、PostgreSQL 灾备与发布收口。

### I1 Phase 8 多进程发布、故障注入与 PostgreSQL 灾备（2026-08-10）

`APP_PROCESS_ROLE=all` 保留 SQLite 单机；`api|worker|relay|scheduler` 仅允许 PostgreSQL + Redis 且要求 `STARTUP_MIGRATIONS=false`。API 不启动执行 worker 或 relay publisher；专用 worker 用实例 ID 领取队列，relay 的 durable cursor 行锁防止多发布者游标回退，scheduler 用 `FOR UPDATE SKIP LOCKED` 分类过期租约。worker 的执行终态和事件批次都校验同一 owner/generation；心跳失去租约会取消旧 task。API-only cancel 只写 durable cancel request，由持租约 worker 提交终态。

scheduler 不允许 worker 直接重领 expired lease：已提交终态只完成队列，取消请求提交 cancelled；无副作用 DSL 才在尝试预算内重排。含 tool、human、MCP tool/memory agent 或动态插件的模糊 worker loss 进入 dead letter，并在同一事务写失败事件、释放项目配额。事件 seq 在每次 worker 接管时从 PostgreSQL 最大值续接；Redis 启动/运行故障后 API connection supervisor 和 relay 都会重连，期间 SSE 走 PostgreSQL gap polling。

生产 Compose 固定 PostgreSQL 17/pgvector、Redis、2 API、2 worker、单 scheduler/relay；一次性 migrate 后由 bootstrap 写 seed/default model/template/MCP，所有长驻进程不拥有发布迁移。部署命令显式 `--scale backend=2 --scale worker=2`，避免 Compose 实现差异改变拓扑。Docker runtime 对齐 Python 3.12 并包含 PostgreSQL 17 client。灾备命令生成 `pg_dump` custom archive + SHA-256 manifest，恢复只接受不同且空的目标库和独立空数据目录；restore drill 比对迁移 head、全部应用表、checkpoints/waiting approvals、events、document chunks/pgvector probe，CI 再启动指向恢复库的 API 调 `/readyz`。

最新宿主机证据：隔离 PostgreSQL 16.14 + pgvector 0.8.3 的真实 marker 5/5；2 API + 2 worker + scheduler + relay 拓扑完成跨 API 查询/SSE、双 worker 排他领取、worker kill 后 generation 2 / attempt 2 接管，以及全运行时重启后的审批续跑。Redis-compatible Garnet 证明共享协作、Lua 协调、中断时快照/变更 HTTP 503、无重启重连及 PostgreSQL SSE fallback；但 Garnet 不支持 `XADD`/`XREAD`，不能计作 Redis Streams live relay 或重复投递验收。quiesced 备份已恢复到不同空数据库/数据目录，restore drill 比对全部应用与 checkpoint 表、3167 events、2 pgvector chunks，并通过恢复库 readiness、RAG 首位命中和 waiting approval 续跑。最终后端门为 494 passed / 5 skipped，应用行 83.17%、关键分支 82.09%，Ruff、format、mypy（309 文件）通过；默认 suite 的 5 个 skip 已由独立真实 marker 5/5 补充。CI 已加入 multi-owner/stale fence、2+2、worker/Redis 停启、全运行时审批恢复和隔离 restore drill；Phase 8 只对真实 Redis Streams 重复投递/live relay 与 Docker 容器故障编排保持开放。

## 12. 风险规避

1. SDK 演进快 → uv.lock 锁死;engine 只依赖 StateGraph/Command/interrupt 稳定面;流式 token 自管
2. anyio 跨 task 崩溃 → owner-task 模式 + test_mcp_manager.py 覆盖
3. SSE 被代理缓冲 → nginx proxy_buffering off + 心跳
4. 单 worker 限制 → EventBus 接口预留 Redis pub/sub,文档写明 scale-out 路径

## 13. 验证方式

- 每阶段按验收标准手动验证(`python -m uv run uvicorn app.main:app --reload` + `pnpm dev`)
- 后端单测:pytest(FakeChatProvider 脚本化流/内存 DB/内存 EventBus),重点覆盖 compiler、conditions、templates、mcp manager
- 质量门:GitHub Actions 固定运行 pytest+覆盖率阈值+Ruff+mypy(app+tests)、后端 pip-audit、前端两套 tsc+build + pnpm audit(high)、14 条 Chromium Playwright 路径，以及生产 Compose 构建/Trivy/smoke/备份
- 健康探针:`/livez` 存活、`/readyz` 就绪(DB 往返/Alembic 头/checkpointer/配置安全,失败 503);compose healthcheck 用 `/readyz`
- P6 终验:生产 Compose 在 Linux runner 上完成迁移、readyz、跨 P3-P5 smoke、备份与恢复演练，并通过 U4 性能预算

## 14. 简历亮点 vs 基础设施

| 简历亮点(值得深挖) | 基础设施(够用即可) |
|---|---|
| DSL→LangGraph 动态编译器(注册器+闭包工厂、并行 reducer、双层循环保护) | CRUD 路由 |
| supervisor 动态路由(Command)+ human-in-the-loop(interrupt/resume) | 文件上传、列表分页 |
| MCP 三传输统一连接管理(owner-task、懒连接+空闲回收、schema 缓存) | Toast/Modal 组件 |
| SSE 精确一次事件重放(seq+Last-Event-ID+先订阅后补历史) | Alembic 迁移 |
| 多 Provider 统一流式+工具调用归一化 | nginx/compose 配置 |
| embedding 批处理+内容寻址缓存;无 Redis 优雅降级 | 路由/布局壳 |
| React Flow 大图优化(流式文本与图状态解耦、rAF 批量 flush、视口裁剪) | |
