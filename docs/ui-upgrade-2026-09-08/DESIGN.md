# AgentCanvas UI upgrade — adaptation record

The canonical, agent-readable design system now lives at the repository root:
[`../../DESIGN.md`](../../DESIGN.md). It follows the `awesome-design-md` (Stitch) section
format and is the single document to hand to an agent.

This file records **provenance, scope and the decisions taken in this round**, so the
root document can stay a description of the system rather than a changelog.

## Evidence / provenance
- Reference: VoltAgent/awesome-design-md, revision `8147538b4226ae41e2487a9179e3bcc1f68e8554`, retrieved 2026-09-08.
- Reviewed: `design-md/linear.app/DESIGN.md` and `design-md/vercel/DESIGN.md` (foundations and component guidance).
- Sources: https://github.com/VoltAgent/awesome-design-md/tree/8147538b4226ae41e2487a9179e3bcc1f68e8554/design-md
- The reference documents are design analyses, not verified specifications of the original companies' live products.

## Adaptation, not a clone
Use Linear's layered neutrals, deliberate accent placement, compact navigation, hairline
depth and aggressive display tracking; use Vercel's precise separators, clear hierarchy
and technical numeric typography. Keep AgentCanvas's existing cyan identity, its surface
tokens and its theme implementation. Do not copy marketing heroes, logos, proprietary
fonts, gradients, or external assets.

## Round 2 (2026-09-10) — typography and shared primitives

> **Superseded.** This section records the round-1 font decision. The font was changed again
> on 2026-09-10 (same day, user-directed) to **Noto Sans SC** — see the root
> [`../../DESIGN.md`](../../DESIGN.md) §3 and root `task_plan.md`. Round 1's shared primitives
> (`workspace-page-title`, `workspace-section-title`, the surface/tracking tokens) were kept and
> extended; only the family choice changed.

### Typography replaced
- **Space Grotesk → Inter** as the display and UI family (`index.html`, `@theme`).
  Rationale: Inter is the open substitute the reference systems build on, it
  optical-sizes through its `opsz` axis, ships real tabular numerals, and stays neutral
  at the 11–14px the product actually uses. It also matches the typography already named
  in `design-system/MASTER.md`, removing a long-standing code/doc divergence.
- Registering Inter as `--font-sans` (not only `--font-display`) redirects Tailwind's
  preflight default, so un-styled text no longer falls back to the system stack.
- `JetBrains Mono` is unchanged and still carries all telemetry, identifiers and code.
- Character variants `cv05`/`cv08` are enabled globally so identifier-heavy copy
  (`exec_1d3f`, `API_Key`) stays unambiguous at small sizes; monospace contexts reset them.

### Typography scale introduced
- `--tracking-display` (-0.022em), `--tracking-title` (-0.011em, now the global default
  for every `.font-display` heading), `--tracking-label` (+0.07em).
- Role classes `type-display` / `type-title` / `type-label` / `type-data` plus the shared
  chrome classes `workspace-page-title` / `workspace-section-title`.
- The nine routed page titles that each re-declared
  `font-display text-sm font-semibold text-ice sm:text-base` now share
  `workspace-page-title`, so the family, tracking and responsive size have one source.
- `text-wrap: balance` on headings.

### Interaction and depth refinements
- Shared panel primitive `workspace-panel` (`overview-panel` kept as an alias); dark
  surfaces get a `inset 0 1px` hairline top highlight instead of a heavier drop shadow.
- `--shadow-card` softened to `0 10px 26px -14px rgba(0,0,0,.65)` + inset highlight.
- Press feedback (`translateY(1px)`) on icon and primary buttons; a distinct `:active`
  background on navigation links; all of it disabled under `prefers-reduced-motion`.
- `.brand-title` now uses the display tracking token instead of `letter-spacing: 0`.

### Explicitly out of scope
- Canvas geometry, edge animation, node glow and execution semantics are untouched.
- No backend, contract, dependency, permission, routing or i18n-key changes.
- The dark/light palette values are unchanged. *(Superseded: round 3 rewrote the dark palette — see below.)*

## Round 3 (2026-09-10) — the dark material pass

Requested as "黑夜模式配色太普通、没有质感". Round 2 changed the dark palette's *values*; round 3
changes what the surfaces are *made of*. Everything here is dark-only.

- **Chromatic ladder.** `void #060810` (~235°), `ink #0b0e1c` (~232°), `raise #161c30` (~228°) drift
  in hue instead of stepping a grey ramp, so the stack reads as one lit surface and the cyan accent
  (~187°) lands as a complement. `line` went `#151b2b → #1e2440` (+~40% luminance); a dark UI whose
  borders are invisible has no structure for depth to act on. `ghost #909ab2`, `ice #eaeefb`.
- **Aurora + grain backdrop.** One composited layer on `.platform-shell` (`--stage-layers`): three
  out-of-frame lights plus fine grain, blended so 50% grey is neutral and the grain modulates the
  lights rather than washing out near-black. Routed page roots dropped `bg-void` so the backdrop is
  continuous and the glass chrome has something to sample. Safe by construction — `body` still
  carries `background: var(--bg-void)`.
- **Glass chrome.** `--chrome-bg` + `--chrome-blur` on the header, rail and mobile drawer.
- **Surfaces with bodies.** `--sheen` (top-down 5.5% light) on panels, cards, stat tiles, row glyphs,
  badges and keycaps; `--inset-well` recesses the command trigger and `field-input`; the elevation
  ladder was retinted from `rgba(0,0,0,…)` to `rgba(2,3,10,…)`, because a neutral black shadow on a
  blue-violet floor reads as a grey halo; cost bars became lit columns (bright top, fading down).
- **Light theme neutralised, not restyled.** `--sheen`, `--stage-layers` and the grain resolve to
  `none` under `[data-theme='light']`, and every added effect is scoped behind
  `:root:not([data-theme='light'])`, so the same stylesheet flattens back to the round-2 light look.

Three defects were found by measuring output rather than trusting it: a grain layer that was
silently inert (`feTurbulence` noise in the alpha channel gave `overlay` nothing to blend),
a Lightning CSS trap where hand-written `-webkit-backdrop-filter` causes the unprefixed property to
be dropped, and a specificity regression the round itself introduced, which silently removed the
unpriced-day hatch from the cost chart. Details in `verification-round-3.md`.

## Implementation rules
- Shell: 56px header; 224px expanded / 72px compact desktop navigation; independently scrollable navigation. Current route identity is derived from the same destination registry as links. Saved workflow routes remain under Workflows.
- Overview: bounded 1440px work surface, readable page intro, a shared metric band, framed cost telemetry, quota column, and direct workflow/knowledge/model links. Avoid nested decorative cards.
- Interaction: 160ms color/border feedback, 120ms transform feedback, brief entrance reveal. No animated layout width or continuous ornament. Respect reduced motion.
- Chart: real API data only; hover, keyboard, and touch expose date/cost/execution values. Unknown values remain explicitly unknown and do not influence the known-cost scale.
- Loading, request errors, empty data, and unpriced data are distinct. Refresh retains previous data while explicitly marked busy. No hardcoded service-health claims.
- All new/changed shell and overview copy is translated in the existing typed zh/en dictionaries. Retain permission filtering; do not introduce backend/API changes.
- Verify desktop, 390px mobile, light/dark, reduced motion, keyboard focus, and serious/critical axe findings. Preserve bundle budget and canvas performance gates.
