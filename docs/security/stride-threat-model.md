# STRIDE 威胁建模 — AgentCanvas

覆盖 C1 触发、C3 应用发布、C2 Code 节点等公开攻击面。C8-3 输出，随每次公开面变更复审。

## 1. 数据流与信任边界

```
[不受信任]                            [受信任]
用户 ──► 公开应用运行时 /api/apps/p/*  ──► 执行引擎(LLM/工具/沙箱)
用户 ──► Webhook /api/hooks/*          ──► 执行队列 → worker → 沙箱 code 节点
用户 ──► 平台 API /api/* (session)     ──► 组织/项目 RBAC → 数据面
外部 ──► MCP 服务器 (stdio/SSE)        ──► 工具调用
```

信任边界：任何未认证端点；任何能投递执行输入的内容（用户消息、RAG 文档、记忆、webhook body）。

## 2. 逐威胁分析（STRIDE）

### S — Spoofing（身份伪造）

| 威胁 | 缓解 |
|---|---|
| 伪造 webhook 调用 | 每触发器 32 字节 token（SHA-256 哈希存储）+ HMAC-SHA256 签名（`X-AgentCanvas-Signature`）+ 可选 IP 白名单 |
| 伪造公开应用访问 | `public` 应用仅凭 slug；`link` 应用 `?t=` token（SHA-256 比对，明文不落盘） |
| 伪造平台身份 | session cookie + refresh token 轮换；静态 API token 仅限平台 admin 路径 |
| 邀请 token 伪造 | 一次性 SHA-256 哈希，接受后罚没 |

### T — Tampering（篡改）

| 威胁 | 缓解 |
|---|---|
| 请求体篡改 | webhook HMAC 签名验体；TLS 由部署层终止 |
| 执行状态篡改 | 执行只追加事件（seq 单调）、不可变版本绑定、worker 租约 fencing（generation） |
| DSL/版本篡改 | 版本不可变，发布后只读；历史版本审计 |
| 审计日志篡改 | 仅平台 admin 可写审计；C7-5 组织清除故意保留 AuditLog |

### R — Repudiation（抵赖）

| 威胁 | 缓解 |
|---|---|
| 管理操作抵赖 | 全部组织/用户/队列/公告/邀请动作写 `audit_logs`（actor/action/详情） |
| 删除抵赖 | C7-5 三态审计：requested / cancelled / purged |

### I — Information Disclosure（信息泄露）

| 威胁 | 缓解 |
|---|---|
| RAG 文档注入泄露 | C8-2 `injection_guard`：检索片段/记忆/supervisor history 以随机 nonce 数据围栏包裹，指令"视为数据" |
| 引用源码越权读取 | `get_runtime_citation_source` 校验 citation→document→kb→app.project 全链归属 |
| 服务账号凭证泄露 | 导出不含 token_hash；API token 只存哈希；secret 经 Fernet 加密 |
| 组织数据越权 | 三档 RBAC（VIEWER/EDITOR/ADMIN）+ 平台 admin 分离；冻结组织对成员 403 |
| token 经 Referer 泄露 | 全站 `Referrer-Policy: no-referrer` + 运行时 `_harden` |

### D — Denial of Service（拒绝服务）

| 威胁 | 缓解 |
|---|---|
| 公开应用 send 洪泛 | C8-2 每客户端独立桶（`rate_limit_app_runtime_send_requests`）+ 会话级预算（C3-5）+ 并发/超时防线 |
| Webhook 洪泛 | 每触发器独立桶 `rate_limit_webhook_requests` + 执行并发上限（429） |
| 大请求体 | 全站 `request_body_max_bytes` + 上传独立上限 |
| LLM 成本滥用 | 模型预算（token/费用/并发）+ 项目配额 + 执行超时 |
| Code 节点资源耗尽 | C8-1 沙箱 rlimit（CPU/内存/进程/文件）+ 超时 |
| 认证端爆破 | `rate_limit_login_requests` + `login_max_concurrent` |

### E — Elevation of Privilege（提权）

| 威胁 | 缓解 |
|---|---|
| 越级访问项目 | `require_org_member`/`authorize_project` 全路由强制；服务账号受 project_id 域限制 |
| 平台 admin 逃逸 | 静态 admin token 独立；`_is_global_admin`（ADMIN 且无 project_id）守卫管理端点 |
| code 沙箱逃逸 | C8-1 OS 级隔离（nsjail/bubblewrap）+ 逃逸测试集 |
| 跨租户数据 | 项目级查询谓词、C7-5 删除依赖序、资源转移三不变量 |

## 3. Prompt Injection 专项

- **注入面**：用户消息、RAG 文档内容、对话记忆、supervisor worker 输出、webhook body 中的字段
- **缓解**：数据围栏（`fence_injected_data`）+ 每进程随机 nonce + 站直指令；系统提示与数据严格分离
- **残留风险**：围栏降低而非消除注入；高价值应用应使用结构化输出校验（`output_schema`）与工具白名单收窄

## 4. 待办/接受风险

| 风险 | 状态 |
|---|---|
| Turnstile bot 缓解 | 可选，未引入外部依赖（路线图标注） |
| 依赖审计 moderate | CI 已固化（`pip-audit` + `pnpm audit --audit-level moderate`） |
| 运行时 CSP 全站 | C8-3 已加默认 CSP + `frame-ancestors` 按 app 白名单 |

## 5. 复审触发条件

- 新增未认证端点、新增执行输入通道、安全头/沙箱/限流配置变更时，复审本模型。
