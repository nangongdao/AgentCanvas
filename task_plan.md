# Task Plan: AgentCanvas UI upgrade — round 3

## Goal

Three rounds, each fixing what the previous one got wrong:

- **Round 1** — token tweaks + Space Grotesk → Inter. Technically correct, visually imperceptible.
- **Round 2** — made the redesign *visible*: Noto Sans SC, a rebuilt surface/elevation language, a
  restructured shell (rail + top bar), new navigation and stat components, a published design system.
- **Round 3** (this round) — made the dark theme *have body*. Round 2 changed the dark palette's
  values; round 3 changes what the surfaces are made of: a chromatic ladder, an aurora + grain
  backdrop, glass chrome, panel sheen, recessed wells and environment-tinted shadows.

## Phases

- [x] Phase 1: Re-read the round-1 diff and audit why it was imperceptible
- [x] Phase 2: Study four awesome-design-md entries (voltagent / vercel / raycast / linear.app) and extract concrete tokens
- [x] Phase 3: Rebuild tokens — Noto Sans SC, 3-step surface ladder, radius scale, stacked elevation ladder, one motion scale
- [x] Phase 4: Restructure the shell — brand into the rail, eyebrow section headers, icon-tile navigation, 56px top bar, search-field command trigger with keycap
- [x] Phase 5: Upgrade the overview workspace — display title, badge, icon-tile stat band, row glyphs
- [x] Phase 6: Publish the system — root `DESIGN.md`, `design-system/preview.html`, doc corrections
- [x] Phase 7: Verify — typecheck, build, bundle budget, focused browser suites, desktop/light/dark/mobile screenshots
- [x] Phase 8 (round 3): Rebuild the dark palette as a chromatic ladder with a visible `line`
- [x] Phase 9 (round 3): Add the material layer — `.platform-shell` aurora + grain, `workspace-chrome` glass, `--sheen`, `--inset-well`, tinted shadows
- [x] Phase 10 (round 3): Make the shell backdrop continuous by dropping `bg-void` from routed page roots
- [x] Phase 11 (round 3): Verify — src + e2e typecheck, build, budget, 20 focused browser tests, measured grain, 1:1 crops

## Scope decisions

- Preserve dark/light theming, i18n, routing and every accessibility contract.
- **Locked by tests, do not change**: top bar / rail geometry (`aside` exactly 224px expanded and 72px compact), `aria-label="主导航"`, `aria-current="page"`, `data-testid="desktop-navigation"` / `workspace-destination`, the `[data-metric]` nesting depth used by `locator('../..')`, `.overview-panel` (reduced-motion assertion), `.overview-chart-bar[data-known]`, and the drawer's close button staying the first focusable element.
- Canvas geometry, colours and motion stay untouched; no backend, contract or dependency changes.
- **Light theme is deliberately left flat**: every material token resolves to `none` / neutral under `[data-theme='light']`, and every added effect is scoped behind `:root:not([data-theme='light'])`.
- Accent colour values are frozen — they are shared with canvas node semantics and with light mode's darker AA-compliant variants.

## Typography decision (supersedes round 1)

Space Grotesk → **Inter** (round 1) → **Noto Sans SC** (round 2, user-directed).

`Noto Sans SC` is a variable font (weight 100–900) that ships both the Simplified-Chinese and
Latin glyph sets, so `--font-sans` can be a single family with no script-specific fallback
stack — the right answer for a bilingual console. Registered as both `--font-sans` and
`--font-display`, so Tailwind's preflight default follows it too. Inter's `cv05`/`cv08`
feature settings were removed (they are Inter-specific). Tracking was retuned for CJK:
display `-0.018em`, title `-0.006em`, labels `+0.08em` — CJK is far more sensitive to negative
tracking than Latin.

## What changed visually

**Round 2**

1. **Rail**: brand block moved into the sidebar; mono uppercase section headers that draw their
   own hairline; 26px icon tiles; stronger active state (tint + border + left bar + tinted tile);
   a workspace card in the rail footer.
2. **Top bar**: 56px; breadcrumb (`工作空间 › 目的地`); vertical divider; the command trigger is
   now a search field with a `Ctrl K` / `⌘K` keycap.
3. **Overview**: 30px display title, pulse-dot eyebrow, badge; the flat metric band became stat
   cells with 28px icon tiles, mono 26px tabular values and staggered entrance.
4. **Depth**: a real 4-step surface ladder (`void → ink → raise`) plus a stacked elevation ladder
   (`--elev-1..4`) instead of ad-hoc shadows.

**Round 3 (dark only)**

1. **A chromatic ladder instead of a grey ramp.** Neutral grey is what makes a dark console read as
   "yet another dark admin theme": the eye gets lightness but no material. `void` (~235°), `ink`
   (~232°) and `raise` (~228°) now drift in hue, so the stack reads as one lit surface — and the
   cyan brand accent (~187°) becomes a true complement. `line` was lifted ~40% in luminance,
   because a dark UI whose borders are invisible has no structure for depth to act on.
2. **One composited backdrop for the whole shell**: three out-of-frame lights plus fine grain
   (`--stage-layers`), blended so the grain modulates the lights instead of washing out near-black.
   Routed pages no longer paint their own `bg-void`, so the backdrop and the blurred chrome above it
   stay continuous across the seam.
3. **Chrome became glass**: top bar, rail and mobile drawer blur the backdrop instead of covering it
   with an opaque fill.
4. **Surfaces got bodies**: a top-down sheen on panels, cards, stat tiles and glyphs; the command
   trigger and form fields became recessed wells (`--inset-well`); shadows are tinted with the void
   hue rather than pure black; cost bars became lit columns.
5. **Light theme untouched**, by construction rather than by review.

## Evidence

- Canonical system: [`DESIGN.md`](DESIGN.md) (root, awesome-design-md / Stitch 9-section format).
- Visual counterpart: [`design-system/preview.html`](design-system/preview.html) — palette, a live
  material-layer row, type scale, controls, navigation, data surfaces, elevation ladder.
- Round 1–3 records: [`docs/ui-upgrade-2026-09-08/`](docs/ui-upgrade-2026-09-08/)
  (`verification.md`, `verification-round-2.md`, `verification-round-3.md`).
- Screenshots (gitignored, regenerated per round): `docs/ui-upgrade-2026-09-08/artifacts/round-3/`.

## Status

**Implemented and verified.** src + e2e typecheck clean, production build clean, bundle gzip
**102.34 KiB** (budget < 120 KiB), focused browser suites **20/20** in 2.5 min. Round 3 also fixed
three defects found by measuring the output rather than trusting it — a grain layer that was
silently inert, a `-webkit-backdrop-filter` build trap, and a chart-hatch specificity regression
that the round added and then locked with a new test. See `verification-round-3.md` for the exact
pass/fail record and environment caveats (broken local `pnpm`, the bulk-delete guard tripping
Playwright's trace cleanup).

---

# Active planning task — 2026-09-13: AgentCanvas

## Goal
审查 docs/agentcanvas-future-roadmap.md 的事实、优先级、范围、依赖、排期与验收，并给出修订建议；不覆盖原方案。

## Phases
- [x] X1: 读取当前仓库状态、规则与项目入口；不沿用其他项目的架构假设。
- [x] X2: 只读核对方案/产品流程、实现、前端与交付；主代理与独立代理分工。
- [x] X3: 执行适度本地验证，保留真实结果与未验证范围。
- [x] X4: 编写详细审查/路线图，含优先级、边界、依赖、工作量假设、验收与风险。
- [x] X5: 核对引用/证据/覆盖范围、审稿、更新索引并交付。

## Decisions
- 仅写规划文档和隔离验证记录，不修改业务源码、已有测试、依赖、原路线图，不提交或清理既有改动。
- Observed / Derived / Assumed / Not verified 分开；不复用其他项目或历史文档的测试/性能结论。
- 主代理负责跨文档综合、API/契约关键路径与验证；并行只读代理只负责界定的独立实现切片。
- 不查看/输出 .env 或凭据，不调用真实模型/生产服务；外部市场、价格、供应商时效事实若未核验则不作为立项证据。
- 证据与索引：docs/planning-review-2026-09-13/；详细发现另追加 notes.md。

## Errors
- 初轮宽输出截断；后续采用窄范围和分段读取，不推断省略内容。

## Status
Complete. Requested planning/review deliverable is written, source/receipt/path/link checked, independently reviewed and indexed. Existing roadmap and business source/test/config edits preserved. Future implementation and explicitly unverified deployment/model/browser work remain future tasks, not missing planning work.
