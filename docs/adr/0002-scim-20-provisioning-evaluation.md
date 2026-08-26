# ADR 0002: SCIM 2.0 供应集成评估

- 状态:**已接受(评估结论:暂缓实现,记录触发条件)**
- 日期:2026-08-23
- 关联:C7-4 成员协作补全;D3 身份/多租户基座;I1 分布式运行时

## 背景

C7-4 要求"SCIM 2.0 评估(先出 ADR)"。SCIM 2.0(RFC 7643/7644)是跨域身份管理系统(IdP,如 Okta/Entra ID/Ping)与 ServiceProvider 之间自动开通/回收账号的标准协议。企业客户通常要求:员工在 IdP 加入/离职时,平台成员关系自动同步,而不是靠管理员手动邀请。

## 现状盘点

平台当前的身份面:

- 本地账号(邮箱+密码)+ 会话/刷新令牌;OIDC PKCE 联邦登录(D3)。
- 组织 = 租户;`memberships` 承载组织级 RBAC(viewer/editor/admin)。
- C7-4 已交付:token 邀请链接(单次、可撤销、可过期)、成员页(角色变更/移除)、组织停用、平台管理台的用户停用。
- 审计与配额体系均以 organization/user 为锚点。

## SCIM 概念映射(若实现)

| SCIM | 平台概念 | 说明 |
|---|---|---|
| `/Users` | `users` | 外部 IdP 用户名映射为本地账号(email 作 userName);`active=false` 即用户停用(C7-3 语义) |
| `/Groups` | `organizations`(及其 `memberships`) | 一个 SCIM Group 对应一个组织;成员增删 = membership 增删 |
| `externalId` | 新增 `users.scim_external_id` / `memberships.scim_external_id` | 幂等对账键,IdP 侧主键 |
| `operations`(PATCH) | 角色 = SCIM Group 内的 `role` 扩展属性 | viewer/editor/admin 映射为自定义 schema `urn:agentcanvas:schemas:membership:1.0` |
| Bearer token | 新增 SCIM OAuth token(独立于现有 API token) | 仅覆盖 `/scim/v2/*`,按 IdP 配置 |

需要实现的最小端面:`POST/GET/PATCH/PUT/DELETE /scim/v2/Users`、`/scim/v2/Groups`、`GET /scim/v2/ServiceProviderConfig`、`/scim/v2/ResourceTypes`、`/scim/v2/Schemas`。清单过滤(`filter=userName eq "..."`)是 Okta/Entra 的硬性依赖。

## 决策

**暂缓实现 SCIM。** 理由:

1. **需求证据不足**:当前没有企业部署要求 IdP 驱动开通;C7-4 的邀请流已覆盖"手动加入"的全部路径,SCIM 是其自动化超集,在无 IdP 对接方时无法验收(协议符合性需针对具体 IdP 测试)。
2. **成本与风险不成比例**:SCIM 端面 + 过滤器解析 + 双向对账重放至少 5-8 人日;过滤器解析(RFC 7644 §3.4.2)是注入面,需要与 C8-2 公开面防护同批硬化;错误的重放可能造成成员意外移除(安全敏感)。
3. **架构先决条件已具备但未收口**:映射表已明确(上表),`memberships` 单一事实源、审计、停用语义均在;一旦实现,只新增只读镜像列与 SCIM 专用 token,不动 RBAC 执行点。

## 触发条件(满足其一即重新立项)

1. 首个企业客户在合同/安全评审中明确要求 Okta/Entra/其他 SCIM IdP 的自动 JIT 开通与离职回收。
2. 平台进入 SSO 强制模式(OIDC-only 登录)且组织数量增长到手动成员管理成为运维瓶颈(>50 组织或月度成员变更 >500 次)。
3. 需要向现有 OIDC 联邦补充"离职即回收"的合规承诺(此时优先评估 OIDC token 内 group claim 的窄方案,再评估全量 SCIM)。

## 后果

- 短期:成员管理走邀请流 + 成员页;企业 IdP 对接以"邀请链接 + 管理员操作"文档化。
- 重新立项时:按上表映射实现,SCIM 面与平台 API 分离部署限流(C8-2),过滤器解析用白名单 AST 而非字符串拼接,写操作全部落审计(`scim.user.*, scim.group.*`),并提供按 externalId 的幂等重放对账端点。
- 本 ADR 满足 C7-4 的"SCIM 2.0 评估(先出 ADR)"验收;SCIM 实现本身不在 C7 里程碑内。
