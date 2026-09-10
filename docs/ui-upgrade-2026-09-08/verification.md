# UI upgrade verification

## Scope and status
Implementation complete. This round replaced the typography system, consolidated shared
page/panel primitives, and refined interaction states. No backend, dependency, permission,
contract or deployment changes; canvas geometry and canvas motion untouched.

## Observed checks (round 2, 2026-09-10)
| Check | Result | Evidence |
| --- | --- | --- |
| Source typecheck | Passed, exit 0 | `node node_modules/typescript/bin/tsc --noEmit` reported no errors after the final edits. |
| Production build | Passed, exit 0 | `node node_modules/vite/bin/vite.js build` completed in 10.4s. |
| Bundle budget | Passed, exit 0 | `initial gzip 102.22 KiB / < 120.00 KiB; largest raw chunk < 350.00 KiB; runtime page 5.55 KiB / < 40.00 KiB` — unchanged from the previous round's 102.22 KiB. |
| Focused browser suites | 20/20 passed, exit 0 (3.4m) | `e2e/platform-shell.spec.ts` (6), `e2e/overview-experience.spec.ts` (11), `e2e/theme.spec.ts` (2), `e2e/language.spec.ts` (1), against a real uvicorn backend + Vite dev server. |
| Typography wiring | Verified | Built CSS contains `--font-sans:"Inter", …`, `html{font-family:var(--default-font-family)}` with `--default-font-family:var(--font-sans)`, plus `type-display`, `workspace-page-title` and `tracking-title`. |
| Visual inspection | Verified | `artifacts/overview-dark.png`, `artifacts/overview-light.png`, `artifacts/overview-mobile.png` captured from deterministic API fixtures at 1440×1100 and 390×844: hierarchy, hairline surfaces, metric band, cost chart and quota waterline render correctly; light theme legible; no document-level horizontal overflow at 390px. |

### Regression coverage that exercises the changed surfaces
The focused suites deliberately cover the behaviour touched this round rather than only
pixel output: light and dark cost inspection (keyboard + touch), unknown-cost handling,
refresh retaining stale data, project-switch race rejection, zero-quota-as-exhausted,
failure vs empty-state separation, compact navigation + role filtering, saved-workflow
route identity, localStorage theme persistence across reload, and `prefers-color-scheme`
live switching. All still pass unchanged.

## Known limits
- The full frontend E2E matrix, the full backend suite, the dependency audit, and any
  deployment were not run; this change is presentational plus one className consolidation.
- `pnpm` and the `node_modules/.bin/vite.cmd` launcher are broken in this Windows
  environment (corepack `MODULE_NOT_FOUND`; stray `PATHEXT` line). Checks were run by
  invoking the local TypeScript, Vite and Playwright entries through Node directly. The
  equivalent `pnpm typecheck` / `pnpm build` stages themselves were not exercised.
- `command-palette.spec.ts` still assumes `audit`/`quotas` are the final palette
  destinations while the unchanged registry also contains `platform`/`members`; the
  application was not changed to satisfy those pre-existing stale order assertions.
- Screenshots are deterministic API fixtures, not production telemetry.
