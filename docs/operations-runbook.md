# AgentCanvas Operations Runbook

This runbook covers both supported modes:

- local/single-host: `APP_PROCESS_ROLE=all`, SQLite, embedded Chroma, and
  optional Redis;
- horizontal production: PostgreSQL 17 with pgvector, Redis, two API replicas,
  two execution workers, one scheduler, one durable event relay, and nginx.

The production release still requires the repository CI fault-injection and
restore-drill lane to pass for the exact image tag being deployed.

## 1. Prerequisites and secrets

- Docker Engine with Compose v2 and at least 8 GiB free memory for the default
  horizontal topology.
- Persistent disk sized for `postgres-data`, `backend-data`, `redis-data`, and
  `backup-data`.
- Copy `.env.production.example` to an operator-owned `.env.production` with
  mode `0600`; never commit it.
- Generate `SECRET_KEY` with Fernet and use independent random admin/editor/
  viewer tokens. Record the key in the deployment secret manager because an
  unknown key makes encrypted model and MCP credentials unrecoverable.
- Pin `IMAGE_TAG` to the release version or commit SHA. Do not deploy `latest`.

### Secret references and rotation

Model API keys and MCP env/header values may be stored as encrypted references
instead of copied plaintext:

- `env://UPPER_CASE_NAME` resolves from the backend process environment.
- `docker://relative-file` resolves only below the absolute `DOCKER_SECRET_DIR`
  (default `/run/secrets`) and is read with a bounded UTF-8 limit.
- `external://path` is an adapter seam. An embedding deployment must inject an
  `external_secret_resolver` into `create_app`; the default application has no
  network lookup and fails closed when the adapter is absent.

The API returns only `********` plus a safe source label. To rotate a value,
replace the environment/Docker/external value and restart or reload the
provider process as appropriate; the encrypted reference stays unchanged.
To change the reference itself, update the model or MCP entry with the new
reference. Never place the resolved secret in an audit detail, URL, log, or
shell argument. Mount Docker secret files read-only and keep
`DOCKER_SECRET_DIR` outside application-writeable volumes.

All commands below run from the repository root:

```bash
docker compose --env-file .env.production -f compose.prod.yml config --quiet
docker compose --env-file .env.production -f compose.prod.yml build
docker compose --env-file .env.production -f compose.prod.yml up -d --wait \
  --scale backend=2 --scale worker=2
curl --fail http://127.0.0.1:8080/readyz
```

The `migrate` service must finish before `bootstrap`; bootstrap performs the
idempotent secret migration and default model/MCP/workflow/template seeds.
Only then do the API, worker, scheduler, and relay services start. Every
long-running horizontal role sets `STARTUP_MIGRATIONS=false`; schema and seed
ownership remain with these one-shot release steps.

## 2. Health and traffic

- `/livez` proves only that the backend process responds.
- `/readyz` blocks traffic when the DB, migration head, checkpointer, or
  production security config is unavailable. It reports `role`, `instance_id`,
  database backend, collaboration backend, and vector backend.
- Every HTTP response includes `X-AgentCanvas-Instance`; use it to prove that a
  load balancer reaches both API replicas. The bundled nginx uses Docker's DNS
  resolver and refreshes the backend service every five seconds after replica
  recreation.
- Do not route traffic until Compose reports both `backend` replicas healthy
  and `/readyz` returns HTTP 200 with `status=ready`, `role=api`, and
  `vector_store.backend=pgvector`.
- Use `docker compose ... ps` and `docker compose ... logs --tail=200 backend`
  to correlate startup failures. Include `worker scheduler relay` when tracing
  an execution. Logs rotate at 10 MiB with five files.

## 3. PostgreSQL backup and restore drill

Quiesce API writers and drain or pause execution workers before the snapshot.
Leave PostgreSQL running. The backup service uses `pg_dump` custom format with
`--no-owner --no-privileges`, copies uploads/workspace/legacy Chroma data, and
writes a SHA-256 manifest covering every archive member. Database credentials
are passed only through `PG*` environment variables.

```bash
docker compose --env-file .env.production -f compose.prod.yml stop frontend backend worker scheduler relay
docker compose --env-file .env.production -f compose.prod.yml \
  --profile backup run --rm backup
```

Copy the resulting archive out of the `backup-data` volume to offline/object
storage and apply retention there. A backup is not accepted until it restores
into a different empty database and passes the inventory and readiness drills.
Set `PG_RESTORE_TARGET_DATABASE_URL` to that database; it must never equal
`DATABASE_URL`.

```bash
docker compose --env-file .env.production -f compose.prod.yml exec -T postgres \
  createdb -U agentcanvas agentcanvas_restore
docker compose --env-file .env.production -f compose.prod.yml \
  --profile restore run --rm restore
docker compose --env-file .env.production -f compose.prod.yml \
  --profile restore run --rm restore-drill
docker compose --env-file .env.production -f compose.prod.yml --profile restore \
  run --no-deps -d --name agentcanvas-restore-api -p 18000:8000 restore-api
curl --fail http://127.0.0.1:18000/readyz
docker rm -f agentcanvas-restore-api
```

The restore validates paths, member hashes, dump metadata, source/target
identity, and target emptiness before invoking `pg_restore --exit-on-error`.
It never runs `clean`, `drop`, or in-place replacement. `restore-drill` compares
Alembic head, every application table, checkpoint tables, waiting approvals,
events, pgvector extension/version, vector row counts, and a database-side
distance probe. After it passes, resume one preserved approval and run a known
retrieval against the restored API before promoting it.

For a host-native deployment, equivalent commands are:

```bash
cd backend
python -m uv run python -m app.services.backup backup ../backups/snapshot.tar.gz --quiesced
python -m uv run python -m app.services.backup restore ../backups/snapshot.tar.gz \
  --target-database-url "$PG_RESTORE_TARGET_DATABASE_URL" \
  --data-dir ../restore-data
python -m uv run python -m app.services.restore_drill \
  --target-database-url "$PG_RESTORE_TARGET_DATABASE_URL"
```

For legacy SQLite single-host data, the same `backup` command continues to use
SQLite's online backup API and includes checkpoints, Chroma, uploads, workspace,
and optional Redis AOF. SQLite restore is the only mode that accepts `--force`;
it moves replaced paths to a timestamped `*.pre-restore-*` recovery directory.

## 4. Upgrade and rollback

The version-specific entry point is the [v1.0 upgrade guide](v1.0-upgrade-guide.md).
Operators moving from a v0.x single-host installation must use the complete
[SQLite to PostgreSQL migration manual](v1.0-sqlite-to-postgresql.md), which
also covers relational data before the checkpoint and vector steps below.

1. Drain traffic and let current executions finish; record any
   `waiting_approval` execution IDs.
2. Create and export a verified backup.
3. Pull/build the new immutable image tag.
4. Run `docker compose ... run --rm migrate` and require exit code 0.
5. Run `docker compose ... run --rm bootstrap` and require exit code 0.
6. Start the stack with `up -d --wait`, inspect `/readyz`, then run
   `backend/scripts/container_smoke.py` against the public endpoint.
7. Restore traffic gradually and watch error rate, execution terminal states,
   ingest failures, disk use, and Redis fallback.

Application rollback uses the previous image tag. Database rollback is not
automatic: stop all services and restore the pre-upgrade archive so schema,
checkpoints, vectors, uploads, and Redis state remain one consistency point.

### SQLite to PostgreSQL checkpointer cutover

The application database migration to PostgreSQL must be completed and
verified separately. Before starting a PostgreSQL-backed API, copy the legacy
LangGraph checkpoint file while no execution worker is running:

```bash
cd backend
python -m uv run python -m app.services.migrate_checkpoints \
  --source data/checkpoints.db
```

The command requires `DATABASE_URL=postgresql+asyncpg://...`, uses a bounded
Psycopg pool, is safe to repeat, and validates every persisted
`waiting_approval` execution after the copy. The API refuses startup when any
waiting execution has no checkpoint. Preserve the SQLite file until the first
PostgreSQL restart/resume drill succeeds; it is not a PostgreSQL backup.

### Embedded Chroma to PostgreSQL pgvector cutover

Install pgvector on the target PostgreSQL server and run the current release
head `0030_callback_activation_boundary`, which includes the vector-table migration
`0024_document_chunks`, before copying vectors. The application relational data,
including `knowledge_bases` and `documents`, must already exist in the target
database. Stop ingest writers, mount the old Chroma directory read-only, and set
`VECTOR_BACKEND=pgvector` plus the target `DATABASE_URL`:

```bash
cd backend
python -m uv run python -m app.services.migrate_vectors \
  --source data/chroma
```

The command first validates every `ready` document without changing the target:
source vectors must exist and their knowledge-base ID, filename, and chunk count
must match the relational row. Only a complete preflight starts the idempotent
copy. Each document replacement locks its durable document row; a rerun replaces
that document atomically. Require the final document/chunk totals and `/readyz`
`vector_store.backend=pgvector` with a non-empty `extension_version`, then run a
known retrieval from a second API replica. Preserve Chroma until that check and a
PostgreSQL backup/restore drill pass.

## 5. Key rotation

Database-backed users receive a 15-minute access session and a 30-day
refresh-token family by default; both lifetimes are configurable within enforced
security bounds. Every refresh rotates both credentials;
replay of a consumed refresh token revokes the complete family. Tune
`AUTH_SESSION_TTL_SECONDS` and `AUTH_REFRESH_TTL_SECONDS` only with the refresh
TTL longer than the access TTL.

Optional enterprise login uses OIDC Authorization Code + PKCE. Configure
`OIDC_ISSUER`, `OIDC_CLIENT_ID`, and the provider-registered
`OIDC_REDIRECT_URI` together; production issuer/callback URLs must be HTTPS.
Set `OIDC_POST_LOGIN_REDIRECT_URL` to a same-origin path or a URL whose origin is
listed in `CORS_ORIGINS`. The backend validates discovery issuer, PKCE, state,
nonce, asymmetric JWKS signatures, audience, authorized party, expiry, and a
verified email before it links or creates a local account. Keep
`OIDC_DEFAULT_ROLE=viewer` unless a reviewed deployment policy requires more.

API tokens can be rotated by updating `.env.production` and recreating backend
and frontend. Rotate one role at a time and verify `/api/auth/login` before
removing the old deployment secret.

`SECRET_KEY` rotation requires decrypting and re-encrypting stored model/MCP
secrets with an application migration while both keys are available. Do not
simply replace the key. Until a dual-key rotation command is implemented, take
a backup, export/re-enter affected credentials through the admin UI, rotate the
key, restart, and verify every provider/MCP connection.

## 6. Disk growth and corruption recovery

- Alert before any data volume reaches 80%. Expand the host filesystem first;
  named volumes then see the additional capacity without recreation.
- In horizontal production, PostgreSQL tables/checkpoints/pgvector and
  `/app/data/uploads` dominate capacity; Redis AOF is operational stream/cache
  state rather than the durable execution source of truth. In local mode,
  Chroma and SQLite WAL may dominate. Never delete individual database, Chroma,
  or SQLite sidecar files while services run.
- If `/readyz` reports a DB/checkpointer error, stop writers, preserve the
  damaged volume, and restore the newest verified archive into a new volume.
- If only a document/vector is corrupt, delete and re-ingest through the API;
  do not edit Chroma files directly.
- Redis is non-blocking because memory falls back to SQLite, but restore its AOF
  with the same backup generation when conversational memory consistency matters.

## 7. Emergency stop

First remove public traffic, then stop API ingress. Allow workers a bounded
drain period before stopping the scheduler and relay:

```bash
docker compose --env-file .env.production -f compose.prod.yml stop -t 30 frontend backend
docker compose --env-file .env.production -f compose.prod.yml stop -t 30 worker
docker compose --env-file .env.production -f compose.prod.yml stop scheduler relay redis
```

Use `docker compose ... kill` only for an active security incident or hung
runtime. On the next start, the scheduler classifies expired work: replay-safe
items return to the queue, while ambiguous side-effecting work is dead-lettered.
Inspect dead letters and pending cancellations before reopening traffic.

## 8. Outbound callback operations

Outbound workflow callbacks are delivered by the `scheduler` process from
`workflow_callback_deliveries`. A `retry_wait` row is eligible after
`next_attempt_at`; an expired `leased` row is reclaimed by another scheduler.
`dead_letter` rows require operator inspection and are not retried automatically.
Receivers should deduplicate with `X-AgentCanvas-Delivery-ID` or
`Idempotency-Key`, reject stale `X-AgentCanvas-Timestamp` values, and verify
`X-AgentCanvas-Signature` as HMAC-SHA256 over `<timestamp>.<raw-body>`.

Do not configure localhost, private, link-local, reserved, or metadata-service
addresses. The API rejects explicit non-public IPs and the dispatcher checks all
DNS answers again immediately before sending. Callback URLs do not follow
redirects and do not inherit process proxy settings.

## 9. Desktop (Tauri) troubleshooting (C9)

The desktop shell starts a packaged backend sidecar on a per-launch loopback
port and stores user data outside the install directory. Nothing below
applies to the containerized Web deployment.

### Startup fails or the splash shows an error

- The splash lists each `/readyz` check (database, migrations, checkpointer,
  config, vector store, sandbox) with its state; the first row that is not
  `ready` is the blocker.
- Full sidecar output — uvicorn logs, migration progress, tracebacks — is
  appended to `<data>/logs/sidecar.log`, and the failure surface prints the
  exact path. Copy the diagnostics button captures the error plus that path.
- A sidecar that exits before readiness fails the wait immediately (exit
  code shown); migration failures are the common cause. Inspect the log for
  the alembic revision that failed.

### Where data lives

- Windows: `%APPDATA%\AgentCanvas` (injected as `APP_DATA_DIR`). Everything
  derives from it: `app.db`, `checkpoints.db`, the SQL vector store,
  `uploads/`, `logs/`, and the local encryption key.
- Resetting the app = closing it and deleting that directory; there is no
  server-side state. Back it up first — the key file re-encrypts nothing:
  a deleted `.agentcanvas.key` makes existing encrypted MCP/model secrets
  unreadable and they must be re-entered.

### Ports and conflicts

- The shell reserves a free loopback port per launch and passes it to the
  sidecar; a conflict with a dev server on 8000 is therefore impossible.
  `AGENTCANVAS_BACKEND_URL=http://127.0.0.1:8000` reuses an already-running
  uvicorn instead of spawning one (development mode).
- A second double-click focuses the existing window; it never starts a
  second backend. Orphan sidecars cannot survive the shell: the child runs
  inside a Windows job object with kill-on-close plus an explicit kill on
  drop — verify with `tasklist` filtered on `agentcanvas-backend` if unsure.

### Fonts and offline use

- All fonts ship inside the app (`public/fonts`, OFL licenses included);
  first paint and offline startup never contact a font CDN. If text renders
  in a system fallback after an upgrade, a new UI string may have introduced
  characters outside the subset — regenerate `noto-sans-sc-subset.woff2`
  with the charset sweep described in `frontend/src/index.css`.
