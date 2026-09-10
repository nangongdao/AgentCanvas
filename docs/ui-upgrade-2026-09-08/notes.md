# Evidence and design notes

## Observed
- AgentCanvas: existing React/TypeScript frontend with Vite, Tailwind, Lucide, React Flow, Zustand, and Playwright tests.
- Dependencies already installed in frontend/node_modules; availability is not evidence that builds/tests pass.
- Initial git status: untracked design-system/, development_progress.md, seven roadmap/progress documents, notes.md, task_plan.md. No tracked changes reported.
- Existing design system favors restrained, industrial, operational work surfaces and theme-aware tokens; Chinese/English typed dictionaries exist.
- No AGENTS.md or index Markdown files returned by the initial scoped rg search.

## Assumed
- “Upgrade” primarily means improve frontend usability and visual consistency rather than perform speculative package/backend upgrades.

## Unknown / next
- Exact runtime layout, opportunities, backend test requirements, and the reference repository contents.

## Reference and baseline
- Observed: direct GitHub retrieval succeeded (HTTP 200). Reference revision: 8147538b4226ae41e2487a9179e3bcc1f68e8554. Design adaptation is recorded in DESIGN.md; no third-party instructions were executed.
- Observed: baseline Playwright authenticated-home test discovered/executed 1 test, passed in 26.9s including server startup. Serious/critical axe findings were empty in that test.
- Observed visually: existing light overview is flat and tightly spaced, with very small labels, a noninteractive chart, and hardcoded online/ready claims unrelated to health data.
- Observed in source: failed/missing overview summaries currently display some zero counts; saved workflow URLs do not match the /workflows/new NavLink.
- Chosen scope: shared shell/navigation, overview presentation/panels, typed locale additions, scoped CSS, and focused Playwright coverage. Auth, backend, dependencies, and canvas implementation stay out of scope.

## Resumed implementation audit (2026-09-08)
- Observed: tracked partial changes in navigation.ts and zh/en dictionaries; untracked PlatformNavigation.tsx and useSidebarPreference.ts. Neither new component is wired into PlatformShell yet. Preserve and complete this work.
- Observed: current shell still calls the removed controlPlaneOnline translation and renders a hardcoded platform-ready label. The overview currently hardcodes Chinese copy and renders missing execution counts as zero.
- Observed: reference files for Linear and Vercel were re-accessed successfully (HTTP 200) at the recorded immutable revision. The UI/UX helper recommends dense summary/filter/chart layout and precise separators; keep the existing brand palette/fonts rather than its generic palette/font suggestion.
- Scope: integrate the shell, isolate overview data orchestration, add a keyboard/touch cost inspection interaction, style the overview, and add focused E2E regressions. No changes to backend, dependencies, or user-owned root plans/design-system.
- Invocation notes: combined source reads exceeded the tool output cap; narrowed to specific line ranges before relying on the content. Windows rg does not expand wildcard path arguments; two discovery searches returned OS error 123 for wildcard paths, then exact file paths were used.

## Verification findings and corrections
- Existing shell regression: 6/6 passed, exit 0 (38.1s). New experience suite first run: 9/10 passed, exit 1 (1.0m).
- The new dark test expected `data-theme="dark"`, but `useTheme.ts` intentionally removes the attribute for the default dark palette; `index.html` uses the same convention. Corrected the test to assert the effective CSS `color-scheme` for both light and dark, without changing the established theme implementation. Dark interaction/axe assertions after the failed line were not executed in that first run.
- `pnpm build` produced artifacts and a passing bundle check, but also emitted `'PATHEXT:' is not recognized as an internal or external command` and ran Vite twice. Inspection found stray trailing `PATHEXT:;.JS;=;%` and a duplicated Node invocation in the locally installed `frontend/node_modules/.bin/vite.cmd`. This file is outside the tracked UI changes; do not silently call that launch clean. Verify the equivalent build stages directly through Node, without changing installed dependencies.
- One test launch failed before execution because paths redundantly included `frontend/` while already in that working directory. Corrected paths; the subsequent run discovered and executed 10 tests.
- Font provenance correction: `index.html` loads the existing fonts from Google Fonts, not bundled assets. DESIGN.md now says "existing typography". No new font or dependency was added.
