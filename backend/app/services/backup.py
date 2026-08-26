"""Checksummed SQLite and PostgreSQL backup/restore workflows.

SQLite uses the online backup API and restores only behind ``--force``.
PostgreSQL uses native custom-format dumps and restores only into a distinct,
empty database plus a distinct, empty application-data directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

from sqlalchemy.engine import URL, make_url

from app.core.config import Settings, load_settings

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
POSTGRES_MANIFEST_VERSION = 2
BACKUP_DIRS = ("chroma", "uploads", "workspace")
POSTGRES_DUMP_NAME = "database.dump"


class BackupError(RuntimeError):
    """Raised when a backup is incomplete or a restore is unsafe."""


def _database_path(settings: Settings) -> Path:
    url = settings.effective_database_url
    if not url.startswith("sqlite"):
        raise BackupError("backup/restore currently supports local SQLite only")
    try:
        raw = url.split("///", 1)[1].split("?", 1)[0]
    except IndexError as exc:
        raise BackupError(f"unsupported SQLite URL: {url}") from exc
    return Path(unquote(raw)).resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _postgres_url(value: str, *, label: str) -> URL:
    try:
        url = make_url(value)
    except Exception as exc:  # noqa: BLE001 - normalize parser failures
        raise BackupError(f"invalid {label} PostgreSQL URL") from exc
    if url.get_backend_name() != "postgresql" or not url.database:
        raise BackupError(f"{label} URL must name a PostgreSQL database")
    return url


def _postgres_identity(url: URL) -> tuple[str, int, str]:
    return (
        (url.host or "localhost").lower(),
        int(url.port or 5432),
        str(url.database),
    )


def _postgres_env(url: URL) -> dict[str, str]:
    env = dict(os.environ)
    values = {
        "PGHOST": url.host,
        "PGPORT": str(url.port) if url.port is not None else None,
        "PGUSER": url.username,
        "PGPASSWORD": url.password,
        "PGDATABASE": url.database,
    }
    for name, value in values.items():
        if value is not None:
            env[name] = value
    query = dict(url.query)
    sslmode = query.get("sslmode") or query.get("ssl")
    if sslmode:
        env["PGSSLMODE"] = str(sslmode)
    return env


def _find_tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise BackupError(f"required PostgreSQL client tool not found: {name}")
    return executable


def _run_native(args: list[str], *, env: dict[str, str]) -> str:
    result = subprocess.run(
        args,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "native command failed").strip()
        raise BackupError(f"{Path(args[0]).name} failed: {detail[:500]}")
    return result.stdout.strip()


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_db = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    target_db = sqlite3.connect(destination)
    try:
        source_db.backup(target_db)
    finally:
        target_db.close()
        source_db.close()


def _copy_tree(source: Path, destination: Path) -> None:
    if source.exists():
        shutil.copytree(source, destination, dirs_exist_ok=True)


def _manifest(root: Path) -> dict[str, object]:
    members: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != MANIFEST_NAME:
            members.append({"path": path.relative_to(root).as_posix(), "sha256": _sha256(path)})
    return {
        "version": MANIFEST_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "members": members,
    }


def create_backup(settings: Settings, archive: Path) -> Path:
    """Create an archive and return its resolved path."""
    archive = archive.expanduser().resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agentcanvas-backup-") as temporary:
        root = Path(temporary)
        _snapshot_sqlite(_database_path(settings), root / "app.db")
        _snapshot_sqlite(settings.checkpoint_db_path, root / "checkpoints.db")
        _copy_tree(settings.chroma_dir, root / "chroma")
        _copy_tree(settings.uploads_dir, root / "uploads")
        _copy_tree(settings.workspace_dir, root / "workspace")
        if settings.redis_aof_dir:
            _copy_tree(settings.redis_aof_dir, root / "redis")
        manifest = _manifest(root)
        (root / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        with tarfile.open(archive, "w:gz") as stream:
            for path in sorted(root.rglob("*")):
                stream.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
    return archive


def create_postgres_backup(
    settings: Settings,
    archive: Path,
    *,
    quiesced: bool = False,
) -> Path:
    """Create a checksummed pg_dump archive after writers were quiesced."""
    if not quiesced:
        raise BackupError(
            "PostgreSQL backup requires quiesced API/worker/scheduler/relay writers; "
            "stop them and pass --quiesced"
        )
    source = _postgres_url(settings.effective_database_url, label="source")
    archive = archive.expanduser().resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    pg_dump = _find_tool("pg_dump")
    env = _postgres_env(source)
    tool_version = _run_native([pg_dump, "--version"], env=env)
    with tempfile.TemporaryDirectory(prefix="agentcanvas-pg-backup-") as temporary:
        root = Path(temporary)
        dump = root / POSTGRES_DUMP_NAME
        _run_native(
            [
                pg_dump,
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--no-password",
                "--file",
                str(dump),
            ],
            env=env,
        )
        if not dump.is_file() or dump.stat().st_size == 0:
            raise BackupError("pg_dump completed without a non-empty archive")
        for name in BACKUP_DIRS:
            _copy_tree(settings.data_dir / name, root / name)
        manifest = _manifest(root)
        manifest.update(
            {
                "version": POSTGRES_MANIFEST_VERSION,
                "kind": "postgresql",
                "consistency": "quiesced",
                "database": {
                    "archive": POSTGRES_DUMP_NAME,
                    "format": "custom",
                    "source_database": source.database,
                    "pg_dump_version": tool_version,
                    "size_bytes": dump.stat().st_size,
                    "sha256": _sha256(dump),
                },
            }
        )
        (root / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        with tarfile.open(archive, "w:gz") as stream:
            for path in sorted(root.rglob("*")):
                stream.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
    return archive


def _safe_extract(stream: tarfile.TarFile, root: Path) -> None:
    for member in stream.getmembers():
        target = (root / member.name).resolve()
        if target != root and root not in target.parents:
            raise BackupError(f"archive path escapes root: {member.name}")
        if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
            raise BackupError(f"unsupported archive member: {member.name}")
    stream.extractall(root, filter="data")


def _validated_manifest(root: Path, *, version: int) -> dict[str, object]:
    try:
        manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
        members = manifest["members"]
        if manifest["version"] != version or not isinstance(members, list):
            raise ValueError("unsupported manifest")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BackupError("invalid backup manifest") from exc
    for entry in members:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise BackupError("invalid manifest member")
        path = (root / entry["path"]).resolve()
        if root not in path.parents or not path.is_file():
            raise BackupError(f"missing backup member: {entry['path']}")
        if _sha256(path) != entry.get("sha256"):
            raise BackupError(f"checksum mismatch: {entry['path']}")
    return manifest


def restore_backup(settings: Settings, archive: Path, *, force: bool = False) -> Path:
    """Validate and restore an archive; return the target data directory."""
    if not force:
        raise BackupError("restore requires force=True; use the CLI --force flag")
    archive = archive.expanduser().resolve()
    if not archive.is_file():
        raise BackupError(f"backup archive not found: {archive}")
    with tempfile.TemporaryDirectory(prefix="agentcanvas-restore-") as temporary:
        root = Path(temporary)
        with tarfile.open(archive, "r:gz") as stream:
            _safe_extract(stream, root)
        manifest = _validated_manifest(root, version=MANIFEST_VERSION)
        members = manifest["members"]
        assert isinstance(members, list)

        target = settings.data_dir.resolve()
        target.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        previous = target.parent / f"{target.name}.pre-restore-{stamp}"
        touched = [root / entry["path"].split("/", 1)[0] for entry in members]
        top_levels = sorted({path.name for path in touched})
        destinations = {
            "app.db": _database_path(settings),
            "checkpoints.db": settings.checkpoint_db_path,
            "chroma": settings.chroma_dir,
            "uploads": settings.uploads_dir,
            "workspace": settings.workspace_dir,
            "redis": settings.redis_aof_dir or (settings.data_dir / "redis"),
        }
        unknown = set(top_levels) - destinations.keys()
        if unknown:
            raise BackupError(f"unsupported top-level backup member: {sorted(unknown)[0]}")
        if any(destinations[name].exists() for name in top_levels):
            previous.mkdir(parents=True, exist_ok=True)
        for name in top_levels:
            current = destinations[name]
            current.parent.mkdir(parents=True, exist_ok=True)
            if current.exists():
                shutil.move(str(current), str(previous / name))
            source = root / name
            if source.is_dir():
                shutil.copytree(source, current)
            else:
                shutil.copy2(source, current)
    return target


def restore_postgres_backup(
    settings: Settings,
    archive: Path,
    *,
    target_database_url: str,
    target_data_dir: Path,
) -> str:
    """Restore a validated dump into a distinct, empty PostgreSQL database."""
    source = _postgres_url(settings.effective_database_url, label="source")
    target = _postgres_url(target_database_url, label="target")
    if _postgres_identity(source) == _postgres_identity(target):
        raise BackupError("target database must differ from the configured source database")
    target_data_dir = target_data_dir.expanduser().resolve()
    source_data_dir = settings.data_dir.expanduser().resolve()
    if (
        target_data_dir == source_data_dir
        or source_data_dir in target_data_dir.parents
        or target_data_dir in source_data_dir.parents
    ):
        raise BackupError("target data directory must differ from the configured source data")
    archive = archive.expanduser().resolve()
    if not archive.is_file():
        raise BackupError(f"backup archive not found: {archive}")
    psql = _find_tool("psql")
    pg_restore = _find_tool("pg_restore")
    env = _postgres_env(target)
    with tempfile.TemporaryDirectory(prefix="agentcanvas-pg-restore-") as temporary:
        root = Path(temporary)
        with tarfile.open(archive, "r:gz") as stream:
            _safe_extract(stream, root)
        manifest = _validated_manifest(root, version=POSTGRES_MANIFEST_VERSION)
        if manifest.get("kind") != "postgresql":
            raise BackupError("backup manifest is not a PostgreSQL archive")
        if manifest.get("consistency") != "quiesced":
            raise BackupError("PostgreSQL backup does not prove quiesced writer consistency")
        database = manifest.get("database")
        if not isinstance(database, dict) or database.get("archive") != POSTGRES_DUMP_NAME:
            raise BackupError("PostgreSQL backup manifest is missing its custom dump")
        dump = root / POSTGRES_DUMP_NAME
        if database.get("format") != "custom" or database.get("sha256") != _sha256(dump):
            raise BackupError("PostgreSQL dump metadata checksum mismatch")
        if target_data_dir.exists() and any(target_data_dir.iterdir()):
            raise BackupError("target data directory must be empty before restore")
        empty_sql = (
            "SELECT (SELECT count(*) FROM pg_class c JOIN pg_namespace n "
            "ON n.oid=c.relnamespace WHERE n.nspname NOT IN "
            "('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' "
            "AND c.relkind IN ('r','p','v','m','S','f')) + "
            "(SELECT count(*) FROM pg_extension WHERE extname <> 'plpgsql')"
        )
        object_count = _run_native(
            [psql, "--no-password", "--tuples-only", "--no-align", "--command", empty_sql],
            env=env,
        )
        try:
            parsed_count = int(object_count.strip())
        except ValueError as exc:
            raise BackupError("could not verify that target database is empty") from exc
        if parsed_count != 0:
            raise BackupError("target database must be empty before restore")
        _run_native(
            [
                pg_restore,
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--no-password",
                "--dbname",
                str(target.database),
                str(dump),
            ],
            env=env,
        )
        target_data_dir.mkdir(parents=True, exist_ok=True)
        for name in BACKUP_DIRS:
            source_path = root / name
            if source_path.exists():
                _copy_tree(source_path, target_data_dir / name)
        members = manifest.get("members")
        assert isinstance(members, list)
        for entry in members:
            assert isinstance(entry, dict)
            relative = str(entry.get("path") or "")
            if relative.split("/", 1)[0] not in BACKUP_DIRS:
                continue
            restored = (target_data_dir / relative).resolve()
            if (
                target_data_dir not in restored.parents
                or not restored.is_file()
                or _sha256(restored) != entry.get("sha256")
            ):
                raise BackupError(f"restored member checksum mismatch: {relative}")
    return str(target.database)


def _settings_with_data_dir(path: str | None) -> Settings:
    settings = load_settings()
    return replace(settings, data_dir=Path(path).expanduser()) if path else settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.services.backup")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup = subparsers.add_parser("backup", help="create a checksummed archive")
    backup.add_argument("archive", type=Path)
    backup.add_argument("--data-dir")
    backup.add_argument(
        "--quiesced",
        action="store_true",
        help="confirm API/worker/scheduler/relay writers are stopped",
    )
    restore = subparsers.add_parser("restore", help="validate and restore an archive")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--data-dir")
    restore.add_argument("--force", action="store_true")
    restore.add_argument(
        "--target-database-url",
        default=os.environ.get("PG_RESTORE_TARGET_DATABASE_URL", ""),
    )
    args = parser.parse_args()
    settings = load_settings()
    result: Path | str
    if args.command == "backup":
        settings = _settings_with_data_dir(args.data_dir)
        result = (
            create_postgres_backup(settings, args.archive, quiesced=args.quiesced)
            if settings.database_backend == "postgresql"
            else create_backup(settings, args.archive)
        )
    elif settings.database_backend == "postgresql":
        if not args.target_database_url:
            raise BackupError(
                "PostgreSQL restore requires --target-database-url or "
                "PG_RESTORE_TARGET_DATABASE_URL"
            )
        if not args.data_dir:
            raise BackupError("PostgreSQL restore requires an explicit --data-dir target")
        result = restore_postgres_backup(
            settings,
            args.archive,
            target_database_url=args.target_database_url,
            target_data_dir=Path(args.data_dir),
        )
    else:
        settings = _settings_with_data_dir(args.data_dir)
        result = restore_backup(settings, args.archive, force=args.force)
    print(result)


if __name__ == "__main__":
    main()
