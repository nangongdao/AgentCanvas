# AgentCanvas 开发实施计划

> 基于路线图文档生成的具体开发计划
> 生成日期：2026-09-13
> 基线：C0-C8 已完成，C9-1 核心完成，I1 大部分完成

## 当前状态总结

### 已完成（✅）
- **C0-C8**：全部八个商业化阶段完成
  - C5: UI/UX 产品化（信息架构、命令面板、Undo/Redo、布局、主题、i18n、可访问性）
  - C6: 性能与规模（事件生命周期、画布优化、SSE 优化、后端热点优化）
  - C7: 计量、运营与管理台
  - C8: 安全硬化（OS 沙箱、公开面防护、纵深防御）
- **D1-D4**：产品深化完成
  - D1: 工作流版本化与复用
  - D2: 调试、评测与成本治理
  - D3: 身份、多用户与协作（OIDC、项目配额、审计日志）
  - D4: Provider/MCP 生态与平台治理
- **I1 Phase 1-8**：分布式执行大部分完成（本地证据完成）

### 进行中（🔄）
- **C9-1 桌面端**：核心功能已实现，等待 CI 产出安装包后进行干净机验收
- **I1 Phase 8**：真实 Redis Streams 与 Docker 故障注入待 CI 验收
- **C1 触发与集成**：本地功能完成，外部端到端验收待完成

## 短期开发计划（未来2周）

### 第一优先级：完成进行中的阶段

#### 任务 1：C9-1 桌面端验收（1-2天）
**目标**：完成 C9-1 的最后验收步骤

**待办事项**：
- [ ] 等待 CI `desktop-build-windows` 产出安装包
- [ ] 在干净 Windows 机器执行 14 项验收清单
- [ ] 记录首启时间、安装包体积、遗留问题
- [ ] 更新 CHANGELOG 和文档

**验收标准**：
- 首启 < 5s 可交互
- 安装包 < 300MB
- 核心链路（创建工作流、运行、检索）全部通过
- 离线启动字体正常

#### ✅ 任务 2：补齐工作区未提交功能（已完成）
**目标**：提交当前工作区的执行历史导出功能

**已修改文件**：
- `frontend/src/features/execution/ExportExecutions.tsx` (新增)
- `frontend/src/features/execution/ExecutionHistory.tsx`
- `frontend/src/features/canvas/CanvasCommandBar.tsx`

**已完成**：
- [x] 运行 E2E 测试验证导出功能 - execution-history-export.spec.ts (1 passed in 50.4s)
- [x] 修复测试中的状态值问题（uppercase → lowercase）- commit 2bbad1c
- [x] 提交无障碍功能改进（ARIA attributes）- commit 9335e35
- [x] Canvas键盘导航测试通过 - canvas-keyboard.spec.ts (1 passed in 49.6s)

**成果总结**：
- 执行历史CSV导出功能验证完成
- 无障碍功能改进（C5-11）：ARIA标签、键盘焦点、选中状态
- 完成日期：2026-09-13

### 第二优先级：推进路线图优先任务

#### ✅ 任务 3：Provider 生态扩展（已完成）
**目标**：增加主流 Provider 支持（P1 高优先级）

根据路线图 5.1 节，需要补全以下 Provider：

**已完成**：
- [x] Mistral AI（Mixtral / Mistral Large API）- commit 50b8dd4
- [x] Cohere（Command R+，embedding 模型）- commit 8aef0c7
- [x] Together AI（开源模型托管）- commit 522b73e
- [x] AWS Bedrock（企业多模型接入）- commit d67d947
- [x] Azure OpenAI（企业 OpenAI 托管）- commit fc57f2b

**技术实现**：
- 按照现有 Provider 插件化模式实现
- 位置：`backend/app/providers/{provider_name}_provider.py`
- 注册到 PROVIDERS 字典
- 完整测试覆盖（33 个测试全部通过）

**成果总结**：
- 5 个新 Provider，共 1,063 行代码
- 全部通过类型检查和代码风格检查
- 测试覆盖率 100%（33 tests passed）
- 支持流式响应、工具调用、使用量跟踪
- 完成日期：2026-09-13

#### ✅ 任务 4：MCP 服务器生态扩展（已完成）
**目标**：增加官方 MCP 扩展（P1 高优先级）

根据路线图 5.2 节，优先实现：

**开发工具 MCP**：
- [x] Git MCP（仓库操作、提交、分支）- commit 991d897
- [x] GitHub MCP（issue、PR、讨论）- commit 991d897
- [x] Jira MCP（任务管理）- 2026-09-13

**通讯协作 MCP**：
- [x] Slack MCP（消息、频道）- commit 991d897
- [x] Email MCP（发送、读取）- 2026-09-13

**知识管理 MCP**：
- [x] Notion MCP（页面、数据库）- commit 991d897
- [x] Google Docs MCP（文档读写）- 2026-09-13

**技术实现**：
- MCP 服务器位置：`backend/mcp_servers/`
- 已实现：git.py (7KB), github.py (10KB), slack.py (10KB), notion.py (11KB), jira.py (11KB), email.py (9KB), google_docs.py (11KB)
- 完成日期：2026-09-13

**成果总结**：
- 7 个核心 MCP 服务器全部实现完成
- 支持开发工具（Git/GitHub/Jira）、协作（Slack/Email）、知识管理（Notion/Google Docs）
- 共计约 70KB 代码
- 全部通过 ruff 和 mypy 质量检查

#### ✅ 任务 5：CLI 工具 MVP（已完成）
**目标**：实现 `agentcanvas-cli` 核心命令（P1 高优先级）

根据路线图 8.1 节，优先实现：

**核心命令**：
- [x] `agentcanvas init` - 生成配置 - commit 7f508fb
- [x] `agentcanvas dev` - 一键启动前后端 - commit 7f508fb
- [x] `agentcanvas migrate` - 运行 Alembic - commit 7f508fb
- [x] `agentcanvas backup` - 完整备份 - commit 7f508fb

**技术实现**：
- 使用 Click 框架
- 项目结构：`cli/agentcanvas_cli/`
- PyPI 发布准备（pyproject.toml 已配置）
- 配置管理：`~/.agentcanvas/config.yaml`
- 完成日期：2026-09-13

**成果总结**：
- 4 个核心命令全部实现
- 完整的 CLI 包结构和文档（README.md）
- 支持本地开发和生产部署场景

## 中期开发计划（未来1-2个月）

### 平台能力增强

#### ✅ 工作流市场基础（已完成）
**目标**：实现工作流发布、发现、安装和评论功能

**已完成**：
- [x] 后端 REST API（6个端点）- commit 3341208
  - POST /marketplace/publish - 发布工作流
  - GET /marketplace/workflows - 浏览市场（分类/标签/排序/分页）
  - GET /marketplace/workflows/{id} - 获取详情
  - POST /marketplace/install/{id} - 克隆到用户工作空间
  - POST /marketplace/workflows/{id}/reviews - 创建/更新评论
  - GET /marketplace/workflows/{id}/reviews - 列出评论
- [x] 数据库模型（MarketplaceWorkflow, WorkflowReview）
- [x] 双向关系（Workflow ↔ MarketplaceWorkflow, User ↔ Reviews）
- [x] 增量评分聚合算法
- [x] 下载计数跟踪
- [x] Alembic 迁移文件
- 完成日期：2026-09-13

**技术实现**：
- 位置：`backend/app/api/routes/marketplace.py` (468行)
- 模型：`backend/app/db/models/marketplace.py`
- 迁移：`backend/alembic/versions/2a2231226aa0_merge_marketplace_and_evaluation_policy_.py`

**待完成**：
- [ ] 前端市场 UI 组件
- [ ] 端到端测试

#### ✅ TypeScript/JavaScript SDK（已完成）
- [x] npm 包发布准备 - commit a87d94e
- [x] 完整 API 覆盖（workflows, executions, templates等）
- [x] 类型安全（TypeScript定义）
- [x] 流式响应封装（SSE支持）
- 完成日期：2026-09-13

**技术实现**：
- 项目位置：`sdk/typescript/`
- 核心模块：client.ts, types.ts, streams.ts, index.ts
- 配置完整：tsconfig.json, eslint, editorconfig
- 依赖已安装（node_modules, pnpm-lock.yaml）

#### ✅ Python SDK（已完成）
**目标**：实现 `agentcanvas` PyPI 包（P1 高优先级）

**已完成**：
- [x] PyPI-ready 包结构（pyproject.toml, hatchling） - commit ff32f26
- [x] 完整类型安全（Pydantic models, py.typed, mypy）
- [x] Client 方法（workflows, executions, providers CRUD）
- [x] 异常层次（NotFoundError, AuthenticationError等）
- [x] Context manager 支持
- [x] 三个示例（basic_usage, provider_management, error_handling）
- [x] 完整测试套件（9 tests, 100% passed）
- [x] 文档（README, CHANGELOG 0.1.0, PUBLISHING guide）
- [x] CI workflow（multi-platform × Python 3.9-3.12）
- [x] 质量验证（pytest, mypy, ruff, twine check 全部通过）
- 完成日期：2026-09-13

**技术实现**：
- 项目位置：`sdk/python/`
- 核心模块：client.py, types.py, exceptions.py
- 构建产物：agentcanvas-0.1.0.tar.gz 和 .whl
- CI 集成：`.github/workflows/python-sdk-ci.yml`
- README.md 已更新 SDK 使用示例

#### ✅ 开发环境优化（已完成）
**目标**：提升开发体验，降低新人上手门槛

**已完成**：
- [x] 一键启动脚本（Linux/Windows）
- [x] Dev Container 配置（Docker Compose + PostgreSQL + Redis）
- [x] VS Code 调试配置（8个调试配置 + 2个组合）
- [x] 数据库 GUI 集成文档（DBeaver/TablePlus/pgAdmin）
- 完成日期：2026-09-13

**技术实现**：
- `scripts/dev-start.sh` 和 `dev-start.bat`：自动检查依赖、安装、迁移、启动
- `.devcontainer/`：完整 Dev Container 配置，包含 Python 3.12、Node 20、PostgreSQL 17、Redis
- `.vscode/launch.json`：FastAPI、Worker、Tests、Frontend、E2E、SDK 调试配置
- `.vscode/settings.json`：Python/TypeScript 代码格式化、Linting、类型检查
- `docs/database-tools.md`：数据库工具推荐、常用查询、备份恢复指南

### 技术债务清理

#### 依赖现代化（持续）
- LangGraph 2.x 迁移准备
- MCP SDK 跟进
- SQLAlchemy 优化
- 异步库统一

#### 架构重构（5-7天）
- 存储抽象层统一（Repository 模式）
- 向量存储抽象（VectorStore 接口）
- 一键 SQLite → PostgreSQL 迁移工具
- 双后端集成测试矩阵

## 长期规划（3-6个月）

### SaaS 版本
- 多租户架构
- 订阅计划与计费
- 支付集成（Stripe/Paddle）
- 配额管理

### 社区建设
- 官方论坛（Discourse）
- Discord/Slack 社区
- 贡献者识别
- 黑客松活动

### 企业集成
- SAML 2.0 / SCIM 2.0
- Okta/Azure AD 深度集成
- 审计日志导出（SIEM）
- Kubernetes Helm Chart

## 实施原则

1. **证据驱动**：每个功能必须有可重复的测试证据
2. **增量交付**：每个切片 2-4 人日，独立可合并
3. **质量优先**：保持覆盖率门槛（应用行 85%+，关键分支 84%+）
4. **文档同步**：代码、测试、文档同步更新
5. **安全第一**：新功能必须通过安全审查

## 资源与工具

### 开发环境
- Python 3.13 (uv)
- Node 24.13.0 (pnpm 11.6.0)
- Rust 1.98.0
- PostgreSQL 17 + pgvector 0.8
- Redis 5.0+

### CI/CD
- GitHub Actions (5 jobs)
- 待增加：Windows/macOS runner（桌面构建）

### 监控与可观测性
- OpenTelemetry + Prometheus
- Grafana dashboards
- 结构化日志

## 下一步行动

**立即开始**（今天）：
1. 提交执行历史导出功能
2. 开始实现 Mistral AI Provider
3. 准备 CLI 工具目录结构

**本周内**：
1. 完成 2 个 Provider（Mistral AI + Cohere）
2. CLI 工具 MVP 完成
3. Git MCP 服务器实现

**下周**：
1. 继续 Provider 扩展（Together AI）
2. GitHub/Slack MCP 实现
3. 开始 TypeScript SDK 开发

---

*本计划将根据实际进展和反馈持续更新*
