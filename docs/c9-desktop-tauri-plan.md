# C9:桌面端原生化与体验重构(Tauri + 内置终端 + UI 分区重构)

> 本文档是 C9 阶段的实施计划。总体架构与阶段划分的单一事实源仍是 `docs/plan.md`;
> 视觉与交互的单一事实源仍是 `design-system/MASTER.md`。本阶段对两者的增补以本文
> 「12. 文档同步」列出的方式回写,不在此文档内另立事实源。

- 状态:计划待批准
- 日期:2026-08-25
- 前置:C0–C8 已完成(C5 UI/UX 产品化 C5-1…C5-11 全量关闭、C6 性能与规模五项性能门达成、C8 安全头与 CSP 落地)
- 来源:`docs/commercial-roadmap-2026-08-11.md` 第 5 节 Backlog「桌面端打包(Tauri,SQLite 单机模式复用)」(原估 5-8 人日,需求驱动)
- 修订:本阶段将原 backlog 条目从「打包」扩展为「原生化 + 内置终端 + 体验重构」,估算随之上调(见第 10 节)

---

## 1. 目标与非目标

### 1.1 目标

1. **摆脱浏览器**:双击图标启动即得完整可用的 AgentCanvas,不需要用户开浏览器、不需要用户装 Python/Node/Docker、不需要手动起两个服务。
2. **内置终端**:应用内提供真实 PTY 终端(xterm.js),并内置平台运维预设(起停后端、alembic 迁移、跑测试、看日志、MCP stdio 探活)。
3. **性能不退且有增**:桌面形态下重新定义性能门,既守住 C6 已达成的全部指标,又利用原生能力拿到浏览器拿不到的收益(字体本地化、启动时间、GPU 终端渲染)。
4. **UI 与交互重构**:在不推翻 `design-system/MASTER.md` 的前提下提升观感与交互质感,采用**分区施策**(见第 6 节)。

### 1.2 非目标

- 不做移动端(Tauri 2 支持 iOS/Android,但本项目的画布与数据密度不适配,且 C5 已交付 390px 响应式 Web 路径)。
- 不做自动更新(updater)的完整分发链路——本阶段只预留配置位与签名密钥流程,真实发布通道留后续切片。
- 不改后端业务语义。C9 对后端的改动限于:启动形态(sidecar 生命周期)、桌面模式配置(单机降级)、CORS/CSP 放行 Tauri origin、PTY 相关的新增端点(如落在后端)。
- 不替换 Web 部署形态。容器栈(`compose.prod.yml`)与桌面包是并行的两种分发,共用同一份前后端代码。

### 1.3 已确认的三项架构决策

2026-08-25 确认,后续切片不再重新论证:

| 决策 | 选择 | 代价 |
|---|---|---|
| 后端分发 | **托管式嵌入 Python 运行时**:Tauri sidecar 拉起随包分发的 Python onedir,Rust 负责启动/健康探测/退出回收 | 安装包 200–300MB(经 13.4 排除 chromadb 依赖树后修正,原估 350–600MB);向量后端改用 `VECTOR_BACKEND=sql` |
| 内置终端 | **真 PTY + 平台运维预设**:portable-pty 起真实 PowerShell/bash,另附平台任务预设 | PTY 能力必须纳入 C8 安全模型,默认仅本地会话可用 |
| UI 重构尺度 | **分区施策**:门面 surface 吸收参考站的动效工艺与材质层次;工作 surface 严守克制工业语言 | MASTER.md 增补边界章节而非被推翻 |

---

## 2. 现状盘点(本次实测)

### 2.1 工具链就绪度

本机已满足 Tauri 2 全部构建前置,无需额外安装:

| 组件 | 实测版本 | Tauri 要求 | 结论 |
|---|---|---|---|
| rustc / cargo | 1.98.0 | ≥ 1.77 | 就绪 |
| Node | 24.13.0 | ≥ 18 | 就绪 |
| pnpm | 11.6.0 | — | 就绪(项目 `packageManager` 锁定 11.6.0) |
| Python | 3.11.4(本机 PATH) | 3.13.x | 需对齐:后端 venv 实际为 **CPython 3.13**,嵌入运行时锚定 3.13(见 13.3) |
| MSVC Build Tools | VS 2022 | 必需 | 就绪 |
| WebView2 | 已安装 | Windows 必需 | 就绪(Win11 预装) |

### 2.2 前端现状

- React 19.2 + Vite 8.2 + Tailwind 4.3 + `@xyflow/react` 12.3 + zustand 5 + zundo,`react-router-dom` 7.18 用 `createBrowserRouter`。
- 路由与外壳:`main.tsx` 注册 15 个平台路由 + 1 个公开运行时路由 + 5 个兼容重定向;`PlatformShell.tsx` 提供 52px 全局顶栏 + 224px 左栏 + 移动抽屉。
- 设计 token 底子扎实:`index.css` 定义双主题 CSS 变量(void/ink/line/ghost/ice + pulse/volt/ok/bad/warn)、5 组 glow shadow、7 个命名动画,已在用 `cubic-bezier(0.16, 1, 0.3, 1)`(expo-out)。
- 画布性能已被 C6-2 深度优化:selector 级订阅、条件虚拟化(>50 节点启用 `onlyRenderVisibleElements`)、节点 `aria-label` 在创建期烘焙以保 memoization。
- 已交付质量线:初始 gzip 97.91 KiB(预算 120)、运行页 5.54 KiB(预算 40)、Playwright 71/71、500 节点拖拽 p95 59.5–67.5ms。

### 2.3 桌面化必须处理的「仅浏览器假设」

实测扫描出以下具体改造点,每一处都需在对应切片验证:

| 位置 | 现状 | 桌面影响 |
|---|---|---|
| `frontend/index.html:8-13` | 字体走 Google Fonts CDN(`fonts.googleapis.com`) | **离线首启会 fallback 到系统字体**,整个视觉语言崩塌。必须自托管 Noto Sans SC + JetBrains Mono |
| `api/sse.ts:86,200`、`useWorkflowCollaboration.ts:158` | 三处 `new EventSource` | Tauri 页面 origin 为 `tauri://localhost`,访问 `127.0.0.1:8000` 是**跨源**;后端 CORS 必须显式放行,且 `EventSource` 不带自定义头 |
| `AuthProvider.tsx:140` | OIDC 走 `window.location.assign("/api/auth/oidc/start")` | 桌面无浏览器 redirect 回跳;需改为系统浏览器打开 + 本地回调端口或 deep link |
| `AuthProvider.tsx:100,115` | 两处 `window.location.reload()` | Tauri 下可用但语义粗糙,应改为路由级状态重置 |
| `WorkflowVersionPanel.tsx:168-172` | `createObjectURL` + `<a download>` 导出 DSL | 应改为原生保存对话框(`tauri-plugin-dialog` + `fs`),否则落到不可预期的下载目录 |
| `CommandPalette.tsx:284`、`AppsPage.tsx:53`、`WorkflowTriggerPanel.tsx:117` | 三处 `navigator.clipboard` | WebView2 下需验证权限;必要时切 `tauri-plugin-clipboard-manager` |
| `AppsPage` embed / `app-embed.spec.ts` | 应用嵌入依赖 iframe + 跨源 host 页 | 桌面壳内 iframe 语义变化,需确认 embed 功能在桌面版的定位(建议:桌面版只生成代码片段,不在壳内预览) |

### 2.4 CI 现状与缺口

- 现有 5 个 job 全在 `ubuntu-latest`;`publish-release` 有严格发布把关(版本三方核对、拒绝覆盖已有 tag、changelog 校验)。
- **缺口**:桌面产物需要 Windows/macOS runner 才能构建(Tauri 不支持交叉编译 Windows/macOS 安装包)。这是 CI 拓扑的实质变化,不是加一个 step。

---

## 3. 桌面架构

### 3.1 进程拓扑

```
┌─ AgentCanvas.exe (Tauri 2.11 / Rust) ───────────────────────────┐
│                                                                  │
│  主窗口 WebView2 ── tauri://localhost ── 现有 React 应用(原样)   │
│      │                                                           │
│      ├─ IPC(小消息:窗口控制、菜单、设置、对话框)                │
│      ├─ Channel(高吞吐:PTY 输出流)                              │
│      │                                                           │
│  Rust 侧                                                         │
│      ├─ SupervisorState:sidecar 生命周期(启动/健康/重启/回收)   │
│      ├─ PtyManager:portable-pty 会话池                          │
│      └─ 单实例守卫 + 深链接 + 托盘                              │
│      │                                                           │
│      └─ sidecar ─► Python onedir(uvicorn app.main:app)          │
│                     127.0.0.1:<动态端口>                         │
│                     SQLite + 嵌入 Chroma + MCP stdio             │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 前端如何访问后端(关键决策)

现有前端全部走相对路径 `/api/*`(dev 由 Vite proxy 转发)。桌面下页面 origin 是 `tauri://localhost`,相对路径不再指向后端。三种接法,**选 B**:

| 方案 | 做法 | 判断 |
|---|---|---|
| A. 全量改造为绝对地址 | 前端读运行时注入的 `http://127.0.0.1:<port>` 并拼接 | 要改所有 fetch 调用点;`EventSource` 跨源;CORS 面变大。**否** |
| **B. Rust 侧注册 `/api` 反向代理(选用)** | 在 Tauri 用 `register_uri_scheme_protocol` 或 `register_asynchronous_uri_scheme_protocol` 把 `tauri://localhost/api/*` 透传到 sidecar,**前端代码零改动**、同源、无 CORS | 需自行处理 SSE 流式透传(异步协议 handler 支持流式响应体)。这也顺带绕开了 IPC 带宽墙 |
| C. `tauri-plugin-localhost` | 让页面本身跑在 `http://localhost:<port>` | 官方文档明确警告「considerable security risks」。**否** |

**方案 B 的风险与验证前置**:SSE 经自定义协议透传是本阶段**第一个必须打通的技术验证点**(见 4.1 的 Spike)。若实测流式透传不可用(响应被缓冲、`EventSource` 收不到增量),回退为**方案 B'**:仅 `/api` 的普通 REST 走协议代理,三处 SSE 改为直连 `http://127.0.0.1:<port>` 并在后端 CORS 显式放行 `tauri://localhost`。B' 需要改动的前端点已限定为 `api/sse.ts` 与 `useWorkflowCollaboration.ts`,面很小。

> **Spike 结论(2026-09-12,方案 B 被否,采用 B')**
>
> 按本节原设计,Spike 对「自定义协议能否流式透传 SSE」做了源码级验证,直接核对项目将采用的确切发布线 **`tauri-v2.11.5`** 与 **`wry-v0.57.0`**(Windows WebView2 后端),两层证据一致且相互独立:
>
> 1. **Tauri 公开 API 不存在分段发送通路**:`UriSchemeResponder` 是 `FnOnce`(自消费,只能调用一次),签名 `respond<T: Into<Cow<'static, [u8]>>>(self, http::Response<T>)` —— 整个响应体必须一次性物化后交出(`crates/tauri/src/app.rs` L2455-2462,tag `tauri-v2.11.5`)。API 面上没有任何 partial-write / chunk API。
> 2. **wry 在 WebView2 上全量缓冲后才交还浏览器**:自定义协议经 `AddWebResourceRequested` + deferral 处理,`prepare_web_request_response` 用 `SHCreateMemStream(Some(content))` 把完整 body 物化进内存流后才 `SetResponse` + `Complete()`(`src/webview2/mod.rs` L1179-1200,tag `wry-v0.57.0`)。WebView2 拿到的是完整响应对象,`EventSource` 只能在上游流结束后一次性收到全部数据。
> 3. wry 的 `ProxyConfig` 只是 HTTP-CONNECT/SOCKS **网络代理**配置,与自定义协议桥接无关。
>
> 两层相加是构造性结论:**任何经 `register_(a)synchronous_uri_scheme_protocol` 的响应都不可能增量到达页面**,不是可修的 bug 而是公开 API 不表达这种能力。因此不再需要运行时复测——单次实验只能证明某一次缓冲,源码证明的是不存在另一条路径。理论出路是对 wry 打叉改用自实现 `IStream` 活管道,但这超出本计划的成本边界,不做。
>
> **采用方案 B'**,并在此固化三个实现要点(正式 C9-1 按此执行):
> - REST 仍走协议代理(缓冲语义对 JSON API 恰好正确,前端 fetch 零改动);
> - 三处 SSE(`api/sse.ts` ×2、`useWorkflowCollaboration.ts` ×1)直连 `http://127.0.0.1:<动态端口>`,端口由 Rust 初始化脚本注入(如 `window.__AGENTCANVAS_BACKEND_ORIGIN__`);
> - 后端 CORS 显式放行 **`tauri://localhost` 与 Windows 变体 `http://tauri.localhost`**(WebView2 下自定义协议页面的实际 origin 是后者),拒绝通配 `*`,且桌面模式下不启用 HSTS(见 5.2)。

### 3.3 嵌入 Python 运行时

- **形态**:`--onedir` 而非 `--onefile`。onefile 每次启动解压到临时目录,冷启动慢且被杀软误报率高;onedir 首启即可用。
- **版本对齐**:嵌入 **CPython 3.13.x**。`requires-python = ">=3.12"` 只是下限,后端 venv 与 CI 实跑 3.13(`ruff target-version = py312` 仅约束语法),嵌入版本锚定 3.13 以免「本地过、打包炸」。详见 13.3。
- **依赖裁剪(经审计修正,见 13.4)**:桌面版固定 `VECTOR_BACKEND=sql`,改用 `app/rag/sql_store.py` 而非 Chroma,从而整棵排除 chromadb 依赖树:

  ```
  --exclude-module chromadb --exclude-module onnxruntime
  --exclude-module kubernetes --exclude-module numpy
  --exclude-module grpc --exclude-module tokenizers
  ```

  实测可省约 160MB。安全性已验证:`app/` 下无模块级 `chromadb` 导入(唯一导入在 `chroma_store.py:41` 方法内部),且 `app/` 完全不用 numpy。**原「chromadb ONNX hidden-import」坑随之消失——不打包它就没有这个问题。**
- **代价须明示**:SQL 向量后端是全表余弦扫描,非 HNSW 近似索引。万级 chunk 可接受,十万级需重新评估。写入 ADR。
- 其余压缩手段:排除测试与文档、`--exclude-module` 清理未用的 provider SDK。体积目标写进验收:见第 7.3 节性能门。
- **端口**:不硬编码 8000。Rust 侧向 OS 申请空闲端口后以环境变量传给 sidecar,并注入前端。避免与用户已在跑的开发实例冲突。
- **数据目录**:**零后端改动**。`app/core/config.py` 已有 `APP_DATA_DIR` 环境变量,主库/checkpoints/向量库/uploads/workspace/本地密钥全部由它派生。Rust 侧注入 `APP_DATA_DIR=%APPDATA%\AgentCanvas`(macOS/Linux 用对应 XDG 路径)即可,不落安装目录。详见 13.1。

### 3.4 桌面模式的后端降级

桌面单机模式复用既有的降级能力,不新造机制:

- PostgreSQL → SQLite(`docs/plan.md` 已声明 SQLite 是单机基线)。
- Redis → 既有的 `MemoryStore` SQLite 降级路径(项目已实现「ping 失败自动降级」)。
- 多进程 worker/scheduler → 单进程 `process_role=all`(既有配置维度)。
- 认证 → 桌面默认单用户本地会话,OIDC 作为可选(见 5.4)。

> C9-1 需要后端出一份 `desktop` 配置 profile,把上述降级固化为一组默认值,而不是让 Rust 侧拼一长串环境变量。

---

## 4. 切片划分

延续 D/I/C 阶段的成功模式:每片 2–4 人日、独立可合并、各自带门禁证据。

### C9-1 Tauri 外壳与 sidecar 生命周期(4–6 人日)

**这是全阶段风险最高的一片,必须先做完再谈其他。**

先行 Spike(0.5 人日,不产出正式代码,只回答能不能):
1. 建最小 Tauri 2.11 工程,`register_asynchronous_uri_scheme_protocol` 代理 `/api` 到本机已在跑的 uvicorn。
2. 用现有 `GET /api/executions/{id}/events` 验证 **SSE 增量是否真的逐条到达前端**(不是等流结束才吐)。
3. 结论写入本文档 3.2:确认方案 B,或落到 B'。

**Spike 已完成(2026-09-12)**:以源码级验证替代运行时实验——对 `tauri-v2.11.5` 的 `UriSchemeResponder`(FnOnce 单次完整 body)与 `wry-v0.57.0` 的 `SHCreateMemStream` 全量物化直接取证,证明增量送达路径**构造性不存在**;结论为落到 B',已固化在 3.2 的结论块。C9-1 正式实现阶段无需再建验证工程,Spike 的 1、2 两步作废。

正式实现:
- `src-tauri/` 工程接入 pnpm workspace;`tauri.conf.json` 的 `frontendDist` 指向现有 `frontend/dist`,`devUrl` 指向 5173,**现有 `pnpm dev` 流程不被破坏**。
- Rust `SupervisorState`:申请空闲端口 → 起 sidecar → 轮询 `/healthz` 直到就绪(带超时与失败态)→ 窗口显示;退出时确保子进程被回收(Windows 用 Job Object,避免孤儿 uvicorn)。
- 启动期 UI:原生启动窗口(splash)显示「正在启动运行时」的真实阶段(解压 / 迁移 / 就绪),失败时给可复制的诊断信息与日志路径,而不是白屏。
- alembic 迁移在首启与升级时自动执行,失败必须可见、可重试、可回滚提示(数据在用户机器上,不能静默失败)。
- 单实例守卫:第二次双击聚焦已有窗口,不起第二个后端。
- PyInstaller spec + 体积压缩 + `externalBin` 三平台命名(`-x86_64-pc-windows-msvc` 等)。

**验收**:干净 Windows 机(或干净用户账户)双击安装包 → 无 Python/Node/Docker → 应用启动 → 概览页有数据 → 创建并运行一个工作流看到流式输出 → 关闭应用后进程表无残留 uvicorn。安装包体积记录进证据。

### C9-2 内置终端(3–5 人日)

- Rust `PtyManager`:`portable-pty` 会话池,支持多标签、resize、独立环境变量与工作目录。输出**必须走 Tauri Channel 而非 `emit`**——`emit` 会序列化为 JSON 经 IPC,大量输出直接撞带宽墙。
- 前端 `features/terminal/`:`@xterm/xterm` + `@xterm/addon-webgl`(**不用已废弃的 `addon-canvas`**,v6 会移除)+ `addon-fit` + `addon-search` + `addon-web-links`。必须处理 `webglcontextlost` 事件并降级到 DOM 渲染器。
- 主题联动:终端配色从既有 CSS 变量派生,亮/暗主题切换同步生效;字体用自托管 JetBrains Mono。
- 平台运维预设(决策 2 的落地):起停后端、`alembic upgrade head`、跑 pytest/Playwright、tail 后端日志、MCP stdio 服务探活。预设是**预填命令的新终端**,用户可见、可编辑、可取消,不做隐式执行。
- 布局:底部可停靠面板(可折叠/最大化/拖拽改高),或独立窗口。快捷键遵循桌面惯例(`Ctrl+``)。
- 安全(C8 对齐):PTY 命令由用户在本地发起,不接受来自渲染层的任意远程输入;审计上记录会话开启/关闭而非全部击键(避免把密码写进审计);默认工作目录限定项目根,不给逃逸到系统盘根的默认入口。

**验收**:开三个标签跑不同 shell;`yes` 类高吞吐输出不卡 UI(帧率与主线程阻塞有实测数);resize 正确重排;主题切换同步;预设任务真实跑通 alembic 与 pytest;关闭应用回收全部子进程。

### C9-3 桌面原生交互层(3–4 人日)

- 自定义标题栏:`decorations: false` + 自绘拖拽区,与 52px 顶栏合并为一条(桌面不需要浏览器地址栏,这条空间省下来给内容)。**必须保留系统能力**:双击最大化、边缘吸附、右键系统菜单、Windows Snap Layouts。
- 窗口材质:Windows 11 用 `window-vibrancy` 的 Mica、macOS 用 vibrancy。**已知坑**:`transparent: true` 下初始尺寸会白底、圆角失效——若实测观感不稳,退回不透明 + 现有 glass token(观感差异小,稳定性差异大)。
- 原生集成:系统菜单栏与快捷键、托盘(常驻/最小化到托盘)、原生文件对话框(替换 2.3 表中的 `<a download>`)、系统通知(执行完成/失败)、深链接 `agentcanvas://`(替换 OIDC 回跳)。
- 窗口状态持久化:位置/尺寸/最大化/终端面板高度跨会话恢复。
- 多窗口(评估后决定是否本片交付):画布独立开窗,便于双屏对照。

**验收**:标题栏交互与系统一致(逐项核对上述系统能力);材质在 Win11 亮/暗主题下正确;托盘与通知可用;导出 DSL 走原生保存框;窗口状态跨重启恢复。

### C9-4 UI 与交互重构 —— 分区施策(5–8 人日)

细则见第 6 节。切片内部按 surface 分批,每批独立截图基线:
- C9-4a 门面层:启动/加载态、概览页、空状态、引导、发布与成功态。
- C9-4b 动效 token 层:把参考站的工艺抽成 duration/easing/spring token 与编排原语,全站沿用。
- C9-4c 工作面精修:画布、Inspector、表格、命令面板的交互质感(不改视觉构成,只改反馈精度)。

### C9-5 性能与门禁(3–4 人日)

细则见第 9 节。包含:字体自托管、桌面启动时间门、终端吞吐门、桌面 e2e 通道(CDP)、三平台 CI 构建、体积门。

---

## 5. 后端改动清单

**注:本节的文件级定位以 C9-1 开工前的后端审计为准,下列为改动面与约束,不预设实现细节。**

### 5.1 桌面配置 profile

新增一组 `desktop` 默认值(SQLite、`process_role=all`、Redis 关闭、数据根目录来自注入、绑定 `127.0.0.1`),让 Rust 侧只需传端口与数据目录两个变量。

### 5.2 CORS / CSP 放行 Tauri origin

C8 落地了站点级安全头与 HSTS。桌面下:
- REST 走 3.2 方案 B 的协议代理部分,前端与后端在自定义协议内**同源**,CORS 不需放开——这是混合方案保留的收益。
- SSE 按 3.2 已定的 B' 直连 sidecar,必须显式放行 `tauri://localhost` 与 **Windows 变体 `http://tauri.localhost`**(WebView2 下自定义协议页面的实际 origin)。**不能用 `allow_origins=["*"]`**,凭据请求下无效且是安全退步。
- CSP 需要容纳 `tauri://` / `http://tauri.localhost` 与自托管字体,并为 SSE 直连目标(`http://127.0.0.1:<port>`)加 `connect-src`;桌面模式下不应保留 HSTS(无 HTTPS 语义)。

### 5.3 sidecar 就绪与优雅退出

- **就绪探针零改动**(经审计修正,见 13.2)。后端已有 `/healthz`、`/livez`(存活)与 `/readyz`(就绪),均免认证免限流。`/readyz` 已逐项检查 database / migrations / checkpointer / config / vector_store / sandbox,任一 unavailable 返回 503。Rust 监工轮询 `/readyz` 拿 200 才放行前端;返回体的 `checks` 可直接逐项渲染在启动画面上,比原计划体验更好且不写后端代码。
- 收到终止信号时优雅关闭:停 scheduler、断 MCP stdio 子进程、flush event bus。桌面用户会直接点窗口关闭按钮,这条路径必须干净。**这一项仍需后端确认现状**,是本节唯一可能有改动量的地方。

### 5.4 桌面认证

桌面默认单用户本地会话。OIDC 若保留为可选,回跳改为:系统浏览器打开授权页 → 回调到 `agentcanvas://` 深链接或本地临时回调端口 → Rust 转交前端。这替换 `AuthProvider.tsx:140` 的 `window.location.assign`。

### 5.5 PTY 归属:放 Rust,不放后端

终端的 PTY 由 **Rust 侧**持有,不新增后端端点。理由:
- 桌面终端是本地能力,经后端会多一跳 HTTP/SSE 且要新造鉴权面;
- 后端已有的 MCP stdio 子进程管理与之无关,不应混入;
- Web 部署形态**不应该**获得 PTY 能力——若放后端,Web 部署也会暴露这个面,与 C8 安全硬化方向相反。

> 这条同时是安全边界:PTY 只存在于桌面二进制里。

---

## 6. UI 与交互重构:分区施策

### 6.1 先说清冲突

用户提供的参考效果来自 [designprompts.dev](https://designprompts.dev) 与 [motionsites.ai](https://motionsites.ai)。两站的实际性质是**AI 设计 prompt 库**:前者是设计风格画廊(Swiss/brutalist 排版、编辑式布局、高对比),后者是 68+ 动效落地页 demo 库(视频背景、glassmorphism、滚动驱动 hero)。相关公开描述见 [MotionSites 说明](https://chatgate.ai/post/motionsites) 与 [prompt library](https://motionsites-ai-prompt-library.vercel.app/)。

`design-system/MASTER.md` 的现行方向与之直接对立,原文:产品性格为 "industrial, operational, data-dense, and quiet";明文 "Avoid marketing composition, nested cards, decorative blobs, 3D charts, crowded legends, and oversized headings inside work surfaces"。

照搬的具体代价不是抽象的「风格不符」,而是:
- 亮色主题的 accent 是**特意调暗一档**才过 WCAG AA 的(`--accent-pulse: #155e75` 等),换成参考站的高饱和霓虹会直接破 axe 对比度门;
- C5 各切片建立了截图基线防漂移,全站视觉换代等于重建全部基线;
- 画布上的视觉改动同时是性能改动(C6-2 的教训:一个内联箭头函数击穿 memo 就让 500 节点拖拽从 59ms 掉到 132ms)。

### 6.2 分区边界

| | 门面 surface | 工作 surface |
|---|---|---|
| **范围** | 启动窗口/加载态、概览页、空状态与引导、模板库、发布与成功态、设置页的说明区 | 画布、Inspector/配置面板、执行时间线、各类表格、命令面板、终端 |
| **可用手法** | 入场编排(stagger)、材质层次(景深/噪点/渐变边)、数值滚动、状态转场、有意义的空间叙事 | 仅状态反馈与操作确认;克制、即时、可预测 |
| **禁止** | 仍然禁止:视频背景、装饰性 blob、滚动劫持、纯装饰的 3D | 禁止一切装饰性动效;禁止任何进入每帧路径的新计算 |
| **动效预算** | 单次入场 ≤ 600ms,可编排 | 交互反馈 ≤ 200ms,不可编排 |

**判定规则**:一个 surface 属于门面还是工作面,看用户在它上面**停留时是在读还是在做**。读=门面,做=工作面。

### 6.3 从参考站移植什么(工艺,不是构成)

这是本节的实操核心。移植以下四类,每类都落成 token 或原语:

1. **缓动与时长体系**。现有只有 `cubic-bezier(0.16,1,0.3,1)` 一条曲线在复用。扩成一组语义 token:`--ease-out-expo`(入场)、`--ease-out-back`(轻微过冲,用于确认态)、`--ease-in-out-quart`(位移)、`--duration-instant/fast/base/slow`。**所有值必须与 `prefers-reduced-motion` 联动降级为 0**(现有已全覆盖,不能倒退)。
2. **编排(stagger)原语**。参考站的高级感主要来自「元素按序入场」而非单个元素更花。做一个 CSS-first 的 stagger 工具(`animation-delay` 按 index 递增,或用 `@starting-style`),门面 surface 复用。**不引入 GSAP/Framer Motion**——现有 bundle 预算 120 KiB,加动画库是净亏;CSS 动画 + Web Animations API 足够,且不占主线程。
3. **材质层次**。现有 glass token 只有一档(`--surface-glass`)。扩成 2–3 档景深(近/中/远),配合边缘高光(1px inset 亮线)与极低强度噪点。**桌面下这一层可由 Mica/vibrancy 真实提供**,这是桌面形态相对 Web 的真实视觉优势。
4. **状态转场的精度**。工作 surface 唯一允许的提升:让状态变化有明确的来源指向(如执行从哪个节点流到哪个),而不是全局闪一下。这条要与 C6-2 的 memoization 约束一起设计。

### 6.4 交互效果重构(不只是视觉)

桌面形态解锁的交互提升:
- **命令面板升格为桌面级**:全局快捷键(应用未聚焦也可召唤,经 Tauri global shortcut)、最近项、动作历史。
- **画布交互**:原生手势(触控板捏合缩放的精度在 WebView 里可控)、右键原生上下文菜单、拖放文件直接进画布/知识库。
- **多任务**:执行中最小化到托盘、完成时系统通知、任务栏进度(Windows)。
- **键盘优先**:桌面用户预期完整键盘操作。现有 C5-11 已建可访问性底子,本片补齐面板间焦点循环与快捷键索引页。

---

## 7. 性能优化

### 7.1 桌面形态带来的真实收益(不是营销话术)

| 项 | Web 现状 | 桌面可达 | 机制 |
|---|---|---|---|
| 字体 | Google Fonts CDN,首启阻塞 + 离线失效 | 本地零延迟 | 自托管 woff2 + `font-display: block` 不再需要 |
| 首屏 | 网络往返 + 未缓存首访 | 磁盘直读 | `frontendDist` 打进二进制 |
| 终端渲染 | — | GPU | `addon-webgl` |
| 画布 | 受浏览器 GPU 策略与其他标签页竞争 | 独占进程、可控 GPU 开关 | WebView2 启动参数 |
| API 延迟 | 反代 + 网络栈 | 本机回环 | sidecar 同机 |

### 7.2 必须新做的优化

1. **字体自托管**(C9-5,也是 2.3 的必修项):Noto Sans SC + JetBrains Mono 子集化(中文常用字 + 拉丁 + 中文标点),woff2,`preload`。Web 部署同样受益,顺带去掉两个 `preconnect` 与一个第三方阻塞请求。注意 Noto Sans SC 的 CJK 子集体积远大于拉丁字体,必须按使用字符集裁剪,不能整字重全量内嵌。
2. **启动时间**:sidecar 冷启动是桌面版最大的体感风险。**排除 chromadb 依赖树后此风险显著下降**(见 13.4:不再 import chromadb/onnxruntime/numpy/grpc)。剩余手段:延迟导入非首屏依赖、窗口先显示骨架而非等后端就绪、用 `/readyz` 返回体的 `checks` 分项渲染启动进度(已有能力,见 13.2)。
3. **终端吞吐**:PTY 输出经 Channel 传输 + 前端批量写入(按帧聚合而非逐 chunk `write`),避免高吞吐输出时主线程被打满。
4. **不引入动画库**:见 6.3 第 2 条。bundle 预算不动。
5. **画布不退步**:C9-4c 的任何改动都必须跑 `pnpm test:perf-scale`(500 节点/2000 边),这是硬门。

### 7.3 桌面性能门(新增,纳入验收)

| 指标 | 目标 | 依据 |
|---|---|---|
| 冷启动到可交互 | < 5s | 桌面应用体感底线;含 sidecar 起 + 迁移检查 |
| 冷启动到全功能就绪 | < 12s | Python 重依赖 import 的现实约束 |
| 终端高吞吐(持续输出)主线程最长阻塞 | < 100ms | 对齐 C6 的 maxLongTask 口径 |
| 安装包体积(Windows) | < 450MB,目标 < 300MB | 经 13.4 依赖裁剪后收紧(原为 600 / 400) |
| 内存驻留(空闲,含 sidecar) | < 500MB | 无 onnxruntime/numpy 驻留后收紧(原 600MB) |
| **C6 既有全部性能门** | **不退步** | 500 节点拖拽 p95 < 100ms、初始 gzip < 120 KiB、运行页 < 40 KiB、执行创建 c50 p95 < 2s、1000 事件 SSE 重放 < 150ms、10k 分页 p95 < 300ms |

---

## 8. 测试策略

### 8.1 现有资产必须复用,不重写

34 个 Playwright spec 是本项目的核心质量资产。桌面化**不新建平行测试体系**:

- **Web 通道保持不变**:34 个 spec 继续在浏览器跑,继续是主门禁。桌面化不能让 Web 形态退化。
- **桌面通道(Windows)**:实测确认可行——Tauri 启动时设 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222`,Playwright 经 CDP `connectOverCDP` 连上 WebView2,**现有 spec 可复用**。新增 `playwright.desktop.config.ts` 挑选一个关键子集(启动、画布、执行流式、终端、原生对话框)在桌面通道跑。
- **macOS 的不对称限制**:WKWebView **没有 CDP**,Playwright 方案在 macOS 不可用,只能走 WebdriverIO + `tauri-driver`(而 `tauri-driver` 本身在 macOS 也无 WKWebView driver)。**结论**:macOS 桌面 e2e 本阶段不做自动化,以人工验收清单 + Web 通道覆盖代偿,并在文档明示这一缺口。不要假装三平台等价覆盖。

### 8.2 Rust 侧测试

- `SupervisorState` 的状态机(启动/就绪/失败/重启/回收)单测。
- `PtyManager` 的会话生命周期与并发写入单测。
- 协议代理的 SSE 流式透传集成测试(3.2 的核心风险点,必须有回归保护)。

### 8.3 新增验收清单(人工,写进文档)

干净机首装流程、三平台窗口交互逐项、离线启动、升级路径(旧数据目录 → 新版本迁移)、卸载后残留检查。

---

## 9. CI 与发布工程

### 9.1 CI 拓扑变化

现有 5 个 job 全在 `ubuntu-latest`。Tauri **不能交叉编译** Windows/macOS 安装包,必须新增 runner:

| 新增 job | runner | 产出 |
|---|---|---|
| `desktop-build-windows` | `windows-latest` | `.msi` / `.exe`(NSIS) |
| `desktop-build-macos` | `macos-latest` | `.dmg`(需 universal 或双架构) |
| `desktop-build-linux` | `ubuntu-latest` | `.deb` / `.AppImage` |
| `desktop-e2e-windows` | `windows-latest` | CDP 通道 e2e 子集结果 |

**成本提示**:Windows/macOS runner 分钟数计费倍率高(macOS 通常 10x),且 Rust 编译 + PyInstaller 打包耗时长。建议:桌面 job 只在 tag 与手动触发时跑全量,PR 上只跑 `cargo check` + `cargo clippy` + `cargo test`(Linux 即可)。

### 9.2 发布把关延续现有严格度

现有 `publish-release` 有版本三方核对(tag / `PRODUCT_VERSION` / `pyproject.toml`)、拒绝覆盖已有 tag、changelog 校验。桌面产物需纳入同一套:
- `tauri.conf.json` 的 `version` 加入三方核对(变成四方)。
- 安装包体积门作为发布前断言(超限即失败,防止依赖膨胀无声通过)。
- 签名:Windows 代码签名与 macOS notarization 需要证书。**本阶段不申请证书**,只预留配置位与文档;未签名包在 Windows 会触发 SmartScreen、macOS 会被 Gatekeeper 拦截。这是必须对用户明示的现实限制。

---

## 10. 估算与排期

| 切片 | 人日 | 依赖 |
|---|---|---|
| C9-1 Tauri 外壳与 sidecar(含 0.5 天 Spike) | 4–6 | 无(须先完成) |
| C9-2 内置终端 | 3–5 | C9-1 |
| C9-3 桌面原生交互层 | 3–4 | C9-1 |
| C9-4 UI 与交互重构(4a/4b/4c) | 5–8 | C9-1(4c 需 C6-2 性能门在手) |
| C9-5 性能与门禁(含 CI 三平台) | 3–4 | C9-1…C9-4 |
| **合计** | **18–27** | |

原 backlog 估算 5–8 人日只覆盖「打包」。本阶段包含内置终端、原生交互层、UI 重构与三平台 CI,故上调。建议里程碑 **M12 桌面版 `v1.6.0`**(排在 C7 商用运营版之后,或按需求优先级插队到 C7 之前——这是产品优先级选择,不是技术依赖)。

**推荐推进顺序**:C9-1 → C9-2 → C9-3 → C9-4 → C9-5。理由:C9-1 的 Spike 结论会影响后续所有片;C9-4 放在原生层之后,才能把 Mica/vibrancy 的真实材质纳入设计,而不是先做一遍 CSS 假玻璃再推翻。

---

## 11. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| ~~**SSE 经自定义协议不能流式透传**~~ **已证实(2026-09-12 Spike)** | 方案 B 不可用,执行流式输出与协作全断 | 源码级证实为构造性限制(见 3.2 结论块);**按预案落到 B'**(REST 走协议代理 + SSE 直连 + CORS 放行含 Windows 变体 `http://tauri.localhost`),改动面仍是两个文件。此风险关闭 |
| ~~PyInstaller 打包 chromadb ONNX 失败~~ | — | **已消除**:13.4 改用 `VECTOR_BACKEND=sql` 并整棵排除 chromadb,不打包即无此风险 |
| **PyInstaller 打包其余重依赖失败**(langgraph / mcp / psycopg) | sidecar 起不来 | Spike 阶段验证打包产物能跑;最坏退路是嵌入完整 Python venv 而非 PyInstaller 冻结 |
| **安装包体积失控** | 分发体验差 | 已由 13.4 降到 200–300MB 量级;体积门(< 450MB)写进 CI;排除未用 provider SDK |
| **SQL 向量后端在大知识库下退化** | 桌面 RAG 检索变慢 | 全表余弦扫描的固有代价(13.4);万级 chunk 内可接受;超出则评估 sqlite-vec 之类嵌入式索引 |
| **冷启动过慢**(Python 重依赖 import) | 桌面体感崩塌 | 依赖裁剪后风险已降;剩余手段见 7.2 第 2 条;若仍超 5s,考虑 sidecar 常驻托盘 |
| **macOS e2e 无自动化通道** | 三平台质量不等价 | 8.1 已明示缺口;以人工清单代偿,不假装等价 |
| **UI 重构砸掉 axe / 截图基线** | C5 成果回退 | 分区施策(第 6 节);门面 surface 单独截图基线;工作面视觉构成不动;每批跑 axe |
| **画布性能回退** | C6-2 成果回退 | C9-4c 强制跑 `test:perf-scale`;禁止任何新增每帧计算(C6-2 教训) |
| **未签名包被系统拦截** | 用户装不上 | 9.2 已明示;文档给绕过步骤;证书申请列为后续独立事项 |
| **孤儿 uvicorn 进程** | 用户机器上残留后端占端口 | Windows Job Object 绑定;退出路径单测;验收含进程表检查 |
| **桌面与 Web 两形态代码分叉** | 维护成本翻倍 | 前端零改动是硬约束(方案 B 的核心价值);桌面专属代码限定在 `src-tauri/` 与 `features/terminal/`;桌面能力经能力探测降级,不 fork 组件 |

---

## 12. 文档同步

本阶段完成时需回写:

- `docs/plan.md`:第 11 节阶段表加入 C9 行;第 2 节架构图补桌面拓扑;第 3 节关键技术决策表加入「桌面分发形态」「PTY 归属」两条。
- `docs/commercial-roadmap-2026-08-11.md`:第 5 节 Backlog 的「桌面端打包」条目标记为已升格为 C9 阶段并给出新估算;第 3 节里程碑总览加 M12。
- `design-system/MASTER.md`:**增补**「Surface Tiers」章节界定门面/工作面边界与各自的动效预算;增补动效 token 体系;增补桌面窗口材质规则。现有条款不删改。
- `docs/adr/0003-desktop-runtime-and-pty-ownership.md`:新建 ADR 记录三项架构决策(3.2 的协议代理、3.3 的嵌入运行时、5.5 的 PTY 归属 Rust)及其被否方案。
- `docs/operations-runbook.md`:新增桌面版故障排查(启动失败诊断、日志路径、数据目录、重置流程)。
- `README.md`:新增桌面版安装与使用段落,明示未签名包的系统提示处理。
- `CHANGELOG.md`:按现有格式记录。

**附带改动(已在审计中发现,独立于 C9)**:CI 增加「`CURRENT_REVISION` 必须等于 alembic head」的断言。见 13.6——上一个提交正因为缺这道门,静默地让 `/readyz`、scheduler、event relay 三处同时失效。

---

## 13. 后端审计结论(已闭环,替代原待确认清单)

开工前的 5 项待确认事项已实测闭环 4 项。结论直接改写了第 3、5、7 节的若干假设,**总体是好消息:后端改动面比原估计小得多**。

### 13.1 数据目录:零改动

`app/core/config.py` 已有 `APP_DATA_DIR` 环境变量,且所有派生路径都由它计算:

| 派生项 | 来源 |
|---|---|
| SQLite 主库 | `data_dir / "app.db"` |
| LangGraph checkpoints | `data_dir / "checkpoints.db"` |
| 向量库 | `data_dir / "chroma"` |
| 上传 / 工作区 | `data_dir / "uploads"`、`data_dir / "workspace"` |
| 本地加密密钥 | `data_dir / ".agentcanvas.key"` |

**结论**:第 3.3 节的数据目录方案退化为「sidecar 启动时注入一个环境变量」,不需要任何后端改造。Rust 侧传 `APP_DATA_DIR=%APPDATA%\AgentCanvas`(macOS/Linux 用对应 XDG 路径)即可。

### 13.2 就绪探针:已存在,零改动

后端已有三个探针且语义已分层,均免认证、免限流(`ratelimit.py`、`request_policy.py` 的 `_EXEMPT_PATHS`):

- `/healthz`、`/livez` — 存活
- `/readyz` — 就绪,逐项检查 database / migrations / checkpointer / config / vector_store / sandbox,任一 unavailable 返回 503

**结论**:第 5.3 节「`/healthz` 两级就绪改造」取消。Rust 监工直接轮询 `/readyz`,拿 200 才放行前端,并可把返回体里的 `checks` 逐项展示在启动画面上——这比原计划的体验更好,且不用写后端代码。

### 13.3 Python 版本:嵌入 3.13

`requires-python = ">=3.12"`,但实际 venv 与 CI 跑的是 **CPython 3.13**(`ruff target-version = py312` 只是语法下限)。

**结论**:嵌入 3.13.x,与开发环境一致,避免「本地过、打包炸」的版本漂移。

### 13.4 依赖体积:可砍掉 ~160MB(重要修正)

实测 venv 共 550MB(含 dev 工具)。重量分布:

| 包 | 体积 | 谁引入 |
|---|---|---|
| `chromadb_rust_bindings` | 61 MB | chromadb |
| `kubernetes` | 42 MB | **chromadb 传递依赖** |
| `onnxruntime` | 40 MB | **chromadb 传递依赖** |
| `numpy` + `numpy.libs` | 45 MB | **chromadb 传递依赖** |
| `grpc` | 13 MB | chromadb |
| `tokenizers` | 8 MB | chromadb |

**关键发现:桌面版不需要 chromadb。**

1. 应用的 embedding 与 Chroma 无关。`app/rag/embedder.py` 只有两个 provider:`LocalHashEmbeddingProvider`(纯 Python blake2b 特征哈希,离线、无模型文件)和 `OpenAIEmbeddingProvider`(HTTP)。Chroma 仅作向量存储,向量始终由应用显式传入。
2. 存在功能等价的替代后端。`app/rag/sql_store.py`(288 行,完整实现 upsert/query/query_text/delete/health)通过 `VECTOR_BACKEND=sql` 启用,SQLite 即可跑,已有 `tests/test_vector_store_backend.py` 覆盖。
3. 排除是安全的。已实测:`app/` 下**没有任何模块级 `import chromadb`**——唯一的 import 在 `chroma_store.py:41` 的 `_collection()` 方法内部,惰性执行;`app/` 下**完全没有 numpy 用法**,numpy 纯属 chromadb 传递依赖。

**结论**:桌面版固定 `VECTOR_BACKEND=sql`,PyInstaller 加 `--exclude-module chromadb --exclude-module onnxruntime --exclude-module kubernetes --exclude-module numpy --exclude-module grpc --exclude-module tokenizers`。这同时消灭了第 11 节风险表里「PyInstaller 打包 chromadb ONNX 已知报错」这一条——不打包它,就没有这个风险。

**启动时间实测(2026-08-25,本机)**:这条修正对冷启动的收益比预期大得多。同一份代码、同一个 SQLite 数据目录:

| 配置 | uvicorn 到 `/healthz` 可用 |
|---|---|
| 默认(`VECTOR_BACKEND=auto` → chroma,含启动迁移检查) | ~40 s |
| `VECTOR_BACKEND=sql` + `STARTUP_MIGRATIONS=false` | **8 s** |

约 5 倍提速,主因是不再 import chromadb 那棵依赖树(onnxruntime / grpc / kubernetes / numpy)。这直接支撑 7.3 的「冷启动到可交互 < 5s」门:桌面版由 Rust 侧在安装/升级时一次性跑迁移(而非每次启动检查),再叠加 onedir 冷启动,5s 目标是有余量的,不是拍脑袋定的。

安装包体积目标据此下调:**目标 < 300MB,上限 < 450MB**(原为目标 400 / 上限 600),第 7.3 节的体积门按此收紧。

代价需明示:SQL 向量后端是暴力全表余弦相似度,不是 HNSW 近似索引。单机知识库规模(万级 chunk)可接受;若桌面版后续要支持十万级 chunk,需要重新评估。这个取舍要写进 ADR。

### 13.5 仍待确认(1 项)

桌面版是否保留应用嵌入(embed)功能 → 影响 2.3 最后一行与 `app-embed.spec.ts` 在桌面通道的取舍。这是产品范围问题,非技术问题,留给 C9-1 开工时决定。

### 13.6 顺带修复的 main 分支缺陷

审计中发现并已修复一个与本计划直接相关的现网缺陷:`app/db/migrations.py` 的 `CURRENT_REVISION` 仍指向 `0046_org_deletion`,而上一个提交(`227656a`)已引入 `0047_evaluation_policy` 且它就是 alembic 实际 head。

影响面比表面更宽,因为 `CURRENT_REVISION` 同时把守三处:

- `api/routes/health.py:52` — `/readyz` 迁移检查失配,**任何跑在 head 的部署都返回 503**,K8s 滚动更新无法就绪
- `services/scheduler.py:58` — 版本不符即拒绝启动,**定时工作流静默全停**
- `services/relay.py:36` — 事件中继同样静默不启动

已修:`CURRENT_REVISION` 上调至 `0047_evaluation_policy`,按既有惯例补 `EVALUATION_POLICY_TABLES`(0047 只加列不加表,故为空集)并并入 `CURRENT_TABLES`;同步更新三处钉住旧 head 的测试断言(`test_event_relay.py`、`test_org_deletion.py`、`test_provider_capability_migration.py`)。相关 25 个测试通过。

这条缺陷说明发布流程缺一道校验:**建议在 CI 增加一个断言 `CURRENT_REVISION == alembic heads` 的检查**,否则下一次加迁移还会重犯。此项列入第 12 节文档同步的附带改动。**已落地(2026-09-12)**:`backend/scripts/check_migration_head.py`(纯文件系统比对 `ScriptDirectory.get_heads()` 与 `CURRENT_REVISION`,多头同样拒绝)+ `quality` job 新步骤「Migration head parity」+ `tests/test_migration_head.py` 4 个(真实图谱一致 / 多头 / 陈旧 revision / 匹配),使该失效模式在 CI 与常规测试套件中都会被拦截。

