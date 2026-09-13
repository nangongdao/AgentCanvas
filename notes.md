# Notes: Local Development Roadmap

## Sources

### 商用路线图
- `docs/commercial-roadmap-2026-08-11.md` 显示 C0-C4 已完成，C5 当前进行中。
- C5-3 Undo/Redo 已有路线图验收记录；下一项是 C5-4 自动布局与对齐。
- C5-4 要求 dagre/elk 分层布局、拖拽吸附参考线、多选对齐和等距分布。

### 当前工作区
- 已加入 `@dagrejs/dagre`、`zundo`，并存在 `canvasLayout.ts`、`CanvasLayoutMenu.tsx`、C5-4/C5-3 E2E。
- `pnpm run typecheck`、`typecheck:e2e`、`build` 和 bundle budget 均通过；初始 gzip 约 93.29 KiB，小于 120 KiB。
- C5-4 针对性 E2E 2/2 通过（自动布局/多选命令/吸附参考线 + 零偏移对齐专项）。

### 根因与修复
- `snapNodeChanges` 原以 `dx === 0 && dy === 0` 判定“没有吸附”。当 `xSnap` 或 `ySnap` 存在但偏移量恰为零时，该判断丢弃了参考线。
- 修复：改为 `!xSnap && !ySnap` 判定——只要找到吸附候选（含 delta 恰为 0 的精确对齐）就渲染参考线。零 delta 表示已对齐目标，而非无目标。
- 同步加固：`positiveDimension` 防护非正/非有限宽高回退默认值；`MIN_DISTRIBUTION_GAP = 24` 防止等距分布在紧凑跨度下产出不可拖拽的重叠行。

## Synthesized Findings

C5-4 自动布局与对齐已完成并通过本地门禁（2026-08-20）。
- 实现：dagre 分层自动布局（LR，marginx/y 80、nodesep 44、ranksep 120）、6 种多选对齐、2 种等距分布、拖拽吸附参考线（SNAP_THRESHOLD=10，left/right/center-x 与 top/bottom/center-y 候选）。
- 前端证据：source/E2E TypeScript、contracts:check、生产构建（初始 gzip 93.29 KiB < 120 KiB）、e2e-config 单测、moderate 依赖审计通过；C5-4 针对性 Playwright 2/2 通过。
- 后端证据（不回归）：Ruff、mypy 270 文件、uv lock --check 通过；chromadb 1.5.9 PYSEC-2026-311 为预存漏洞（pyproject/uv.lock 自 C1-C4 无变更），与本切片无关。
- 下一项：C5-5 分组与注释。

### C5-5 分组与注释（2026-08-20，完成）
- 工作树已有 C5-5 进行中改动：`CanvasGroupFrame`/`CanvasNote`/`CanvasObjectActions` 组件、`canvasLayout` 对齐逻辑、store 的 groups/notes CRUD + `moveCanvasGroup`（成员整体平移）+ 历史分组、`WorkflowDSL.canvas` 字段、后端 `CanvasGroup`/`CanvasNote`/`CanvasMetadata` schema、版本 diff `canvas_changed`。
- 回归修复 1（plugin-sdk）：C5-5 改 `onSelectionChange` 后，`addNode` 未给新节点设 `selected:true`，React Flow 受控 selection 与 store 不同步，空 onSelectionChange 覆盖 `selectedNodeId` 使 ConfigPanel 失焦。修复：addNode 给新节点 `selected:true` 并清其他节点 selected。HEAD baseline 确认 plugin-sdk 在 C5-5 前通过、C5-5 后失败、修复后恢复。
- 回归修复 2（canvas-objects）：`onlyRenderVisibleElements` 在 fitView 完成前用默认视口卸载 end 节点（产品 bug——加载工作流丢失视口外节点）。改为**条件虚拟化**：`VIRTUALIZATION_NODE_THRESHOLD=50`，仅节点数 > 50 时启用，小规模全量渲染避免 fitView 时序丢节点；大规模保留虚拟化保性能。
- 版本 diff 断言：`canvas objects changed` 在合并段落 `<p>` 内，`getByText(exact)` 无法匹配，改为 `getByText(/canvas objects changed/)`。
- 契约兼容性：`WorkflowDiffOut.canvas_changed` 初版为 required bool，触发破坏性变更检查（新增 required 属性）；修复为 `bool = False` 可选字段，旧客户端不受影响。
- 测试断言：`test_workflow_versions.py` 的 `changed_edges == ["e1"]` 与实际 `workflow_body()` 边 id `"edge"` 不符；修正为 `["edge"]`。
- **性能回归修复**：C5-5 移除虚拟化 + `visibleNodes` 每帧 `.map` 重建 100 节点对象，破坏 React Flow 节点 memoization，dragPaintP95 从 HEAD 基线 14.8ms 退化到 105-129ms（超 100ms 阈值）。根因经 HEAD worktree 基线对比定位（基线 14.8ms vs 工作树 129.3ms）。修复：(1) `visibleNodes` 无折叠组时直接返回 `nodes`，避免每帧重建；(2) `SNAP_GUIDE_NODE_LIMIT=60`，大规模画布跳过 snap-guide 的 O(n) 每帧计算；(3) snap-guide 的 `Math.min/max(...spread)` 改为 `reduce`。修复后 dragPaintP95 69.9-77.4ms（两次稳定 < 100ms）。
- **验收完成**：后端 820 passed / 20 skipped、应用行 82.90%、关键分支 83.92%、Ruff/mypy 270 文件/lock/契约 check；前端 source/E2E TypeScript、build（gzip 93.30 KiB < 120 KiB）、moderate audit、e2e-config 单测、默认 Playwright 58/58（含性能）、canvas-objects/canvas-layout 针对性通过。下一项 C5-6 子图复制粘贴。

### C5-6 子图复制粘贴（2026-08-21，完成）
- 工作树已有 C5-6 进行中改动：store 的 `copySelection`/`pasteSubgraph`、`SubgraphClipboard` 类型、sessionStorage 剪贴板持久化、`ClipboardActions` 工具栏、Ctrl/Cmd+C/V 快捷键、后端 `validate_dsl(strict=False)` 草稿保存路径。
- 收尾清理：`clipboard.spec.ts` 残留调试哨兵 `probeLogs: ["!show-actual-probeLogs!"]`（故意永不匹配以打印探针日志）与未使用的 `connectEdge` 辅助函数——前端源码本无 `[C56` 探针输出，断言恒失败。移除后改为干净断言：poll 后端快照验证新 ID 无碰撞 + 内部连线重建，并显式断言内部边存在（`toMatchObject` 对 undefined 字段会静默通过）。
- 粘贴语义确认：深拷贝 data 防嵌套 config 引用泄漏；idMap 重接内部边；plugin 类型经 `canvasNodeType` 归一化；单步 undo 含重建边一起回滚；剪贴板跨工作流/刷新存活但不入历史。
- strict=False 边界确认：仅草稿保存路径（create/update/template/import）跳过可达性与静态环检查；compile/publish/run 保持 strict=True，不可运行图无法执行。
- **验收完成**：clipboard e2e 3/3；后端全量 820 passed / 20 skipped、Ruff、mypy 270 文件、uv lock --check、pip-audit（仅预存 chromadb PYSEC-2026-311）；前端 typecheck ×2、contracts generate/check、build（gzip 93.30 KiB < 120 KiB）、moderate audit、e2e-config 单测；默认 Playwright 全量 61/61。下一项 C5-7 节点 UX 细节。

---

# 2026-09-13 — AgentCanvas planning evidence

Goal: 审查 docs/agentcanvas-future-roadmap.md 的事实、优先级、范围、依赖、排期与验收，并给出修订建议；不覆盖原方案。
Write scope: new planning/review documents, root task_plan.md/notes.md appended entries, isolated records under docs/planning-review-2026-09-13. Existing source/test/config edits remain untouched.
Evidence classification: Observed = current file/command; Derived = inference/recommendation; Assumed = resource/target; Not verified = current scope did not prove it. No cross-project capability assumptions.

## Current-source review findings (2026-09-13)
- Main reviewed the primary future draft, desktop canonical plan, manifests and CI. The future draft's C9 numbering/remaining work differs from docs/c9-desktop-tauri-plan.md:184 (sidecar, origin transport, NSIS/CI, fonts already implemented; clean-machine acceptance still pending). Do not equate implementation with signed distributable acceptance.
- Backend read-only review: VectorStore abstraction/SQL adapter/migration services already exist; SQLite SQL vectors are not pgvector. AND-join, iteration compile reuse, durable execution queue, connection pooling and provider cache/failover are already present. Avoid duplicate architecture work.
- Offline main diagnostic using current functions: response required-field removal and response enum extension both pass check_openapi_compatibility while a concrete new response fails the old JSON schema; HTTP POST node returns no side-effect reason from shared node_side_effect_reason. No HTTP calls or full replays were made. Receipt verification/contract-probes.log. These are contract/classifier findings, not observed production incidents.
- SSE relay can replay around publication/cursor commit; existing seq dedup/heartbeat/replay should not be sold as universal exactly-once processing or rebuilt from scratch. Priority reordering must not make terminal events overtake earlier events.
- MCP live clients are owner-task/worker bound. Generic live-session migration and public catalog installation need explicit process/environment/credential/in-flight semantics; catalog approval is not OS sandboxing.
- Draft issues to address: unsupported freshness/market/cost claims, inconsistent P1/P2/P3 labels, CLI/SDK/market scope ahead of prerequisites, ungrounded backend/src paths, SQLite p95 used to justify PG/cache changes, E2EE mixed with TLS/at-rest encryption, speculative future major versions.
- Tested: 8 selected backend files, 68 passed in 104.44s, no skips shown; frontend typecheck and generated contracts checks exit 0. These are targeted checks, not full suite or clean-machine desktop/browser/performance verification.

## Independent draft review refinement
- Reviewer identified two must-correct issues in the audit draft: it overstated the original proposal as proposing new pooling/AND-join/capability matrix even where it says tuning/already existing/extension; corrected the comparison rows to quote the actual proposal and describe incremental gaps, not a strawman.
- Solo capacity had implied both a new entry and a scenario despite upper-bound estimates exceeding 32-35 planned days. Replaced with explicit AC-T01-T04 + AC-T07 (upper-bound 33 effective person-days); CLI/SDK/templates now require remaining capacity and preserve buffer or move later.
- Genuine vector-abstraction/iteration-repeat premise errors and offline contract/effect findings remain; no business code or original roadmap changed.

## Completion audit — 2026-09-13
- Deliverable: docs/future-roadmap-review-2026-09-13.md. Requirements covered by the actual sections/card tables: current evidence, development/optimization direction, scope/priority, dependencies, effort assumptions, phased capacity, acceptance, risks and concrete next backlog.
- Final structure/path/receipt validation: 45 source-path references and 6 Markdown links checked; no errors. Document SHA-256: f3676719a7fadf5abaec767984a64083c48da7f86c7fbee7ada56f7775d98ac9.
- Initial worktree status entries preserved: 18; new status entries are only this task's new report and planning-record directory. Status preservation is not represented as a pre/post byte-hash proof of all existing files; tool write scope was limited to planning/evidence records.
- Independent reviews: AgentCanvas reviewer required two corrections (fair paraphrase of existing/tuning capabilities and solo upper-bound capacity); both integrated and main rechecked. Helix reviewer found no must-fix items within the established source/probe/capacity scope.
- Tests/checks are scoped and original receipts retained. Invocation errors (empty Redis URL, noncanonical Ruff entry) were diagnosed and rerun without changing tests or application code; no extra production/model/browser guarantee inferred.
- All delegated agents and command sessions used for this task are terminal/closed. No persistent service was launched. Synthetic runtime data is confined to the documented directory and ignored from Git.
- Requested report/roadmap work is complete; no business feature, original roadmap overwrite, dependency update or commit was performed.
