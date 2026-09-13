# AgentCanvas 未来开发路线图

> 本文档为 AgentCanvas 项目规划未来的开发方向、优化方向和潜在可开发功能
> 
> 基准日期：2026-09-12
> 当前版本：1.0.0 (候选发布)
> 文档状态：规划草案

---

## 执行摘要

AgentCanvas 已完成 C0–C8 全部八个商业化阶段，具备生产级分布式执行、多租户身份、可观测性、安全加固与完整质量门禁。当前产品版本为 **1.0.0 候选发布**，待正式发布后启动为期六个月的支持窗口。

本路线图基于现有技术债务、用户反馈、行业趋势与技术演进，为未来 6-18 个月规划三大主线：

1. **C9 桌面端原生化**（已进入实施）：Tauri 2 壳 + 嵌入式 Python + 真 PTY 终端
2. **平台能力增强**：工作流市场、高级编排、企业集成与生态扩展
3. **技术债务清理与性能优化**：依赖现代化、架构重构与规模化准备

---

## 目录

- [1. 当前项目状态盘点](#1-当前项目状态盘点)
- [2. C9 桌面端原生化（进行中）](#2-c9-桌面端原生化进行中)
- [3. 平台能力增强路线](#3-平台能力增强路线)
- [4. 技术债务与优化](#4-技术债务与优化)
- [5. 生态与集成扩展](#5-生态与集成扩展)
- [6. 性能与规模化](#6-性能与规模化)
- [7. 安全与合规强化](#7-安全与合规强化)
- [8. 开发者体验提升](#8-开发者体验提升)
- [9. 商业化与运营](#9-商业化与运营)
- [10. 风险评估与优先级](#10-风险评估与优先级)

---

## 1. 当前项目状态盘点

### 1.1 已完成的核心能力

**架构与基础设施**
- ✅ 分布式执行：API/worker/scheduler/relay 四角色水平扩展
- ✅ PostgreSQL 17 + pgvector 耐久存储，Redis Streams 事件中继
- ✅ LangGraph 1.x + 严格序列化器，跨重启 checkpoint 恢复
- ✅ SSE 精确一次事件重放（seq + Last-Event-ID + PostgreSQL fallback）
- ✅ 可观测性：OpenTelemetry traces/metrics + Prometheus + Grafana
- ✅ 备份恢复：checksummed PostgreSQL dump + 应用数据归档

**身份与多租户**
- ✅ 本地用户 + OIDC 集成，刷新令牌族，会话撤销
- ✅ 组织/项目/RBAC（viewer/editor/admin），服务账号 + API Token
- ✅ 工作流协作：presence + 软锁 + 版本评论/审阅 + 三方合并
- ✅ 审计日志：append-only，秘密脱敏，时间/租户/actor/action 索引
- ✅ 五类项目配额：并发执行/文档存储/embedding字节/模型费用/MCP进程

**编排与节点生态**
- ✅ 12 种内置节点：start/end/agent/tool/rag/condition/switch/iteration/human/code/http/subworkflow
- ✅ Agent 模式：simple/react（工具循环）/supervisor（动态路由）
- ✅ 版本化插件 SDK：manifest + schema + 沙箱隔离子进程
- ✅ DSL 1.0 契约：不可变版本，导入导出，0.9→1.0 迁移器

**MCP 与 Provider 生态**
- ✅ MCP SDK 2.x，stdio/SSE/streamable-HTTP 三传输，owner-task 连接管理
- ✅ MCP catalog：approved 版本，rollout 预检，权限 diff，安全审计
- ✅ Provider 能力矩阵：streaming/function_calling/vision/json_mode 四维约束
- ✅ 韧性治理：熔断/重试预算/fallback chain，健康快照，故障注入

**RAG 与知识管理**
- ✅ 混合检索：BM25 + dense vector + reranker（MMR/cross-encoder）
- ✅ 分块策略：固定/语义/递归/Markdown 四种，动态选择
- ✅ 在线数据源：URL/RSS/API，定时重同步，变化分块重嵌
- ✅ pgvector + SQL 双后端，嵌入式 Chroma（桌面）与 pgvector（生产）

**调试与评测**
- ✅ 节点检查器：输入/输出/耗时/token/费用，脱敏错误
- ✅ 失败节点重跑：精确版本子执行，上游快照，副作用阻断
- ✅ 评测数据集：不可变版本，A/B 对比，RAG 固定语料指标
- ✅ 调试模式：breakpoint 节点，single-step，状态补丁覆写
- ✅ 单节点干跑：ephemeral 执行，mock 输入，实时输出预览

**触发与集成**
- ✅ Webhook 触发器：HMAC 签名，IP 白名单，输入 schema 校验
- ✅ Cron 计划：IANA 时区，misfire 策略，失败策略（skip/retry/alert）
- ✅ 工作流 API：服务账号 + 哈希 token，版本绑定，幂等执行
- ✅ 应用发布：独立运行时，嵌入分发，会话变量，用量视图

**UI/UX 与可访问性**
- ✅ 信息架构：全局顶栏 + 左侧导航，设置页聚合，390px 移动抽屉
- ✅ 命令面板：Ctrl+K，跨页搜索（工作流/应用/知识库/文档），键盘导航
- ✅ Undo/Redo：zundo 时间旅行，50 步历史，跨会话持久化
- ✅ 自动布局：Dagre 分层布局，对齐辅助线，分组与注释
- ✅ 节点 UX：拖拽优化（dragPaintP95 < 70ms），条件虚拟化，流式文本解耦
- ✅ 主题系统：void/ink 双主题，CSS 变量，cubic-bezier 动画
- ✅ i18n：中英双语，语言切换，日期/数字格式化
- ✅ 新手引导：交互式 tour，localStorage 持久化，跳过/完成状态
- ✅ 可访问性：ARIA landmarks，axe serious 0 违规，键盘导航，对比度合规

**成本治理与运营**
- ✅ 模型计费：版本化价格，token 统计，Human pause 快照持久化
- ✅ 预算机制：per-execution 调用数/token/费用上限，超限中止
- ✅ 成本告警：阈值告警，确认状态，durable 持久化
- ✅ 用量计量：execution/document/embedding/cost/mcp 五维统计，月度归档
- ✅ 计划与套餐：抽象层，quota 动态调整，超额策略

**安全与合规**
- ✅ C8-1 OS 沙箱：nsjail/bubblewrap，code 节点与插件隔离
- ✅ C8-2 公开面防护：独立限流桶，prompt injection 数据围栏
- ✅ C8-3 纵深防护：CSP/HSTS/X-Frame-Options，STRIDE 威胁建模
- ✅ Secret Provider：env:// / docker:// / external:// 引用，掩码更新保留原引用
- ✅ HTTP 节点 SSRF 防护：DNS rebinding，redirect 跟随限制，allow_private_network opt-in

**质量门禁**
- ✅ 后端：494 passed / 5 PostgreSQL skipped，应用行 83.17%，关键分支 82.09%
- ✅ 前端：source/E2E typecheck，production build，bundle budget（初始 97.91 KiB < 120 KiB）
- ✅ 浏览器：Playwright 71/71，画布性能 drag p95 59.5–67.5ms，React commit p95 < 10ms
- ✅ 契约：API 1.0.0 / DSL 1.0 / event 1.0 baseline，生成物漂移检查，破坏性变更阻断
- ✅ 依赖审计：pip-audit + pnpm audit moderate，已知风险豁免机制

### 1.2 当前技术栈

| 层级 | 技术选型 | 版本 |
|------|---------|------|
| **前端** | React | 19.2.8 |
| | TypeScript | 5.6.3 |
| | Vite | 8.2.0 |
| | React Router | 7.18.2 |
| | @xyflow/react | 12.3.5 |
| | Tailwind CSS | 4.3.3 |
| | Zustand + zundo | 5.0.1 + 2.3.0 |
| **后端** | Python | 3.12+ (实跑 3.13) |
| | FastAPI | ≥0.115 |
| | LangGraph | 1.0.10–1.x |
| | MCP SDK | 2.0–2.x |
| | SQLAlchemy | 2.0 |
| | Alembic | ≥1.13 |
| **存储** | PostgreSQL | 17 |
| | pgvector | 0.5 |
| | Redis | 5.0 |
| | Chroma | ≥0.5 (桌面) |
| **Provider** | OpenAI 兼容 | — |
| | Anthropic | Messages API |
| | Google Gemini | — |
| | Ollama | — |
| **部署** | Docker Compose | — |
| | nginx | unprivileged |
| | GitHub Actions | 5 jobs |

### 1.3 当前瓶颈与限制

**架构层面**
1. **单机 SQLite 仍是开发默认**：虽有 PostgreSQL 支持，但本地开发仍依赖 SQLite，跨后端特性差异需要额外测试覆盖
2. **协作状态单进程**：Redis-backed collaboration 在 I1 前仍需单 API 进程，多副本需等 I1 完全关闭
3. **MCP 连接归 worker**：控制面 test/discover 后立即释放，live session 不在 worker 间转移，长连接场景受限
4. **向量后端二选一**：Chroma（嵌入式）或 pgvector（生产），无统一抽象层支持混合或切换

**性能层面**
1. **画布规模上限 500 节点**：虽 C6-2 已优化到 drag p95 < 70ms，但超 500 节点后虚拟化与 memoization 边际收益递减
2. **SQLite 写入瓶颈**：c50 并发创建 p95 仍在 1.8s，单实例写入增长为 I1/PostgreSQL 触发信号
3. **SSE 多路复用未完全优化**：C6-3 已实现 multiplex，但弱网重连与执行列表活跃轮询仍有优化空间
4. **事件表无分区**：execution_events 表随时间增长，C6-1 已实现 retention job 与冷热分离，但未做真正分区表

**功能层面**
1. **工作流市场缺失**：有模板库，但无社区分享、评分、版本追踪与依赖管理
2. **高级编排能力受限**：无 parallel gateway、BPMN 补偿事务、长事务 saga 模式
3. **跨工作流协调缺失**：subworkflow 只能嵌入，无 parent-child 协调、信号传递或分布式锁
4. **实时协作受限**：有 presence 与软锁，但无 CRDT、OT 或协同编辑光标

**生态层面**
1. **Provider 仅四家**：OpenAI 兼容 / Anthropic / Gemini / Ollama，无 Cohere / Mistral / Together / Bedrock
2. **MCP 仅三个 demo**：calculator / filesystem / websearch，无官方 Git / Slack / Jira / Notion 等
3. **插件生态空白**：SDK 已就绪，但无官方插件库、发现机制或社区贡献流程
4. **企业集成缺失**：无 SAML、SCIM 2.0 provisioning、Okta/Azure AD 深度集成

**开发体验层面**
1. **CLI 工具缺失**：无 `agentcanvas-cli` 用于初始化、部署、迁移、备份
2. **SDK/客户端库单薄**：只有 Python 后端，无 JavaScript/TypeScript/Go/Java SDK
3. **本地开发体验粗糙**：需手动起两个服务，无 dev container 或一键启动脚本
4. **文档需完善**：API 参考、最佳实践、troubleshooting、迁移指南待系统化

---

## 2. C9 桌面端原生化（进行中）

### 2.1 当前状态

**阶段**：C9-1 实施中（2026-09-12 起）  
**前置**：C0–C8 全部完成  
**文档**：`docs/c9-desktop-tauri-plan.md`（完整实施计划）  
**Spike 结论**：方案 B'（直连 sidecar，前端单点前缀）已定案

### 2.2 目标与范围

**核心目标**
1. **摆脱浏览器**：双击启动即得完整应用，无需浏览器/Python/Node/Docker
2. **内置终端**：真 PTY（portable-pty），平台运维预设（起停/迁移/测试/日志）
3. **性能不退且有增**：守住 C6 全部指标，利用原生能力（字体本地化/GPU 终端）
4. **UI 分区重构**：门面吸收动效工艺，工作面守克制工业语言

**技术方案**
- Tauri 2.11 壳 + WebView2（Windows）
- 嵌入 CPython 3.13 onedir（200–300MB，排除 chromadb）
- `VECTOR_BACKEND=sql`，pgvector table 替代 Chroma
- 前端直连 `http://127.0.0.1:<动态端口>`，后端 CORS 放行 `tauri://localhost`
- xterm.js + portable-pty，平台任务预设（PowerShell/bash）

**实施切片**
1. **C9-1 Tauri 骨架**：空壳 + sidecar 生命周期 + 健康探测
2. **C9-2 Python 嵌入**：onedir 打包 + 依赖裁剪 + 启动验证
3. **C9-3 前后端对接**：origin 注入 + REST/SSE 直连 + CORS 配置
4. **C9-4 内置终端**：PTY 管理 + xterm.js + 平台预设
5. **C9-5 UI 重构**：门面动效 + 材质层次 + MASTER.md 增补
6. **C9-6 打包与分发**：NSIS 安装器 + 签名 + 更新预留

### 2.3 风险与依赖

**高风险**
- Python 嵌入运行时稳定性：onedir 依赖路径、模块发现、动态库加载
- PTY 安全模型：C8 纳入沙箱，默认仅本地会话可用
- 安装包体积：200–300MB 压缩后仍需网络分发与存储成本

**中风险**
- 字体自托管：Noto Sans SC + JetBrains Mono 许可证合规
- OIDC 桌面流：系统浏览器 redirect + 本地回调端口或 deep link
- 更新机制：预留但未实现完整分发链路

**依赖**
- Rust 工具链（已就绪：rustc 1.98.0）
- Windows WebView2（已就绪：Win11 预装）
- CI Windows runner（待增加：桌面产物构建需要 Windows/macOS runner）

### 2.4 后续演进

**短期（C9 完成后）**
- macOS 版本：签名 + 公证 + Apple Silicon 原生
- Linux 版本：AppImage / Flatpak / Snap 三选一
- 自动更新：Tauri updater + GitHub Releases 分发链路

**中期（3-6 个月）**
- 离线模式：本地 LLM（Ollama 集成） + 嵌入式 Chroma 完全离线
- 性能监控：桌面专属指标（启动时间/内存占用/GPU 使用）
- 插件市场桌面端：本地插件发现与安装

---

## 3. 平台能力增强路线

### 3.1 工作流市场与模板生态

**现状**：有模板库（官方 + 用户模板），但无社区分享与评分机制

**P1 - 工作流市场基础**
- **发布到市场**：工作流发布除版本外增加"公开到市场"选项
- **市场发现页**：分类浏览、标签筛选、热度排序、搜索
- **详情与预览**：README、截图、输入输出示例、依赖声明
- **评分与评论**：五星评分、文本评论、有用性投票
- **安装使用统计**：克隆次数、运行次数、成功率（匿名聚合）

**P2 - 依赖与版本管理**
- **依赖声明**：工作流声明所需 Provider/MCP/插件/知识库
- **兼容性矩阵**：检查用户环境是否满足依赖，缺失项提示安装
- **版本追踪**：市场工作流新版本通知，升级向导
- **Fork 与贡献**：Fork 到私有项目，改进后提交 PR 到原作者

**P3 - 收益与激励**
- **付费工作流**：作者设定价格，平台抽成，Stripe 结算
- **订阅模式**：按月付费使用高级工作流
- **赞助机制**：打赏作者，GitHub Sponsors 集成

**技术实现**
- 新增 `workflow_marketplace` 表：公开版本、README、标签、统计
- `marketplace_reviews` 表：评分、评论、有用性投票
- `marketplace_dependencies` 表：依赖声明与兼容性检查
- 前端新增 `/marketplace` 路由与市场浏览 UI

**优先级**：P2（中优先级），依赖工作流市场策略决策

---

### 3.2 高级编排能力

**现状**：支持基础编排（顺序/分支/并行/循环），缺 BPMN 高级模式

**P1 - Parallel Gateway（并行网关）**
- **AND-split**：一个节点触发多个并行分支（已有）
- **AND-join**：等待所有分支完成后继续（已有 join merge）
- **OR-split**：根据多个条件触发部分分支
- **OR-join**：等待任意一个或多个分支完成

**P2 - 补偿事务与 Saga**
- **补偿节点**：定义回滚逻辑，失败时自动触发补偿链
- **Saga 模式**：长事务协调，forward recovery / backward recovery
- **事务边界**：声明哪些节点构成事务单元
- **幂等性保证**：补偿节点重复执行幂等

**P3 - 跨工作流协调**
- **Signal 节点**：发送信号到其他执行或工作流
- **Wait for Signal 节点**：等待外部信号才继续
- **Parent-Child 协调**：父工作流启动子工作流并等待结果
- **分布式锁节点**：跨执行互斥，防止并发冲突

**P4 - 动态子图**
- **动态 subworkflow**：运行时决定嵌入哪个工作流（当前只能编译期）
- **条件子图**：根据条件选择不同子图执行
- **循环子图优化**：避免每次循环重新编译子图

**技术实现**
- 扩展 DSL：新增 `parallel_gateway` / `compensation` / `signal` 节点类型
- 编译器增强：识别补偿边界，生成 saga coordinator
- 跨执行通信：Redis pub/sub 或 PostgreSQL LISTEN/NOTIFY 实现信号
- Saga 状态机：`saga_state` 表持久化补偿栈

**优先级**：P2（中优先级），依赖用户复杂编排需求反馈

---

### 3.3 实时协作增强

**现状**：有 presence + 软锁，但无协同编辑

**P1 - CRDT 协同编辑**
- **Yjs / Automerge 集成**：节点位置、配置同步
- **协同光标**：显示其他用户光标位置与选中节点
- **操作历史同步**：Undo/Redo 跨用户可见
- **冲突自动合并**：位置重叠自动调整，配置冲突提示

**P2 - 实时聊天与评论**
- **画布内评论**：在节点上添加评论气泡
- **实时聊天面板**：团队成员即时沟通
- **@提及通知**：@用户名触发通知

**P3 - 版本协商与冲突解决**
- **版本分支可视化**：显示当前版本的分支树
- **智能合并建议**：AI 辅助冲突解决
- **协作会话录制**：回放编辑历史

**技术实现**
- Yjs 或 Automerge CRDT 库集成
- WebSocket 替代 SSE 用于双向实时通信
- `collaboration_comments` 表：评论持久化
- `collaboration_chat` 表：聊天记录

**优先级**：P3（低优先级），当前软锁机制已满足基本协作需求

---

## 4. 技术债务与优化

### 4.1 依赖现代化

**现状瓶颈**
- Python 3.12+ 实跑 3.13，已相对现代
- LangGraph 1.x 稳定，但 2.x 可能带来破坏性变更
- React 19.2.8 最新，但部分生态库仍不兼容

**优化方向**

**P1 - 后端依赖审计**
- **LangGraph 2.x 迁移准备**：监控 2.x 发布，评估迁移成本
- **MCP SDK 3.x 跟进**：关注 MCP 协议演进
- **SQLAlchemy 2.0 优化**：利用新 API 提升性能
- **异步库统一**：全面采用 asyncio，移除同步阻塞调用

**P2 - 前端依赖优化**
- **React Router 7.x 深度整合**：利用 Server Functions（如适用）
- **Vite 8.x 优化**：利用新的 bundle splitting 策略
- **@xyflow/react 更新**：跟进上游性能改进
- **Zustand 状态管理优化**：减少不必要的订阅

**P3 - 构建工具链升级**
- **esbuild / SWC 替代 Babel**：更快的 TS 编译
- **Turbopack 评估**：考虑替代 Vite（当其稳定后）
- **Biome 替代 ESLint + Prettier**：统一工具链

**技术实现**
- 每季度依赖审计：`pip-audit` + `pnpm audit`
- 破坏性变更迁移指南：文档化升级路径
- 渐进式迁移：功能标志控制新旧实现

**优先级**：P1（高优先级），保持依赖健康避免技术债累积

---

### 4.2 架构重构

**现状瓶颈**
- 单机 SQLite 仍是开发默认，跨后端差异需额外测试
- 协作状态单进程，多副本需等 I1 完全关闭
- MCP 连接归 worker，长连接场景受限
- 向量后端二选一，无统一抽象层

**优化方向**

**P1 - 存储抽象层统一**
- **Repository 模式**：统一 SQLite / PostgreSQL 访问接口
- **向量存储抽象**：VectorStore 接口，Chroma / pgvector 实现
- **迁移工具增强**：一键 SQLite → PostgreSQL 迁移
- **测试覆盖**：双后端集成测试矩阵

**P2 - 分布式协作完整化**
- **I1 Phase 9 完成**：Redis Streams 持久化协作状态
- **多 API 副本支持**：无状态化所有协作端点
- **会话亲和性可选**：sticky session 或完全无状态

**P3 - MCP 连接池优化**
- **连接转移机制**：live session 在 worker 间迁移
- **连接预热**：常用 MCP 预启动
- **健康检查增强**：主动探测 + 熔断

**P4 - 事件架构优化**
- **事件表分区**：按月/季度分区 execution_events
- **冷热分离完善**：归档表 + 查询路由
- **事件压缩**：历史事件 gzip 压缩存储

**技术实现**
- 抽象层接口定义：`backend/src/repository/`
- 迁移脚本：`backend/scripts/migrate_sqlite_to_pg.py`
- 分区 DDL：Alembic 迁移 + pg_partman
- 连接池管理器：`backend/src/mcp/connection_manager.py`

**优先级**：P1（高优先级），存储抽象层是长期维护的基础

---

### 4.3 代码质量提升

**现状瓶颈**
- 后端覆盖率 83.17%，仍有盲点
- 前端 E2E 覆盖不全面，边缘场景未测
- 部分模块耦合度高，难以单独测试

**优化方向**

**P1 - 测试覆盖补全**
- **后端覆盖目标 90%**：补充边缘场景、错误路径
- **前端单元测试**：关键组件 Vitest 测试
- **E2E 场景扩展**：协作、触发器、评测全流程
- **性能回归测试**：C6 指标持续监控

**P2 - 代码质量工具强化**
- **静态分析**：mypy strict 模式，pyright 补充
- **复杂度监控**：radon / sonarqube，圈复杂度阈值
- **依赖注入**：减少全局状态，提升可测试性
- **代码审查自动化**：PR 门禁 + 自动建议

**P3 - 文档自动化**
- **API 文档**：OpenAPI 自动生成 + Redoc 渲染
- **架构图**：PlantUML / Mermaid 代码化
- **变更日志**：conventional commits + 自动生成

**技术实现**
- CI 门禁增强：覆盖率阈值 + 趋势监控
- pre-commit hooks：格式化 + 类型检查 + 单测
- 文档生成 CI job：自动更新 docs/

**优先级**：P2（中优先级），质量提升是持续过程

---

## 5. 生态与集成扩展

### 5.1 Provider 生态扩展

**现状**：仅支持 OpenAI 兼容 / Anthropic / Gemini / Ollama

**P1 - 主流 Provider 补全**
- **Cohere**：Command R+ 支持，embedding 模型
- **Mistral AI**：Mixtral / Mistral Large API
- **Together AI**：开源模型托管
- **AWS Bedrock**：企业多模型接入
- **Azure OpenAI**：企业 OpenAI 托管

**P2 - 能力矩阵扩展**
- **多模态**：图像输入（GPT-4V / Gemini）、音频（Whisper）
- **长上下文**：100K+ token 模型支持
- **JSON Schema 强制**：structured output 模式
- **批处理 API**：降低成本的异步批处理

**P3 - 成本优化**
- **Provider 降级链**：primary → fallback → local
- **智能路由**：根据任务复杂度选择模型
- **缓存策略**：prompt 缓存（Anthropic/Gemini）
- **批量优化**：自动合并请求降低成本

**技术实现**
- Provider 插件化：`backend/src/providers/{provider_name}/`
- 能力注册表：`provider_capabilities` 表扩展
- 路由引擎：`backend/src/providers/router.py`
- 成本估算器：`backend/src/cost/estimator.py`

**优先级**：P1（高优先级），Provider 多样性是核心竞争力

---

### 5.2 MCP 服务器生态

**现状**：仅 3 个 demo（calculator / filesystem / websearch）

**P1 - 官方 MCP 扩展**
- **开发工具**：Git、GitHub、GitLab、Jira、Linear
- **通讯协作**：Slack、Discord、Microsoft Teams、Email
- **知识管理**：Notion、Confluence、Google Docs
- **数据库**：PostgreSQL、MySQL、MongoDB、Redis

**P2 - 企业集成 MCP**
- **CRM**：Salesforce、HubSpot
- **ERP**：SAP、Oracle
- **监控告警**：PagerDuty、Opsgenie、Datadog
- **CI/CD**：Jenkins、CircleCI、GitHub Actions

**P3 - 社区 MCP 市场**
- **社区贡献流程**：MCP 提交 → 审核 → 发布
- **MCP 市场页面**：发现、安装、评分
- **版本管理**：approved 版本机制（已有），社区版本追踪
- **安全审计**：社区 MCP 沙箱隔离增强

**技术实现**
- MCP SDK 模板：快速开发指南
- MCP catalog 扩展：`backend/src/mcp/catalog/` 新增配置
- 社区市场：`/mcp-marketplace` 路由
- 安全审计流程：自动化 + 人工复审

**优先级**：P1（高优先级），MCP 丰富度直接影响用户价值

---

### 5.3 插件生态建设

**现状**：SDK 已就绪，但无官方插件库、发现机制或社区贡献流程

**P1 - 官方插件库**
- **数据转换**：JSON/XML/CSV 解析与转换
- **加密解密**：常见算法封装
- **图像处理**：Pillow 封装，裁剪/缩放/滤镜
- **自然语言处理**：分词、情感分析、实体识别

**P2 - 插件市场基础**
- **发现页面**：分类、搜索、评分
- **安装机制**：一键安装 + 依赖检查
- **版本管理**：插件更新通知
- **沙箱隔离**：C8 OS 沙箱已实现，确保每个插件独立运行

**P3 - 社区贡献流程**
- **插件开发模板**：scaffold 工具
- **CI/CD 集成**：自动测试 + 打包
- **发布审核**：安全扫描 + 人工复审
- **收益分成**：付费插件平台抽成

**技术实现**
- 插件注册表：`plugin_registry` 表
- 安装器：`backend/src/plugins/installer.py`
- 市场 API：`/api/v1/plugin-marketplace`
- 开发者文档：`docs/plugin-development.md`

**优先级**：P2（中优先级），依赖插件市场策略决策

---

### 5.4 企业集成能力

**现状**：无 SAML、SCIM 2.0 provisioning、Okta/Azure AD 深度集成

**P1 - 企业身份集成**
- **SAML 2.0**：IdP-initiated / SP-initiated SSO
- **SCIM 2.0**：用户/组自动同步
- **Okta 深度集成**：预构建应用
- **Azure AD / Entra ID**：企业目录集成
- **Google Workspace**：OAuth + 目录同步

**P2 - 企业治理功能**
- **审计日志导出**：SIEM 集成（Splunk / ELK）
- **合规报告**：SOC 2 / ISO 27001 审计支持
- **数据驻留**：区域化部署支持
- **备份恢复增强**：企业级 RTO/RPO 保证

**P3 - 企业部署支持**
- **Kubernetes Helm Chart**：生产级 K8s 部署
- **高可用架构**：多 AZ 部署指南
- **灾备方案**：跨区域复制
- **企业支持包**：SLA + 专属支持渠道

**技术实现**
- SAML/SCIM 库集成：`python-saml` / `scim2-sdk`
- 企业配置：`backend/src/enterprise/`
- Helm Chart：`deploy/kubernetes/`
- 文档：`docs/enterprise-deployment.md`

**优先级**：P2（中优先级），取决于企业客户需求

---

## 6. 性能与规模化

### 6.1 画布性能突破

**现状瓶颈**：500 节点上限，drag p95 < 70ms，超过后虚拟化边际收益递减

**优化方向**

**P1 - 虚拟化深度优化**
- **分层虚拟化**：节点/边/标签分层渲染
- **LOD（细节层次）**：缩小时简化节点渲染
- **Offscreen Canvas**：边渲染到离屏画布
- **WebGL 渲染**：自定义 WebGL 渲染器（实验性）

**P2 - 内存优化**
- **节点数据懒加载**：仅加载可视区域节点配置
- **图像资源优化**：节点图标 sprite sheet
- **状态压缩**：Undo/Redo 栈使用 diff 存储
- **大图分片**：超大工作流分区加载

**P3 - 交互优化**
- **预测性渲染**：预判拖拽方向预加载节点
- **增量布局**：局部变化时仅重新布局受影响区域
- **GPU 加速**：CSS transforms 代替 position 变化
- **Web Worker 布局**：Dagre 布局移至 Worker

**技术实现**
- React Flow 自定义渲染器：`frontend/src/features/canvas/renderers/`
- 虚拟化策略调整：`frontend/src/features/canvas/useVirtualization.ts`
- 性能监控：`performance.measure()` + 上报
- 压力测试：自动生成 1000+ 节点图测试

**优先级**：P3（低优先级），当前 500 节点已满足大多数场景

---

### 6.2 后端并发优化

**现状瓶颈**：SQLite c50 创建 p95 1.8s，单实例写入瓶颈

**优化方向**

**P1 - 数据库性能优化**
- **连接池调优**：pgbouncer + 连接池参数优化
- **索引优化**：慢查询分析 + 缺失索引补充
- **查询优化**：N+1 问题消除，JOIN 优化
- **物化视图**：高频聚合查询预计算

**P2 - 缓存策略**
- **Redis 缓存层**：热点数据缓存（工作流定义、Provider 配置）
- **本地缓存**：进程内 LRU 缓存（只读数据）
- **CDN 静态资源**：前端资源 + 用户上传文件
- **缓存预热**：启动时加载常用数据

**P3 - 异步处理优化**
- **批量操作**：批量插入/更新减少往返
- **后台任务队列**：Celery / ARQ 处理非关键路径
- **流式响应**：大数据集流式返回
- **懒加载**：分页 + cursor-based pagination 优化

**技术实现**
- 慢查询监控：`pg_stat_statements` + Grafana
- 缓存层：`backend/src/cache/`
- 批量操作：`backend/src/repository/batch.py`
- 队列系统：`backend/src/tasks/` + Redis/RabbitMQ

**优先级**：P1（高优先级），I1 分布式执行依赖此优化

---

### 6.3 SSE 多路复用优化

**现状**：C6-3 已实现 multiplex，但弱网重连与活跃轮询仍有优化空间

**优化方向**

**P1 - 重连策略优化**
- **指数退避**：重连间隔递增，最大 30s
- **心跳保活**：定期 ping/pong 检测连接
- **断点续传**：Last-Event-ID 精确恢复
- **客户端缓冲**：弱网时本地缓存事件

**P2 - 推送效率优化**
- **事件合并**：高频事件批量发送
- **增量更新**：仅发送变化部分
- **压缩传输**：gzip SSE 流（需浏览器支持）
- **优先级队列**：关键事件优先推送

**P3 - 监控与诊断**
- **连接质量指标**：延迟、丢包率、重连次数
- **事件延迟追踪**：生成时间 → 接收时间
- **慢推送告警**：超过阈值告警
- **客户端诊断面板**：连接状态可视化

**技术实现**
- 重连逻辑：`frontend/src/api/sse.ts` 优化
- 服务端推送：`backend/src/sse/multiplexer.py` 增强
- 监控指标：Prometheus + Grafana
- 诊断面板：`frontend/src/features/debug/SSEDiagnostics.tsx`

**优先级**：P2（中优先级），改善用户体验

---

## 7. 安全与合规强化

### 7.1 安全审计增强

**现状**：C8 已完成基础安全加固，但缺乏持续安全审计

**优化方向**

**P1 - 自动化安全扫描**
- **依赖漏洞扫描**：Snyk / Dependabot 集成
- **代码安全扫描**：Bandit (Python) / ESLint security 插件
- **SAST 工具**：SonarQube / Semgrep
- **容器扫描**：Trivy / Clair 扫描 Docker 镜像

**P2 - 渗透测试**
- **定期渗透测试**：季度外部安全审计
- **漏洞赏金计划**：HackerOne / Bugcrowd
- **红队演练**：模拟攻击测试防御
- **安全响应流程**：CVE 响应 + 紧急补丁发布

**P3 - 合规认证**
- **SOC 2 Type II**：安全合规认证
- **ISO 27001**：信息安全管理体系
- **GDPR 合规**：欧盟数据保护
- **HIPAA 合规**：医疗数据（如适用）

**技术实现**
- CI 安全门禁：`bandit` + `safety` + `trivy`
- 漏洞管理：`backend/security/vulnerabilities.md`
- 合规文档：`docs/compliance/`
- 响应流程：`docs/security-response.md`

**优先级**：P2（中优先级），取决于客户合规需求

---

### 7.2 数据安全增强

**现状**：基础 Secret Provider，但缺乏端到端加密与数据分类

**优化方向**

**P1 - 端到端加密**
- **传输加密**：TLS 1.3 强制，HSTS 预加载
- **静态加密**：数据库字段级加密（敏感配置）
- **密钥管理**：HashiCorp Vault / AWS KMS 集成
- **客户端加密**：浏览器端敏感数据加密后传输

**P2 - 数据分类与治理**
- **数据分类标签**：public / internal / confidential / restricted
- **访问控制**：基于数据分类的 RBAC 扩展
- **数据脱敏**：日志/审计中敏感数据自动脱敏（已有）
- **数据保留策略**：自动删除过期数据

**P3 - 隐私保护**
- **差分隐私**：聚合统计添加噪声
- **匿名化工具**：用户数据导出前匿名化
- **GDPR 权利实现**：数据导出/删除/更正
- **隐私影响评估**：新功能隐私审查流程

**技术实现**
- 加密库：`cryptography` / `libsodium`
- KMS 集成：`backend/src/security/kms.py`
- 数据分类：`data_classification` 字段 + 访问检查
- 隐私工具：`backend/src/privacy/`

**优先级**：P2（中优先级），企业客户关注重点

---

### 7.3 访问控制细化

**现状**：组织/项目/RBAC（viewer/editor/admin），但缺乏细粒度权限

**优化方向**

**P1 - 细粒度权限**
- **资源级权限**：单个工作流/知识库权限控制
- **操作权限**：read / write / execute / delete / share
- **字段级权限**：敏感配置字段访问控制
- **时间限定权限**：临时访问权限自动过期

**P2 - 权限委派**
- **角色继承**：自定义角色继承基础角色
- **权限委派**：用户临时授权给他人
- **审批流程**：敏感操作需审批
- **紧急访问**：break-glass 机制 + 事后审计

**P3 - 权限审计**
- **权限变更日志**：谁何时修改了谁的权限
- **权限审查**：定期权限复审提醒
- **异常检测**：权限提升异常告警
- **最小权限原则**：自动建议权限收缩

**技术实现**
- RBAC 扩展：`backend/src/auth/rbac.py` 增强
- 权限模型：Casbin / OPA 集成评估
- 审计日志扩展：`permission_changes` 表
- 审查工具：`backend/scripts/permission_audit.py`

**优先级**：P2（中优先级），企业安全需求

---

## 8. 开发者体验提升

### 8.1 CLI 工具

**现状**：无 `agentcanvas-cli` 用于初始化、部署、迁移、备份

**优化方向**

**P1 - 核心 CLI 功能**
- **项目初始化**：`agentcanvas init` 生成配置
- **本地开发**：`agentcanvas dev` 一键启动前后端
- **数据库迁移**：`agentcanvas migrate` 运行 Alembic
- **备份恢复**：`agentcanvas backup/restore` 完整备份

**P2 - 部署与运维**
- **部署命令**：`agentcanvas deploy --target docker-compose/k8s`
- **健康检查**：`agentcanvas health` 检查服务状态
- **日志查看**：`agentcanvas logs --service api/worker`
- **配置管理**：`agentcanvas config set/get` 环境变量管理

**P3 - 开发辅助**
- **代码生成**：`agentcanvas generate node/mcp/plugin` 脚手架
- **测试运行**：`agentcanvas test --watch` 测试快捷命令
- **性能分析**：`agentcanvas profile` 性能剖析
- **依赖检查**：`agentcanvas doctor` 环境诊断

**技术实现**
- CLI 框架：Click / Typer (Python) 或 Commander (Node.js)
- 项目结构：`cli/` 目录 + PyPI 发布
- 配置管理：`~/.agentcanvas/config.yaml`
- 文档：`docs/cli-reference.md`

**优先级**：P1（高优先级），显著提升开发体验

---

### 8.2 SDK 与客户端库

**现状**：只有 Python 后端，无 JavaScript/TypeScript/Go/Java SDK

**优化方向**

**P1 - 官方 SDK**
- **TypeScript/JavaScript SDK**：npm 包，浏览器 + Node.js
- **Python SDK**：PyPI 包，简化 API 调用
- **Go SDK**：企业后端集成
- **Java SDK**：企业生态集成

**P2 - SDK 功能**
- **完整 API 覆盖**：工作流 CRUD + 执行 + 触发器
- **类型安全**：完整类型定义 + 自动补全
- **错误处理**：统一错误类型 + 重试逻辑
- **流式响应**：SSE 客户端封装

**P3 - 示例与文档**
- **快速开始**：5 分钟上手示例
- **Cookbook**：常见场景代码示例
- **API 参考**：自动生成文档
- **交互式示例**：在线 playground

**技术实现**
- OpenAPI 生成器：自动生成 SDK 骨架
- SDK 仓库：`sdks/{language}/`
- 发布流程：自动 CI/CD 发布到包管理器
- 文档网站：`docs.agentcanvas.io`

**优先级**：P1（高优先级），降低集成门槛

---

### 8.3 开发环境优化

**现状**：需手动起两个服务，无 dev container 或一键启动脚本

**优化方向**

**P1 - 一键启动**
- **开发脚本**：`scripts/dev.sh` 启动完整环境
- **依赖自动安装**：检测并安装缺失依赖
- **热重载优化**：前后端热重载加速
- **环境检查**：启动前验证环境（Node/Python 版本等）

**P2 - Dev Container**
- **VS Code Dev Container**：`.devcontainer/` 配置
- **GitHub Codespaces**：云端开发环境
- **Docker Compose Dev**：开发专用 compose 配置
- **预配置扩展**：推荐 VS Code 扩展自动安装

**P3 - 开发工具集成**
- **调试配置**：VS Code / PyCharm 调试配置
- **数据库 GUI**：集成 Adminer / pgAdmin
- **API 测试**：内置 Swagger UI / Postman 集合
- **日志聚合**：开发环境日志统一查看

**技术实现**
- 开发脚本：`scripts/dev.sh` + `package.json` scripts
- Dev Container：`.devcontainer/devcontainer.json`
- 调试配置：`.vscode/launch.json`
- 文档：`docs/development.md` 完善

**优先级**：P1（高优先级），降低贡献门槛

---

### 8.4 文档体系完善

**现状**：基础文档存在，但需系统化与完善

**优化方向**

**P1 - 核心文档**
- **快速开始**：5 分钟运行第一个工作流
- **概念指南**：核心概念深入解释
- **API 参考**：完整 REST API 文档
- **最佳实践**：性能优化、安全配置、生产部署

**P2 - 进阶文档**
- **架构设计**：系统架构详细说明
- **扩展开发**：插件/MCP/节点开发指南
- **故障排查**：Troubleshooting 手册
- **迁移指南**：版本升级迁移文档

**P3 - 多媒体内容**
- **视频教程**：YouTube 系列教程
- **交互式教程**：网站内嵌 playground
- **案例研究**：真实用户案例
- **博客文章**：技术深度文章

**技术实现**
- 文档网站：VitePress / Docusaurus
- API 文档：OpenAPI → Redoc/Swagger UI
- 交互式示例：StackBlitz / CodeSandbox 集成
- 搜索：Algolia DocSearch

**优先级**：P1（高优先级），文档是产品门面

---

## 9. 商业化与运营

### 9.1 SaaS 版本

**现状**：开源自部署，无托管 SaaS 版本

**优化方向**

**P1 - 托管服务基础**
- **多租户架构**：租户隔离 + 资源配额
- **订阅计划**：Free / Pro / Team / Enterprise
- **计量计费**：执行次数/时长/token 消耗
- **支付集成**：Stripe / Paddle 订阅管理

**P2 - SaaS 运营功能**
- **用户注册**：邮箱验证 + OAuth 社交登录
- **配额管理**：自动限流 + 升级提示
- **使用统计**：租户 dashboard 用量可视化
- **账单管理**：发票生成 + 支付历史

**P3 - 增值服务**
- **优先支持**：付费用户专属支持渠道
- **高级功能**：企业级功能付费解锁
- **专业服务**：定制开发 + 咨询服务
- **培训认证**：AgentCanvas 认证课程

**技术实现**
- 多租户：tenant_id 分区 + RLS (Row-Level Security)
- 计费系统：`backend/src/billing/`
- 支付集成：Stripe SDK
- 配额执行：中间件 + 装饰器

**优先级**：P2（中优先级），依赖商业化策略决策

---

### 9.2 社区建设

**现状**：开源项目，但社区生态待建设

**优化方向**

**P1 - 社区平台**
- **官方论坛**：Discourse / GitHub Discussions
- **Discord/Slack**：实时社区交流
- **贡献指南**：CONTRIBUTING.md 完善
- **行为准则**：CODE_OF_CONDUCT.md

**P2 - 社区激励**
- **贡献者识别**：README 贡献者墙 + 徽章
- **月度亮点**：博客突出优秀贡献
- **黑客松活动**：定期编程竞赛
- **Ambassador 计划**：社区大使奖励

**P3 - 生态合作**
- **合作伙伴计划**：插件/集成开发商合作
- **教育计划**：大学课程合作
- **开源基金**：资助相关开源项目
- **技术会议**：AgentCanvas Conf

**技术实现**
- 论坛部署：Discourse self-hosted
- Discord bot：社区管理自动化
- 贡献统计：GitHub API + dashboard
- 文档：`docs/community.md`

**优先级**：P2（中优先级），长期社区健康投资

---

### 9.3 市场营销

**现状**：技术驱动，缺乏系统化市场推广

**优化方向**

**P1 - 内容营销**
- **技术博客**：Medium / Dev.to 系列文章
- **案例研究**：客户成功故事
- **开源营销**：Hacker News / Reddit / ProductHunt
- **SEO 优化**：官网 + 文档 SEO

**P2 - 产品展示**
- **Demo 视频**：YouTube 产品演示
- **交互式 Demo**：在线可玩 playground
- **模板库展示**：精选工作流模板
- **对比页面**：vs 竞品对比

**P3 - 增长策略**
- **病毒式增长**：邀请奖励机制
- **API 生态**：开发者倡导计划
- **会议演讲**：技术大会 talk
- **播客采访**：创始人访谈

**技术实现**
- 官网 SEO：Next.js SSG + 元标签优化
- Demo 环境：demo.agentcanvas.io
- 分析工具：Google Analytics + Plausible
- 增长实验：A/B 测试框架

**优先级**：P3（低优先级），产品成熟后考虑

---

## 10. 风险评估与优先级

### 10.1 短期优先级（3 个月）

**P0 - 必须完成**
1. **C9-1 到 C9-3**：Tauri 桌面端核心功能
2. **依赖安全审计**：修复已知漏洞
3. **CLI 工具 MVP**：`init` / `dev` / `migrate` 命令
4. **TypeScript SDK**：npm 包发布

**P1 - 高优先级**
5. **Provider 扩展**：Cohere / Mistral / Bedrock
6. **官方 MCP 补充**：Git / Slack / Notion
7. **存储抽象层**：统一 SQLite/PostgreSQL 接口
8. **后端并发优化**：连接池 + 缓存层
9. **文档完善**：快速开始 + API 参考

**风险**：C9 桌面端 Python 嵌入运行时稳定性，需充分测试

---

### 10.2 中期优先级（6 个月）

**P1 - 高优先级**
1. **C9-4 到 C9-6**：终端 + UI 重构 + 打包分发
2. **工作流市场 MVP**：发布 + 发现 + 评分
3. **高级编排**：Parallel Gateway + 补偿事务
4. **企业身份集成**：SAML / SCIM
5. **Python/Go SDK**：多语言覆盖

**P2 - 中优先级**
6. **测试覆盖提升**：后端 90% / 前端单元测试
7. **插件市场基础**：官方插件库 + 发现页
8. **安全扫描自动化**：CI 集成 + 定期审计
9. **数据库性能优化**：索引 + 查询优化
10. **Dev Container**：VS Code / Codespaces 支持

**风险**：工作流市场与插件市场需要明确商业化策略

---

### 10.3 长期优先级（12+ 个月）

**P2 - 中优先级**
1. **SaaS 托管版本**：多租户 + 订阅计费
2. **CRDT 协同编辑**：Yjs 集成
3. **企业合规认证**：SOC 2 / ISO 27001
4. **跨工作流协调**：Signal 节点 + 分布式锁
5. **macOS/Linux 桌面版**：多平台支持

**P3 - 低优先级**
6. **WebGL 画布渲染**：突破 500 节点限制
7. **社区市场完整化**：MCP / 插件 / 工作流市场成熟
8. **高级数据安全**：端到端加密 + 差分隐私
9. **细粒度权限**：资源级 + 操作级权限
10. **营销与增长**：系统化市场推广

**风险**：长期项目需持续投入，需要资金与团队支持

---

### 10.4 技术债务管理

**立即处理**
- SQLite → PostgreSQL 迁移文档完善
- 前端 E2E 测试稳定性提升
- 依赖版本锁定 + 自动更新流程

**季度处理**
- 代码复杂度审计 + 重构
- 性能回归测试 + 基准线更新
- 安全漏洞扫描 + 修复

**年度处理**
- 主要依赖升级（LangGraph 2.x / React Router 8.x）
- 架构重构评估（事件驱动 / CQRS）
- 技术栈演进决策

---

### 10.5 资源需求评估

**团队配置建议**
- **核心开发**：3-5 全职工程师（前端 2 / 后端 2 / DevOps 1）
- **产品设计**：1 产品经理 + 1 UI/UX 设计师
- **社区运营**：1 开发者关系 + 1 技术写作
- **安全合规**：兼职安全顾问

**基础设施成本**
- **开发环境**：GitHub / CI/CD 免费额度
- **SaaS 基础设施**：云服务器 + 数据库 + CDN（$500-2000/月）
- **监控告警**：Grafana Cloud / Sentry（$100-500/月）
- **域名 SSL 证书**：$100/年

**时间投入估算**
- **C9 桌面端**：2-3 个月（1-2 工程师）
- **工作流市场**：1-2 个月（1 全栈工程师）
- **Provider/MCP 扩展**：持续投入（每个 1-2 周）
- **文档完善**：1 个月（技术写作 + 工程师配合）

---

## 结语

AgentCanvas 已完成从 C0 到 C8 的全部商业化阶段，形成了**生产级分布式执行 + 企业级多租户 + 完整安全加固 + 严格质量门禁**的坚实基础。当前 **1.0.0 候选发布**版本已具备真实商业化部署能力。

未来 6-18 个月的发展主线清晰：

1. **C9 桌面端原生化**（进行中）：摆脱浏览器依赖，提供开箱即用的原生体验
2. **平台能力增强**：工作流市场、高级编排、企业集成，构建完整生态
3. **技术债务清理**：依赖现代化、架构优化、性能提升，为规模化铺路

本路线图的优先级排序基于：
- **用户价值**：直接提升用户体验与生产力
- **技术风险**：降低长期维护成本与安全风险
- **商业潜力**：支撑商业化与生态扩展
- **资源可行性**：当前团队能力与时间约束

建议采用**渐进式交付**策略：每个季度完成 2-3 个 P1 优先级项目，持续验证市场反馈，根据用户需求动态调整路线图。同时保持技术债务的持续清理，避免积累到不可控的程度。

AgentCanvas 的核心竞争力在于**可视化编排 + MCP 生态 + 分布式执行**的独特组合。未来发展应继续强化这三大支柱，同时通过市场、SDK、文档降低用户接入门槛，形成正向飞轮效应。

---

**文档版本**：v1.0  
**最后更新**：2026-09-12  
**下次审查**：2026-12-12（3 个月后）