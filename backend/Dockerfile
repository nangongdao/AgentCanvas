# AgentCanvas backend image. Build dependencies and uv stay in the builder;
# the runtime layer contains only Python, the locked virtualenv, and app data.
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.11.32

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/app/.venv/bin:${PATH}" \
    APP_DATA_DIR=/app/data \
    STARTUP_MIGRATIONS=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl --fail --show-error --silent \
       -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
       https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-17 \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 agentcanvas \
    && useradd --system --uid 10001 --gid agentcanvas --home-dir /app --shell /usr/sbin/nologin agentcanvas \
    && mkdir -p /app/data /app/restore-data \
    && chown -R agentcanvas:agentcanvas /app

WORKDIR /app
COPY --from=builder --chown=agentcanvas:agentcanvas /app/.venv /app/.venv
COPY --chown=agentcanvas:agentcanvas app ./app
COPY --chown=agentcanvas:agentcanvas alembic ./alembic
COPY --chown=agentcanvas:agentcanvas alembic.ini ./alembic.ini
COPY --chown=agentcanvas:agentcanvas mcp_servers ./mcp_servers
COPY --chown=agentcanvas:agentcanvas plugins ./plugins

USER agentcanvas
EXPOSE 8000

# Migrations run in the separate `migrate` Compose service. The web process is
# deliberately read-only with respect to schema versioning.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
