# Development Handoff: 2026-08-04

## Completed

- U3-5: workflows, executions, knowledge bases, documents, chat sessions, and chat messages expose a shared cursor page contract (`items`, `next_cursor`, `has_more`). Limits are bounded to 1-200; search, sortable fields, asc/desc order, stable ID tie-breakers, and parent/filter scope validation are covered by HTTP tests.
- U3-6 single-process runtime slice: named request windows cover login, execution start, chat send, upload, ingest, retrieval, and MCP probes. Pure-ASGI policies add bounded bodies and operation concurrency; non-streaming operations have request timeouts, while Chat SSE relies on workflow/model timeouts. The engine limits active runs, and `ModelCallGate` adds a process window, process concurrency, per-call timeout, and an execution budget reused across pause/resume. Multipart request and file limits are separate. Settings and `.env.example` expose all thresholds.
- Frontend adapters consume page envelopes. Existing screens retain bounded first-page state; incremental loading remains a UX follow-up.

## Verification

- Backend: `python -m uv run pytest -q` -> 118 passed; Ruff, mypy, and `python -m uv lock --check` pass.
- Frontend: `pnpm typecheck`, `pnpm typecheck:e2e`, and `pnpm build` pass.
- Browser: `pnpm test:e2e` -> 4/4 Chromium paths pass; no listeners remain on ports 8000/5173 afterward.
- Docker smoke was not run because Docker is unavailable on this workstation; container acceptance remains open.

## Remaining Roadmap Work

1. Complete U3-7/U3-8/U3-9/U3-10/U3-11 acceptance work, including the missing P5 browser paths and coverage/migration/accessibility gates.
2. Continue U2 container build/smoke, backup/restore, and release operations.
3. Keep multi-worker shared counters and distributed coordination in I1; add frontend large-list loading and U4 performance benchmarks.

## Review Notes

Standards and specification reviews found no remaining blocker. U3-5 review bound cursor scope and paginated chat message history. U3-6 review added the missing Provider time window, preserved execution budgets across resume, separated multipart/file limits, moved active-run concurrency into the engine, excluded Chat SSE from response-wide timeout, and made ingest cancellation terminal. Unsigned cursors remain an optional future integrity hardening item because cursor tampering changes list position only and does not cross authorization boundaries.
