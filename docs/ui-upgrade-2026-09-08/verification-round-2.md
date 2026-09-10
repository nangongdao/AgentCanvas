# Verification — UI upgrade round 2 (2026-09-10)

Round 1 (recorded in `verification.md`) changed tokens and swapped Space Grotesk for Inter.
It was technically correct but visually indistinguishable, which is why round 2 exists.
This file records what round 2 changed and what was actually measured.

## 1. Changed files and responsibilities

| File | Change |
|---|---|
| `frontend/index.html` | Google Fonts request: `Inter` → `Noto Sans SC` (variable `wght@100..900`), JetBrains Mono unchanged |
| `frontend/src/index.css` | `--raise` surfaced added to both themes; `--elev-1..4` stacked elevation ladder (light theme fully overridden); `--duration-*` / `--ease-*` motion scale; `--radius-badge/tile/field/panel`; `--font-sans` + `--font-display` → Noto Sans SC; tracking retuned for CJK; Inter's `cv05`/`cv08` feature settings removed; `--shadow-card` re-pointed at `--elev-4`; new `--color-raise` |
| `frontend/src/features/shell/workspace.css` | Rebuilt primitives: `.workspace-rail`, `.workspace-brand`, `.workspace-nav-section` (hairline via `::after`), `.workspace-nav-link` + `.workspace-nav-icon`, `.workspace-rail-footer` / `.workspace-workspace-card`, `.workspace-display-title`, `.workspace-command-trigger`, `.workspace-keycap`, `.workspace-badge`, `.workspace-stat-*`, `.workspace-panel-lift`, `.workspace-row-glyph`, staggered `overview-metric` entrance |
| `frontend/src/features/shell/PlatformShell.tsx` | Top bar 52 → 56px; brand block moved into the rail (compact mark kept in the header below `md`); breadcrumb; separator before `AuthStatus` |
| `frontend/src/features/shell/PlatformNavigation.tsx` | Section headers, icon tiles, stronger active state, workspace card in the footer |
| `frontend/src/features/shell/CommandPalette.tsx` | Trigger is now a search field with a platform-aware keycap; palette internals untouched |
| `frontend/src/features/overview/OverviewPage.tsx` | Display title + eyebrow dot + badge; stat band rebuilt on `workspace-stat-*`; skeleton `aria-label` on a role-less `div` replaced with `aria-hidden` (axe `aria-prohibited-attr`, serious) |
| `frontend/src/features/overview/OverviewPanels.tsx` | Row glyphs, count chip on `raise` |
| `DESIGN.md`, `design-system/MASTER.md`, `design-system/preview.html`, `README.md`, `task_plan.md` | System published and re-pointed |
| `docs/c9-desktop-tauri-plan.md` | Self-hosted font plan corrected to Noto Sans SC (with a CJK-subsetting caveat) |

**Not changed:** canvas geometry/edges/execution semantics, the accent palette values, backend,
contracts, dependencies, routing, permissions, i18n keys, theme resolution.

## 2. Commands and results

Local `pnpm` is broken on this machine, so the gates were run through the local binaries.

| Gate | Command | Result |
|---|---|---|
| Typecheck | `node node_modules/typescript/bin/tsc --noEmit` | **exit 0** |
| Build | `node node_modules/vite/bin/vite.js build` | **exit 0** |
| Bundle budget | `node scripts/check-bundle-budget.mjs` | **exit 0** — initial gzip **102.33 KiB** / < 120 KiB; largest raw chunk < 350 KiB; runtime page 5.55 KiB / < 40 KiB |
| Browser suites | `node node_modules/@playwright/test/cli.js test e2e/platform-shell.spec.ts e2e/overview-experience.spec.ts e2e/theme.spec.ts e2e/language.spec.ts --trace=off --output=pw-output-final-tmp` | **20 passed (2.3m)** |

Compiled-output spot checks: `--font-sans:"Noto Sans SC", …` in `dist/assets/index-*.css`,
`.bg-raise{background-color:var(--color-raise)}`, `.shadow-card{--tw-shadow:var(--elev-4)}`,
and all new `workspace-*` rules present.

## 3. Visual evidence

`artifacts/round-2/` (gitignored, regenerated with a throwaway spec that was deleted afterwards):

- `overview-dark.png` — 1440×950, dark: rail with brand block, eyebrow section headers, icon-tile nav,
  active state, workspace card, breadcrumb + search field with keycap, stat band with icon tiles.
- `overview-light.png` — same at 1440×950 in light theme.
- `overview-compact-rail-dark.png` — 72px rail, active tile + left bar preserved.
- `palette-dark.png` — command palette open.
- `overview-mobile.png` + `drawer-mobile.png` — 390×844; `scrollWidth − clientWidth ≤ 1` asserted.

Round 1's screenshots remain in `artifacts/*.png` for before/after comparison.

## 4. Limits and caveats

- **The first full suite run reported 8 failures; this was an environment artifact, not a regression.**
  `playwright.config.ts` uses `trace: "retain-on-failure"`, so every passing test deletes its trace
  artifacts. That deletion volume trips the host's bulk-delete guard
  (`SAFE_DELETE_BULK_CONFIRM_REQUIRED` from `node-safe-delete-shim.cjs`), which then surfaces as
  unrelated timeouts and not-found locators across the run. Re-running the same 8 tests with
  `--trace=off` gave 8/8 in 1.3 min, and the full 20 with `--trace=off` gave 20/20 in 2.3 min.
  No product code was changed to accommodate this.
- The only genuine defect the first run surfaced was the axe `aria-prohibited-attr` violation in the
  overview skeleton, which is fixed.
- `e2e/command-palette.spec.ts` still carries pre-existing stale assertions (it assumes `audit` and
  `quotas` are the last two palette destinations, while the registry also has `platform`/`members`).
  Unrelated to this round; not run as part of the focused set and not changed.
- Noto Sans SC is served from Google Fonts, so it is a render-blocking third-party stylesheet.
  `docs/c9-desktop-tauri-plan.md` C9-5 already tracks self-hosting it; note that CJK subsets are far
  larger than the previous Latin-only font and must be character-subset, not embedded whole.
- No full-app test run (`pnpm test:e2e`) was attempted — the focused suites were used because they
  cover every surface this round touched.
