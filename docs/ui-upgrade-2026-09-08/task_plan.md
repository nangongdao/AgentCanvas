# Task plan: AgentCanvas UI and interaction upgrade

## Goal
Improve the existing AgentCanvas frontend using suitable principles from VoltAgent/awesome-design-md, without changing backend contracts or product permissions.

## Invariants and scope
- Preserve existing workflows, routes, auth/roles, localization, light/dark theme, and canvas performance.
- Inspect runtime source, callers, and tests before editing.
- Keep user-owned untracked design/roadmap/working documents unchanged.
- No dependency upgrades, backend changes, external deployments, or new services without a concrete requirement.
- New copy in both zh/en; keyboard, mobile, loading/error states, and reduced motion remain usable.

## Phases
- [x] Establish repository state and isolated working-document index.
- [x] Inspect shell, overview/workflow entry points, existing tests, and reference designs.
- [x] Choose a coherent design and implement a bounded frontend upgrade.
- [x] Add focused behavior/accessibility regression tests and verify desktop/mobile visuals.
- [x] Run type checks, build/bundle budget, relevant regressions; inspect diff and report limits.

## Errors / limitations
- The GitHub HTML route initially returned `ERR_CONNECTION_CLOSED` in the browser workspace. The raw Linear DESIGN.md route then loaded and was verified; direct retrieval also supplied the Linear and Vercel reference documents.
- The broader regression sample exposed two pre-existing command-palette assertions whose expected last destination no longer matches the `HEAD` destination registry (`platform` for admins and `members` for viewers). Product behavior was left unchanged because this upgrade did not alter palette membership or ordering.
- No full backend suite, dependency audit, or deployment was run; this scope changes frontend presentation and accessibility only.

## Status
Complete. The shell and overview are refactored, focused experience coverage is in place, desktop/mobile visuals were inspected, a Safari-scroll accessibility finding was fixed, and final typecheck/build/core regressions pass.

## Round 2 (2026-09-10)
Complete. Space Grotesk was replaced by Inter (see [DESIGN.md](DESIGN.md) for the rationale),
the typography scale and page/panel primitives were consolidated into `index.css` and
`features/shell/workspace.css`, and the nine routed page titles that each re-declared the
same utility string now share `workspace-page-title`. The canonical, agent-readable system
was published at the repository root as [../../DESIGN.md](../../DESIGN.md). Evidence and
limits are in [verification.md](verification.md).
