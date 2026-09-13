# Verification — UI upgrade round 3 (2026-09-10)

Round 3 is the dark-theme material pass, requested as "黑夜模式配色太普通、没有质感".
Rounds 1–2 are in `verification.md` and `verification-round-2.md`. Round 2 changed the dark
*palette values*; round 3 changes the dark *material* — what the surfaces are made of.

## 1. What actually changed

| File | Change |
|---|---|
| `frontend/src/index.css` | Dark `:root` rewritten: chromatic surface ladder (`void #060810` / `ink #0b0e1c` / `raise #161c30`), `line #1e2440` (+~40% luminance so structure is visible), new `--line-soft` / `--line-strong` / `--ghost #909ab2` / `--ice #eaeefb`; elevation ladder retinted from `rgba(0,0,0,…)` to `rgba(2,3,10,…)`; new material tokens `--chrome-bg` / `--chrome-blur` / `--sheen` / `--inset-well` / `--stage-layers` (+ the three 1:1 companion lists); `.platform-shell` backdrop rule; `.workspace-chrome`; `@utility field-input` gets the well shadow |
| `frontend/src/features/shell/workspace.css` | Rail is now chrome glass; panels / cards / stat tiles / row glyphs / badges / keycaps take `--sheen` (and `--elev-1` where they are physical chips); command trigger becomes a recessed well; brand mark emits light; nav current-state becomes a directional gradient + bloom; cost bars become lit columns. Every one of those is scoped to `:root:not([data-theme='light'])` |
| `frontend/src/features/shell/PlatformShell.tsx` | Header and mobile drawer moved from `bg-ink` to `workspace-chrome` |
| 12 routed page roots (`OverviewPage`, `AuditLogs`, `Chat`, `CostGovernance`, `Evaluations`, `Knowledge`, `McpCatalog`, `Members`, `Models`, `ProjectQuotas`, `PlatformAdmin`, `Apps`) | `bg-void` removed so the shell backdrop is continuous behind them. Safe by construction: `body` carries `background: var(--bg-void)`, so a page that still paints its own `bg-void` degrades to exactly today's look |
| `frontend/e2e/overview-experience.spec.ts` | New assertion (both themes) that the unpriced-day bar keeps its `repeating-linear-gradient` hatch and a priced bar does not |
| `DESIGN.md`, `design-system/preview.html`, `design-system/MASTER.md`, `README.md` | New palette, material-layer table, and two new Don'ts; preview page mirrors the new tokens and gained a live "材质层" swatch row |

**Not changed:** accent colour values (shared with canvas node semantics), canvas geometry/edges/
execution semantics, light theme (neutralised rather than restyled), backend, contracts,
dependencies, routing, permissions, i18n keys, shell geometry, any `data-testid` / aria contract.

## 2. Commands and results

| Gate | Command | Result |
|---|---|---|
| Typecheck (src) | `node node_modules/typescript/bin/tsc --noEmit` | **exit 0** |
| Typecheck (e2e) | `node node_modules/typescript/bin/tsc --noEmit -p tsconfig.e2e.json` | **exit 0** |
| Build | `node node_modules/vite/bin/vite.js build` | **exit 0** |
| Bundle budget | `node scripts/check-bundle-budget.mjs` | **exit 0** — initial gzip **102.34 KiB** / < 120 KiB (was 102.33 before this round); largest raw chunk < 350 KiB; runtime page 5.55 KiB / < 40 KiB |
| Browser suites | `node node_modules/@playwright/test/cli.js test e2e/overview-experience.spec.ts e2e/platform-shell.spec.ts e2e/theme.spec.ts e2e/language.spec.ts --trace=off --output=pw-out-reg --workers=1` | **20 passed (2.5m)** |

Compiled-output spot checks: `.platform-shell{background-color:var(--void);background-image:var(--stage-layers);…}`,
`.workspace-chrome{background:var(--chrome-bg);-webkit-backdrop-filter:var(--chrome-blur);backdrop-filter:var(--chrome-blur)}`,
`--ink:#0b0e1c`, `--line:#1e2440`, `--sheen:linear-gradient(180deg,#ffffff0e,#fff0 54%)`,
and the hatch rule still intact at `.overview-chart-bar[data-known=false]`.

## 3. Visual evidence

`artifacts/round-3/` (gitignored, regenerated with a throwaway spec that was deleted afterwards):

- `overview-dark.png` — 1440×1000 dark, full workspace.
- `crop-header-dark.png` / `crop-rail-dark.png` — chrome and rail at 1:1, to judge the glass and hairlines.
- `crop-metrics-dark.png` / `crop-panel-dark.png` — stat band and cost panel at 1:1, for the sheen and bar gradients.
- `overview-light.png` — same page in light theme; must be visually unchanged.
- `models-dark.png` — a dense table page, to check the aurora against panels rather than against empty space.
- `palette-dark.png`, `overview-mobile.png`, `drawer-mobile.png` — overlay, 390px, and the glass drawer.

Round 2's screenshots remain in `artifacts/round-2/`, and round 1's in `artifacts/*.png`.

## 4. Two non-obvious defects found and fixed in this round

Both were found by measuring, not by looking — the first two rounds' mistake was trusting that a
token change had produced a visible result.

1. **The grain layer was silently doing nothing.** `feTurbulence` emits noise in the *alpha channel*
   too, so with `background-blend-mode: overlay` the layer was mostly transparent and had almost
   nothing to blend: a flat region of the shell measured a neighbour-pixel delta of **0.34**, i.e.
   below the dithering floor. Replacing `feColorMatrix type='saturate'` with an explicit luminance
   matrix whose alpha row is `0 0 0 0 1` forces the noise opaque; the same region now measures
   **1.03**. Measured with `pngjs`-free PNG decoding — inflate, unfilter, then mean |Δ| between
   horizontally adjacent pixels, over a flat patch.
2. **Hand-writing `-webkit-backdrop-filter` next to `backdrop-filter` removes the effect in
   Firefox.** Lightning CSS treats the two as one property, dedupes to the last declaration, and
   then does not re-derive the unprefixed form — the built rule kept *only* the `-webkit-` one.
   Writing just the unprefixed property makes the build emit both (verified in `dist`). This was
   caught by grepping the compiled CSS, not the source.
3. (Caught by review, confirmed by test.) Making the cost bar a gradient added
   `:root:not([data-theme='light']) .overview-chart-bar` at specificity `(0,3,0)`, which silently
   outranked the existing `.overview-chart-bar[data-known='false']` hatch at `(0,2,0)`. Unpriced
   days would have rendered as ordinary priced bars — the one visual cue distinguishing a
   placeholder height from a measurement. Fixed with `:not([data-known='false'])` and locked with
   the new assertion in `overview-experience.spec.ts`.

## 5. Limits and caveats

- **Light theme is deliberately, verifiably flat.** Every material token resolves to `none` /
  neutral under `[data-theme='light']`, and every added effect is scoped behind
  `:root:not([data-theme='light'])`. The light screenshots are for confirming *absence* of change,
  not a design review of light mode.
- The `theme.spec.ts` axe pass runs in light mode; the dark palette was checked by hand against
  WCAG AA instead (ghost on ink ≈ 6.8:1, up from 6.3:1 before this round). There is no automated
  dark-mode contrast gate.
- The grain is calibrated to be *felt, not seen* (σ ≈ 0.7 levels of 255). Its job is dithering the
  large radial gradients; on a low-gamut panel it may be indistinguishable from banding.
- `pnpm` / `pnpm build` still cannot be verified on this machine (corepack is broken); all gates
  ran through the local binaries as above.
- `e2e/command-palette.spec.ts` still carries pre-existing stale assertions about the palette's
  destination order. Unrelated to this round; untouched.
