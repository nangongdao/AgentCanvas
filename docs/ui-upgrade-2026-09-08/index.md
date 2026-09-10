# UI upgrade working index

> Status: round 3 done (2026-09-10). Round 1's font decision (Inter) was superseded the same day by
> **Noto Sans SC**; round 3 rebuilt the dark theme's *material* (chromatic surface ladder, aurora +
> grain backdrop, glass chrome, sheen, tinted shadows). Light theme was deliberately left flat.
> See `../../DESIGN.md` §2 and §6, and `verification-round-3.md`.

- [`../../DESIGN.md`](../../DESIGN.md): the canonical, agent-readable design system (awesome-design-md / Stitch 9-section format). Read this before changing UI.
- [`../../design-system/preview.html`](../../design-system/preview.html): the visual counterpart — palette, material layers, type scale, controls, navigation, data surfaces and the elevation ladder, with a dark/light toggle.
- `task_plan.md`: scope, invariants, phases, and execution status.
- `notes.md`: repository observations, reference research, and design decisions.
- `verification.md`: changed-file responsibilities, commands, results, and remaining limits (round 1).
- `verification-round-2.md`: Noto Sans SC, surface/elevation rebuild, shell restructure — measured gates and the `--trace=off` environment finding.
- `verification-round-3.md`: the dark material pass — measured gates, the grain/blend-mode defect, the `-webkit-backdrop-filter` build trap, and the chart-hatch specificity regression.
- Existing project direction: `../../design-system/MASTER.md` (user-owned; preserve).
- Existing work: root `task_plan.md`, `notes.md`, `development_progress.md`, and untracked roadmap documents (preserve).
- `DESIGN.md`: source revision, adaptation record, round-by-round decisions, and acceptance criteria.

## Runtime implementation map
- `../../frontend/index.html`: font preconnect + `Noto Sans SC` variable / `JetBrains Mono` stylesheet; pre-paint theme and locale resolution.
- `../../frontend/src/index.css`: surface/accent tokens **and the material tokens** (`--chrome-*`, `--sheen`, `--inset-well`, `--stage-layers` + its three companion lists) for both themes, the `.platform-shell` backdrop rule, `.workspace-chrome`, the `@theme` typography scale (`--font-sans`, `--font-display`, `--font-mono`, tracking tokens), the `type-*` role classes, and the global `.font-display` tracking rule.
- `../../frontend/src/features/shell/workspace.css`: layered workspace-only primitives (`workspace-page-title`, `workspace-section-title`, `workspace-panel`, buttons, nav links, chart, motion) including reduced-motion handling; imported by `index.css`.
- `../../frontend/src/features/shell/PlatformShell.tsx`: shell composition, route identity, responsive modal navigation and focus lifecycle.
- `../../frontend/src/features/shell/PlatformNavigation.tsx`, `navigation.ts`, `useSidebarPreference.ts`: permission-filtered destinations, shared active matching, local compact preference.
- `../../frontend/src/features/overview/useOverviewData.ts`: authoritative project selection, discovery/retry, request generation and unmount cancellation.
- `../../frontend/src/features/overview/OverviewPage.tsx`: translated overview composition and metrics, explicit loading/error states.
- `../../frontend/src/features/overview/OverviewCostChart.tsx`: known/unknown cost scale, daily inspection, keyboard and pointer behavior.
- `../../frontend/src/features/overview/OverviewPanels.tsx`: recent workflows, quota boundaries, and direct workspace shortcuts.
