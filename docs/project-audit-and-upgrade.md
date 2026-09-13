# AgentCanvas 项目审计与升级计划

> 审计基线：2026-07-29，代码与 [`plan.md`](./plan.md)、[`progress.md`](./progress.md) 同步检查。

> 实施更新：P3.1 稳定化已于 2026-07-29 完成。本文保留原始审计依据；下文标记为“已关闭”的缺口已有迁移、测试或浏览器验收证据，当前研发入口已切换到 P4 RAG。

## 1. 结论

AgentCanvas 已完成 P0–P3，核心链路从“画布编排”推进到“真实 MCP 工具执行”：DSL 可编译为 LangGraph，执行事件可实时推送和历史重放，条件/并行/supervisor 可运行，MCP 服务可发现、复用、管理并绑定到 tool/Agent 节点。

当前项目适合作为本地开发演示和后续研发基座，但还不适合直接暴露到公网或作为多用户生产服务。下一步不建议立刻只堆 RAG 功能，应先完成一个短周期 **P3.1 稳定化门槛**，再进入 P4。

## 2. 当前能力与证据

| 范围 | 当前结果 | 验证证据 |
|---|---|---|
| 编排引擎 | 线性、条件、并行 join、supervisor、循环保护、checkpointer | compiler/condition 测试与既有 E2E |
| 实时执行 | SSE seq、断线续传、终态前强制排空持久化批次 | Calculator 工作流重放 10 条完整事件 |
| MCP | 三传输配置、owner-task 请求队列、懒连接、重试、空闲回收、schema 缓存 | Calculator 发现 `eval_expr`/`convert_units`，执行结果为数值 `10` |
| Agent 工具 | OpenAI function schema 映射、工具循环上限、tool_call/tool_result 轨迹 | Fake Provider + Fake MCP 引擎测试 |
| 前端工作台 | MCP CRUD/探测、tool/Agent 结构化绑定、工具轨迹、响应式弹层 | Playwright 1440×900 与 390×844，无页面/控制台错误 |
| 质量门 | 后端测试、lint、类型检查；前端类型检查与构建 | 29 tests、Ruff、mypy、tsc、Vite build 全部通过 |

## 3. 首页/工作台检查

当前首屏直接进入可操作画布，符合编排工具的任务导向，不需要增加营销式落地页。画布、节点库、Inspector、执行台的主次关系清楚，MCP 入口也已进入顶栏常用操作区。

审计发现与当前实施状态：

| 优先级 | 不足 | 当前状态 | 后续 |
|---|---|---|---|
| 高 | 没有工作流列表、打开、切换与最近记录入口 | **已关闭**：目录、URL 深链与刷新恢复已验收 | P4/P5 资源页复用导航模式 |
| 高 | 多数节点仍以原始 JSON 配置 | **已关闭**：通用 SchemaForm 已消费后端 schema，原始 JSON 降为高级入口 | 为 P4/P5 新节点补领域控件 |
| 高 | 运行输入仍是单行 JSON | **已关闭**：RunDialog 与后端结构化 422 双重校验 | P5 Chat 复用输入 schema |
| 中 | 缺少自动保存、离开提醒与冲突恢复 | **已关闭**：800ms 自动保存、离开保护、409 两种恢复路径已验收 | 增加版本差异可视化属增强项 |
| 中 | 移动画布编辑偏桌面化 | **部分改善**：无横向溢出、viewer 只读、首载最小缩放可读 | P6 增加画布/节点/执行三视图 |
| 中 | 弹层焦点圈定与焦点归还不足 | **待处理** | P4 组件化 Dialog 时统一补齐 |

## 4. 全项目主要不足

### 上线阻断项

1. **已关闭 - 控制面鉴权**：Token cookie/Bearer、viewer/editor/admin RBAC 与 stdio 命令/脚本/package allowlist 已落地；生产仍建议在反向代理层补速率限制与审计汇聚。
2. **已关闭 - MCP 密钥明文**：env/header 已迁移到 Fernet 密文，旧列清空，API 只回传 `********` 并支持掩码保留更新。
3. **已关闭 - SecretBox 明文降级**：本地生成稳定 Fernet key，生产缺失/无效 key 启动失败，旧 `plain:` 模型密钥会在启动时迁移。
4. **已关闭 - 正式迁移**：Alembic 基线与 `0002_mcp_secrets` 已覆盖空库、旧库接管和真实库升级。

### 可靠性与架构缺口

1. **已关闭**：启动时将遗留 `running` execution 标记失败并补终态事件；`waiting_approval` 为 P5 保留恢复语义。
2. **已关闭**：start/variables 已用于必填、未知字段和类型校验，非法请求不创建 execution。
3. Provider 缓存目前主要围绕 default model；任意 `model_config_id` 的按需加载、多 Provider 管理页与故障切换仍未完成。
4. Agent 工具循环使用非流式 `chat()` 汇总，最终答案要等整轮完成后一次出现；普通 Agent 才是逐 token 输出。
5. EventBus、执行 Task 与 MCP 连接均为进程内状态，只支持单 worker；扩展到多 worker 需队列/租约与 Redis pub/sub。
6. demo Calculator 已避免 `eval`，但仍应增加表达式长度、AST 节点数和指数上限；Web Search 也需出站网络策略。

### 工程与测试缺口

1. **已关闭**：GitHub Actions 固定运行后端与前端全量质量门。
2. 真实 MCP 自动化只覆盖 stdio；SSE 与 streamable HTTP 缺少契约测试、断线和超时测试。
3. **已关闭**：仓库内 Playwright 覆盖角色深链、自动保存/运行/SSE 终态和真实 Calculator MCP 发现。
4. 未统计覆盖率，核心 compiler/events/mcphub 建议先达到 80% 分支覆盖。
5. 存在 LangGraph serializer 待弃用警告，应显式设置 `allowed_objects` 并安排依赖升级验证。
6. `architecture.md`、`dsl-spec.md` 仍只是进度文档中的占位索引，ADR 与正式 DSL 规范尚未落地。

## 5. 推荐升级路线

### P3.1 稳定化门槛

目标：在开始 RAG 前，把当前可演示版本提升为安全、可恢复、可持续回归的本地产品。

- 安全：认证/RBAC、MCP secret 加密与掩码、stdio 命令策略、生产模式强制有效 `SECRET_KEY`。
- 数据：引入 Alembic 基线与迁移测试；启动时处理遗留 `running` execution。
- 执行：输入 schema 校验、全局超时（已完成）、优雅关闭、Provider 按 ID 加载。
- 产品：工作流列表/打开/路由、SchemaForm、RunDialog、自动保存和冲突恢复。
- 质量：GitHub Actions 或同类 CI，固定运行 pytest/Ruff/mypy/tsc/build，并落地 Playwright 主路径。

验收标准：非授权用户不能创建 stdio server；API 不返回密钥明文；迁移可从空库和上一版本升级；刷新页面可重新打开工作流；CI 全绿。

### P4 RAG

1. 建立 `knowledge_bases`、`documents`、`embedding_cache` 模型、迁移、Repository 与 API。
2. 实现安全上传、PDF/Markdown loader、chunker、批量 embedding、sha256 内容寻址缓存与 ingest 状态机。
3. 实现 Chroma store 和 `rag` 节点，输出结构化 chunks、score、document/page 元数据。
4. 在 Agent 上下文中注入检索结果，并把来源作为可点击 citation 显示，而不是只拼接匿名文本。
5. 建立固定小语料检索评测，覆盖重复上传缓存、空文档、损坏 PDF、embedding 超时与删除一致性。

验收标准：PDF/Markdown 上传后到达 ready；重复内容命中缓存；rag 节点返回可追溯来源；前端能查看 ingest 进度和失败原因。

### P5 与 P6

- P5：MemoryStore Redis→SQLite 降级、human interrupt/resume、Chat、Anthropic/Ollama、模型配置管理。
- P6：多 worker 事件总线、性能基准、Dockerfile/Compose/nginx、备份恢复、可观测性与发布文档。

## 6. 下一实施批次

建议下一批只做 P3.1 的四个纵向切片：

1. Alembic 基线 + execution 重启清理。
2. 工作流列表/打开 + URL 路由 + 自动保存。
3. SchemaForm + RunDialog + 后端输入校验。
4. MCP 安全边界 + CI/持久 Playwright 主路径。

完成这四项后再进入 P4，RAG 的数据模型、后台任务和 UI 才会建立在可迁移、可恢复、可测试的底座上。
