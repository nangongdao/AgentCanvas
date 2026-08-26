"""Static release-contract checks that complement the CI Docker smoke."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
import yaml

from app import __version__
from app.core.config import Settings, get_settings, load_settings, validate_runtime_settings
from app.schemas.api import MetaOut

ROOT = Path(__file__).resolve().parents[2]


def test_product_version_and_release_documents_are_consistent() -> None:
    pyproject = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    frontend = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert __version__ == "1.0.0"
    assert pyproject["project"]["version"] == __version__
    assert frontend["version"] == __version__
    assert MetaOut().version == __version__

    required = {
        "CHANGELOG.md": "## [1.0.0] - Unreleased (release candidate)",
        "docs/v1.0-upgrade-guide.md": "v1.0 Upgrade Guide",
        "docs/v1.0-sqlite-to-postgresql.md": "SQLite to PostgreSQL",
        "docs/version-support-policy.md": "Version Support Policy",
    }
    for relative, marker in required.items():
        assert marker in (ROOT / relative).read_text(encoding="utf-8")


def _compose(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def test_local_compose_has_one_shot_migration_before_web() -> None:
    compose = _compose("docker-compose.yml")
    services = compose["services"]
    assert services["migrate"]["command"] == ["python", "-m", "app.services.migrate"]
    assert services["migrate"]["restart"] == "no"
    assert services["backend"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["backend"]["environment"]["STARTUP_MIGRATIONS"] == "false"
    assert services["backend"]["healthcheck"]["test"][-1].endswith("/readyz')")


def test_production_compose_is_private_bounded_and_backup_aware() -> None:
    compose = _compose("compose.prod.yml")
    services = compose["services"]
    backend = services["backend"]
    assert backend["environment"]["APP_ENV"] == "production"
    assert backend["environment"]["AUTH_MODE"] == "token"
    assert backend["environment"]["DATABASE_URL"] == "${DATABASE_URL:?DATABASE_URL is required}"
    assert backend["environment"]["APP_PROCESS_ROLE"] == "api"
    assert backend["environment"]["DATABASE_POOL_SIZE"] == "${DATABASE_POOL_SIZE:-5}"
    assert backend["deploy"]["replicas"] == 2
    assert services["bootstrap"]["command"] == ["python", "-m", "app.services.bootstrap"]
    assert backend["depends_on"]["bootstrap"]["condition"] == ("service_completed_successfully")
    assert backend["read_only"] is True
    assert backend["cap_drop"] == ["ALL"]
    assert "ports" not in backend
    assert services["redis"]["read_only"] is True
    assert "ports" not in services["redis"]
    assert services["redis"]["healthcheck"]["test"] == ["CMD", "redis-cli", "ping"]
    assert services["frontend"]["read_only"] is True
    assert services["backup"]["profiles"] == ["backup"]
    assert "--quiesced" in services["backup"]["command"]
    assert services["restore"]["profiles"] == ["restore"]
    assert services["postgres"]["image"] == "pgvector/pgvector:0.8.6-pg17-bookworm"
    assert services["worker"]["deploy"]["replicas"] == 2
    assert services["worker"]["environment"]["APP_PROCESS_ROLE"] == "worker"
    assert services["worker"]["command"] == ["python", "-m", "app.services.worker"]
    assert services["scheduler"]["environment"]["APP_PROCESS_ROLE"] == "scheduler"
    assert services["relay"]["environment"]["APP_PROCESS_ROLE"] == "relay"
    assert services["restore-drill"]["profiles"] == ["restore"]
    assert services["restore"]["volumes"][-1] == "restore-data:/restore-data"
    assert services["restore-api"]["profiles"] == ["restore"]
    assert services["restore-api"]["environment"]["APP_DATA_DIR"] == "/restore-data"
    assert services["restore-api"]["volumes"] == ["restore-data:/restore-data"]
    assert services["restore-worker"]["profiles"] == ["restore"]
    assert services["restore-worker"]["command"] == [
        "python",
        "-m",
        "app.services.worker",
    ]
    assert services["restore-worker"]["environment"]["APP_PROCESS_ROLE"] == "worker"
    assert services["restore-worker"]["environment"]["APP_DATA_DIR"] == "/restore-data"


def test_release_images_pin_toolchains_and_drop_root() -> None:
    backend = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    frontend = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    package = (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    assert "AS builder" in backend and "AS runtime" in backend
    assert "uv==0.11.32" in backend
    assert "USER agentcanvas" in backend
    assert "FROM python:3.12-slim-bookworm AS builder" in backend
    runtime = backend.split("FROM python:3.12-slim-bookworm AS runtime", 1)[1]
    assert "build-essential" not in runtime
    assert "postgresql-client-17" in runtime
    assert "mkdir -p /app/data /app/restore-data" in runtime
    assert "COPY --chown=agentcanvas:agentcanvas plugins ./plugins" in runtime
    assert "FROM node:24-slim" in frontend
    assert "pnpm@11.6.0" in frontend
    assert "nginxinc/nginx-unprivileged:1.27.5-alpine" in frontend
    assert "resolver 127.0.0.11 valid=5s ipv6=off" in frontend
    assert "server backend:8000 resolve" in frontend
    assert "proxy_pass http://agentcanvas_backend" in frontend
    assert '"packageManager": "pnpm@11.6.0"' in package


def test_startup_migration_switch_is_environment_driven(monkeypatch) -> None:
    monkeypatch.setenv("STARTUP_MIGRATIONS", "false")
    assert load_settings().startup_migrations is False
    monkeypatch.setenv("STARTUP_MIGRATIONS", "true")
    assert load_settings().startup_migrations is True


@pytest.mark.parametrize("role", ["scheduler", "relay"])
def test_coordination_roles_load_settings_without_creating_data_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    data_dir = tmp_path / "read-only-data"
    monkeypatch.setenv("APP_PROCESS_ROLE", role)
    monkeypatch.setenv("APP_DATA_DIR", str(data_dir))
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.process_role == role
        assert not data_dir.exists()
    finally:
        get_settings.cache_clear()


def test_horizontal_roles_require_postgresql_redis_and_release_migrations() -> None:
    with pytest.raises(RuntimeError, match="postgresql"):
        validate_runtime_settings(Settings(process_role="api", startup_migrations=False))
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        validate_runtime_settings(
            Settings(
                process_role="worker",
                database_url="postgresql+asyncpg://db/agentcanvas",
                startup_migrations=False,
            )
        )
    with pytest.raises(RuntimeError, match="STARTUP_MIGRATIONS=false"):
        validate_runtime_settings(
            Settings(
                process_role="relay",
                database_url="postgresql+asyncpg://db/agentcanvas",
                redis_url="redis://redis:6379/0",
            )
        )
    validate_runtime_settings(
        Settings(
            process_role="scheduler",
            database_url="postgresql+asyncpg://db/agentcanvas",
            redis_url="redis://redis:6379/0",
            startup_migrations=False,
        )
    )


def test_ci_requires_real_postgresql_migration_and_drift_gate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "postgresql-integration:" in workflow
    assert "POSTGRES_TEST_DATABASE_URL:" in workflow
    assert "pytest -q -m postgres" in workflow
    assert "redis-integration:" in workflow
    assert "image: redis:7-alpine" in workflow
    assert "REDIS_TEST_URL:" in workflow
    assert "pytest -q -m redis" in workflow
    assert "distributed_smoke.py topology" in workflow
    assert "stop worker" in workflow
    assert "docker kill" in workflow
    assert "docker pause" in workflow
    assert "docker unpause" in workflow
    assert "Quiesce all application writers before the snapshot" in workflow
    assert "attempt FROM execution_queue_items" in workflow
    assert "redis-cli XADD" in workflow
    assert "stream-redis-loss" in workflow
    assert "prepare-ambiguous" in workflow
    assert "dead_letter" in workflow
    assert "redis-cli XLEN" in workflow
    assert "kill -s SIGKILL redis" in workflow
    assert "restore-drill" in workflow
    assert "agentcanvas-restore-api" in workflow
    assert "agentcanvas-restore-worker" in workflow
    assert "prepare-rag" in workflow
    assert "verify-rag" in workflow
    assert "resume-human --base-url http://127.0.0.1:18000" in workflow


def test_container_release_fault_steps_fail_fast_and_restore_dependencies() -> None:
    workflow = _compose(".github/workflows/ci.yml")
    job = workflow["jobs"]["container-release"]
    assert job["timeout-minutes"] >= 60
    assert job["defaults"]["run"]["shell"] == ("bash --noprofile --norc -Eeuo pipefail {0}")

    steps = {step["name"]: step for step in job["steps"] if "name" in step}
    worker_restore = steps["Restore two-worker topology after destructive fault"]["run"]
    assert "up -d --wait --no-deps --scale worker=2 worker" in worker_restore
    assert 'ps --status running -q worker | wc -l)" -eq 2' in worker_restore

    redis_loss = steps["Live SSE survives Redis loss and Redis reconnects"]["run"]
    assert "trap cleanup_sse EXIT" in redis_loss
    assert "up -d --wait redis" in redis_loss
    assert "start redis" not in redis_loss

    unpause = steps["Unpause fault-injected containers"]["run"]
    assert steps["Unpause fault-injected containers"]["if"] == "always()"
    assert "ps --all -q worker" in unpause
    assert 'docker unpause "$container_id"' in unpause


def test_container_release_restore_uses_explicit_maintenance_database() -> None:
    workflow = _compose(".github/workflows/ci.yml")
    steps = {
        step["name"]: step
        for step in workflow["jobs"]["container-release"]["steps"]
        if "name" in step
    }
    restore = steps["Restore into an isolated empty PostgreSQL database"]["run"]
    assert 'createdb -U "$POSTGRES_USER" --maintenance-db "$POSTGRES_DB"' in restore


def test_tagged_release_publishes_version_and_sha_images() -> None:
    workflow = _compose(".github/workflows/ci.yml")
    container_job = workflow["jobs"]["container-release"]
    assert "packages" not in container_job["permissions"]
    container_steps = {step["name"]: step for step in container_job["steps"] if "name" in step}
    export = container_steps["Export tested release images"]
    assert "agentcanvas-backend:$GITHUB_SHA" in export["run"]
    assert "agentcanvas-frontend:$GITHUB_SHA" in export["run"]
    assert export["if"] == "startsWith(github.ref, 'refs/tags/v')"
    assert container_steps["Upload tested release images"]["if"] == export["if"]

    publish_job = workflow["jobs"]["publish-release"]
    assert publish_job["if"] == "startsWith(github.ref, 'refs/tags/v')"
    assert publish_job["needs"] == ["container-release"]
    assert publish_job["permissions"]["packages"] == "write"
    assert publish_job["env"]["PRODUCT_VERSION"] == "1.0.0"
    steps = {step["name"]: step for step in publish_job["steps"] if "name" in step}
    assert "docker load" in steps["Load tested release images"]["run"]
    publish = steps["Publish immutable version and SHA image tags"]
    command = publish["run"]
    assert 'test "$release_version" = "$PRODUCT_VERSION"' in command
    assert "docker manifest inspect" in command
    assert "python backend/scripts/check_release_metadata.py" in command
    assert '--version "$PRODUCT_VERSION"' in command
    assert "Refusing to overwrite existing image tag" in command
    assert ':$PRODUCT_VERSION"' in command
    assert ':$GITHUB_SHA"' in command
    assert "docker push" in command
    assert "latest" not in command

    step_names = [step.get("name") for step in container_job["steps"]]
    assert step_names.index("Export tested release images") > step_names.index(
        "Stop container topology"
    )
