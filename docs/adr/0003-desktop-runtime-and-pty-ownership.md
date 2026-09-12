# ADR 0003: Desktop Runtime Distribution, Transport, and PTY Ownership

- Status: Accepted
- Date: 2026-09-12
- Scope: C9 desktop nativization (Tauri shell, embedded backend, in-app terminal)

## Context

AgentCanvas ships as a Web deployment (nginx + API + worker roles). The C9
phase adds a desktop distribution so the product runs from a desktop icon
with no Python, Node, Docker, or terminal skills required from the user. The
backend keeps every existing degradation path; the desktop profile
(`APP_PROFILE=desktop`, see `app/core/config.py`) freezes SQLite, the `all`
process role, no Redis, and the SQL vector backend, so the packaged bundle
never carries the chromadb dependency tree.

Three decisions had to be made before any desktop code could be written, and
one of them — how the webview reaches the backend — was blocking and
explicitly gated behind a spike.

## Decision

### 1. Embedded Python runtime as a supervised sidecar

The backend ships as a PyInstaller **onedir** bundle (not onefile: no
temp-extraction per launch, which is what keeps the <5s cold-start gate
reachable) launched by the Tauri shell on a freshly reserved loopback port.
The shell injects exactly two variables — `APP_PORT` and `APP_DATA_DIR`
(`%APPDATA%/AgentCanvas` on Windows, XDG-equivalent elsewhere); the desktop
profile freezes everything else. Readiness is polled on `/readyz`, whose
checks already cover database, migrations, checkpointer, config, vector
store, and sandbox. The sidecar process is bound to a Windows job object
with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` plus an explicit kill on drop, so
no orphan uvicorn survives the shell under any exit path.

The SQL vector backend is a deliberate trade: it is a full-table cosine scan,
not an HNSW index. It holds for desktop-scale corpora (≈10⁴ chunks); beyond
that, re-evaluate before switching the desktop bundle back to Chroma.

### 2. Transport: protocol proxy for REST, direct connection for SSE (verdict B')

The webview page runs under Tauri's custom scheme (`tauri://localhost`, or
`http://tauri.localhost` on Windows/WebView2), so relative `/api` paths no
longer reach the backend. The C9 plan proposed routing everything through a
custom-protocol reverse proxy and required a spike to prove SSE could stream
through it.

The spike (2026-09-12) answered the question from source instead of a
prototype, against the exact release line the project ships
(`tauri-v2.11.5`, `wry-v0.57.0`):

- Tauri's `UriSchemeResponder::respond` is `FnOnce` over
  `http::Response<Cow<'static, [u8]>>` — one call, one fully materialized
  body; no partial-write API exists (`crates/tauri/src/app.rs` L2455-2462).
- wry's WebView2 backend materializes the body with `SHCreateMemStream`
  before `SetResponse` + deferral completion
  (`src/webview2/mod.rs` L1179-1200). WebView2 only ever receives a finished
  response object.

Together that is a constructive proof: **an SSE stream cannot arrive
incrementally through a Tauri custom protocol**. `EventSource` consumers
would see the whole stream land at once after the upstream ends. The
alternative of forking wry with a live `IStream` is outside the plan's cost
envelope.

Therefore: REST rides the protocol proxy (buffering is exactly right for
JSON, and the frontend `fetch` surface stays untouched), while the three SSE
consumers (`api/sse.ts` twice, `useWorkflowCollaboration.ts` once) connect
**directly** to `http://127.0.0.1:<dynamic port>` — the port injected into
the page by a Rust initialization script. The backend desktop profile adds
both Tauri origins to the CORS allow-list (`tauri://localhost`,
`http://tauri.localhost`; never `*`), desktop mode never emits HSTS, and the
page CSP's `connect-src` must include the direct loopback target.

### 3. PTY belongs to Rust, not to the backend

The C9-2 terminal runs on a real PTY owned by the Rust shell
(`portable-pty`), never behind a backend endpoint. A backend PTY would add an
authenticated HTTP/SSE hop and would hand the Web deployment a terminal
surface it must never have — the opposite direction of C8. The desktop
binary is the only place the capability exists.

## Alternatives considered

- **Absolute backend URLs everywhere** — touches every fetch call site and
  widens CORS for no benefit. Rejected (C9 §3.2 option A).
- **`tauri-plugin-localhost`** (serve the UI over `http://localhost:<port>`)
  — upstream documents considerable security risks. Rejected (option C).
- **Fork wry for a live-streaming protocol response** — the only route to
  option B; disproportionate cost, re-verified on every wry upgrade. Rejected.
- **PyInstaller onefile** — slower cold start and higher antivirus
  false-positive rate. Rejected.

## Consequences

- The desktop shell compiles on the MSVC toolchain
  (`frontend/src-tauri/rust-toolchain.toml`); CI runs the Rust check/clippy/
  test gates on `windows-latest`, whose preinstalled VS toolchain matches.
- The PyInstaller bundle excludes the chromadb tree; a desktop RAG
  deployment that outgrows the SQL backend needs a new ADR.
- The three SSE call sites carry a desktop conditional; the web deployment
  keeps using relative paths and must not regress.

## References

- `docs/c9-desktop-tauri-plan.md` §3.2 (spike verdict), §5, §13.4
- `app/core/config.py` — `APP_PROFILE=desktop` freeze set
- `frontend/src-tauri/src/supervisor.rs` — sidecar lifecycle and tests
