# AgentCanvas Version Support Policy

This policy applies from product version 1.0.0. Pre-1.0 builds are migration
sources only and receive no compatibility or backport guarantee.

## Support window

- Each stable `1.y` minor line receives correctness and security fixes for six
  months from its release date.
- The 1.0 support window begins when the `v1.0.0` tag is published. Until that
  tag exists, the release candidate remains `Unreleased` and no support clock is
  active. The release commit must replace the changelog marker with the
  publication date and record `Support through: YYYY-MM-DD`, exactly six months
  later.
- Feature work lands only in the newest minor line. Supported older lines receive
  selected patch backports when the change is low-risk and testable.
- After end of support, users must upgrade to a supported minor before requesting
  a fix. Critical disclosures are still assessed, but no out-of-support patch is
  promised.

## Semantic Versioning

Product releases use `MAJOR.MINOR.PATCH`:

- `MAJOR`: an intentionally incompatible public API, DSL/event contract,
  deployment contract, or supported migration boundary.
- `MINOR`: backward-compatible endpoints, optional fields, node types, features,
  or operational capabilities. A minor may include forward-only migrations.
- `PATCH`: backward-compatible defect, security, documentation, packaging, or
  performance fixes. A patch must not require an API consumer rewrite.

Release candidates may use `-rc.N`. Mutable `latest` images are not published;
stable images use both the product version and the full source commit SHA.

## Public contract promise

The product version and public contract versions are independent. The current
API contract is `1.0.0`; Workflow DSL and execution events are `1.0`.

- Existing endpoints, required request fields, response fields, enum values, DSL
  nodes, and event envelopes cannot be removed or narrowed within contract major
  1.
- Backward-compatible optional additions may ship in a product minor release.
- A breaking change requires a new immutable major baseline under `contracts/`,
  migration guidance, and a product major release.
- Deprecated behavior remains available for at least one minor release and 90
  days, unless retaining it would preserve a critical vulnerability.
- CI regenerates deterministic OpenAPI/JSON Schema artifacts and compares them
  with the immutable major baseline on every change.

Database migrations are forward-only release operations. Image rollback is safe
only before a new migration runs; otherwise restore the pre-upgrade backup into
a new database and application-data volume.

## Supported production baseline

The supported v1.0 horizontal baseline is Python 3.12/3.13, PostgreSQL 17 with
pgvector 0.8.x, Redis 5 or newer, and Docker Compose v2. The shipped CI uses
PostgreSQL 17, pgvector 0.8.6, Redis 7, Node 24, and Chromium. SQLite/Chroma
remains supported for local single-instance development, not multi-replica
production.

Security reports should identify the affected product version, image SHA,
deployment mode, reproduction, and impact. Do not include live credentials or
tenant data in a report.
