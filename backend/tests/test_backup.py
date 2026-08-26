"""Backup/restore integrity and safety tests for the U2 release workflow."""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services import backup as backup_service
from app.services.backup import (
    BackupError,
    create_backup,
    create_postgres_backup,
    restore_backup,
    restore_postgres_backup,
)


def _database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS probe (value TEXT NOT NULL)")
        connection.execute("DELETE FROM probe")
        connection.execute("INSERT INTO probe VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def _value(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return str(connection.execute("SELECT value FROM probe").fetchone()[0])
    finally:
        connection.close()


def test_backup_restore_round_trip_preserves_all_single_host_state(tmp_path: Path) -> None:
    data = tmp_path / "data"
    redis = tmp_path / "redis-aof"
    settings = Settings(data_dir=data, redis_aof_dir=redis)
    _database(data / "app.db", "workflow-state")
    _database(data / "checkpoints.db", "approval-state")
    for path, content in (
        (data / "chroma" / "vectors.bin", b"vectors"),
        (data / "uploads" / "handbook.md", b"upload"),
        (data / "workspace" / "tool.txt", b"workspace"),
        (redis / "appendonly.aof.1.incr.aof", b"redis-aof"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    archive = create_backup(settings, tmp_path / "backups" / "snapshot.tar.gz")
    _database(data / "app.db", "mutated")
    (data / "uploads" / "handbook.md").write_bytes(b"mutated")
    (redis / "appendonly.aof.1.incr.aof").write_bytes(b"mutated")
    restored_data = tmp_path / "restored-data"
    restored_redis = tmp_path / "restored-redis"
    restored_settings = Settings(
        data_dir=restored_data,
        redis_aof_dir=restored_redis,
    )

    restored = restore_backup(restored_settings, archive, force=True)

    assert restored == restored_data.resolve()
    assert _value(restored_data / "app.db") == "workflow-state"
    assert _value(restored_data / "checkpoints.db") == "approval-state"
    assert (restored_data / "chroma" / "vectors.bin").read_bytes() == b"vectors"
    assert (restored_data / "uploads" / "handbook.md").read_bytes() == b"upload"
    assert (restored_data / "workspace" / "tool.txt").read_bytes() == b"workspace"
    assert (restored_redis / "appendonly.aof.1.incr.aof").read_bytes() == b"redis-aof"


def test_restore_requires_explicit_force(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    _database(settings.data_dir / "app.db", "state")
    archive = create_backup(settings, tmp_path / "snapshot.tar.gz")

    with pytest.raises(BackupError, match="force=True"):
        restore_backup(settings, archive)


def test_restore_rejects_archive_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    payload = b"outside"
    with tarfile.open(archive, "w:gz") as stream:
        member = tarfile.TarInfo("../outside.txt")
        member.size = len(payload)
        stream.addfile(member, io.BytesIO(payload))

    with pytest.raises(BackupError, match="escapes root"):
        restore_backup(Settings(data_dir=tmp_path / "data"), archive, force=True)
    assert not (tmp_path / "outside.txt").exists()


def test_backup_rejects_non_sqlite_database(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url="postgresql+asyncpg://db/agentcanvas",
    )
    with pytest.raises(BackupError, match="supports local SQLite only"):
        create_backup(settings, tmp_path / "snapshot.tar.gz")


def _fake_postgres_tools(monkeypatch, *, object_count: str = "0"):
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_find(name: str) -> str:
        return name

    def fake_run(args: list[str], *, env: dict[str, str]) -> str:
        calls.append((list(args), dict(env)))
        if args[0] == "pg_dump" and "--version" in args:
            return "pg_dump (PostgreSQL) 17.6"
        if args[0] == "pg_dump":
            destination = Path(args[args.index("--file") + 1])
            destination.write_bytes(b"PGDMP\x01test-custom-archive")
            return ""
        if args[0] == "psql":
            return object_count
        if args[0] == "pg_restore":
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(backup_service, "_find_tool", fake_find)
    monkeypatch.setattr(backup_service, "_run_native", fake_run)
    return calls


def _postgres_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        database_url="postgresql+asyncpg://backup_user:secret@db:5432/agentcanvas",
    )


def test_postgres_backup_uses_custom_dump_and_checksummed_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    calls = _fake_postgres_tools(monkeypatch)
    settings = _postgres_settings(tmp_path)
    (settings.uploads_dir / "source.txt").parent.mkdir(parents=True)
    (settings.uploads_dir / "source.txt").write_text("source", encoding="utf-8")

    archive = create_postgres_backup(
        settings, tmp_path / "postgres.tar.gz", quiesced=True
    )

    with tarfile.open(archive, "r:gz") as stream:
        manifest_file = stream.extractfile("manifest.json")
        dump_file = stream.extractfile("database.dump")
        assert manifest_file is not None
        assert dump_file is not None
        manifest = json.loads(manifest_file.read())
        dump = dump_file.read()
    assert manifest["version"] == 2
    assert manifest["kind"] == "postgresql"
    assert manifest["consistency"] == "quiesced"
    assert manifest["database"]["format"] == "custom"
    assert manifest["database"]["source_database"] == "agentcanvas"
    assert manifest["database"]["size_bytes"] == len(dump)
    dump_call = next(args for args, _env in calls if "--format=custom" in args)
    assert "--no-owner" in dump_call
    assert "--no-privileges" in dump_call
    assert all("secret" not in argument for argument in dump_call)
    dump_env = next(env for args, env in calls if "--format=custom" in args)
    assert dump_env["PGPASSWORD"] == "secret"


def test_postgres_backup_requires_quiesced_writers(tmp_path: Path, monkeypatch) -> None:
    _fake_postgres_tools(monkeypatch)
    settings = _postgres_settings(tmp_path)

    with pytest.raises(BackupError, match="quiesced"):
        create_postgres_backup(settings, tmp_path / "unsafe-postgres.tar.gz")


def test_postgres_restore_requires_distinct_empty_target(tmp_path: Path, monkeypatch) -> None:
    calls = _fake_postgres_tools(monkeypatch)
    settings = _postgres_settings(tmp_path)
    (settings.uploads_dir / "source.txt").parent.mkdir(parents=True)
    (settings.uploads_dir / "source.txt").write_text("source", encoding="utf-8")
    archive = create_postgres_backup(
        settings, tmp_path / "postgres.tar.gz", quiesced=True
    )
    calls.clear()
    target_data = tmp_path / "restored-data"

    restored = restore_postgres_backup(
        settings,
        archive,
        target_database_url=(
            "postgresql+asyncpg://restore_user:target-secret@db:5432/agentcanvas_restore"
        ),
        target_data_dir=target_data,
    )

    assert restored == "agentcanvas_restore"
    assert [args[0] for args, _env in calls] == ["psql", "pg_restore"]
    restore_args, restore_env = calls[-1]
    assert "--exit-on-error" in restore_args
    assert "--clean" not in restore_args
    assert "target-secret" not in " ".join(restore_args)
    assert restore_env["PGPASSWORD"] == "target-secret"
    assert (target_data / "uploads" / "source.txt").read_text(encoding="utf-8") == "source"

    with pytest.raises(BackupError, match="must differ"):
        restore_postgres_backup(
            settings,
            archive,
            target_database_url=settings.database_url,
            target_data_dir=tmp_path / "another-data",
        )


def test_postgres_restore_refuses_nonempty_target(tmp_path: Path, monkeypatch) -> None:
    settings = _postgres_settings(tmp_path)
    _fake_postgres_tools(monkeypatch)
    archive = create_postgres_backup(
        settings, tmp_path / "postgres.tar.gz", quiesced=True
    )
    calls = _fake_postgres_tools(monkeypatch, object_count="3")

    with pytest.raises(BackupError, match="must be empty"):
        restore_postgres_backup(
            settings,
            archive,
            target_database_url="postgresql+asyncpg://db/agentcanvas_restore",
            target_data_dir=tmp_path / "restored-data",
        )
    assert [args[0] for args, _env in calls] == ["psql"]


def test_postgres_restore_refuses_source_or_nonempty_data_dir(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _postgres_settings(tmp_path)
    _fake_postgres_tools(monkeypatch)
    archive = create_postgres_backup(
        settings, tmp_path / "postgres.tar.gz", quiesced=True
    )
    target_url = "postgresql+asyncpg://db/agentcanvas_restore"

    with pytest.raises(BackupError, match="must differ"):
        restore_postgres_backup(
            settings,
            archive,
            target_database_url=target_url,
            target_data_dir=settings.data_dir,
        )
    with pytest.raises(BackupError, match="must differ"):
        restore_postgres_backup(
            settings,
            archive,
            target_database_url=target_url,
            target_data_dir=settings.data_dir / "nested-restore",
        )

    target_data = tmp_path / "occupied-data"
    target_data.mkdir()
    (target_data / "keep.txt").write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(BackupError, match="must be empty"):
        restore_postgres_backup(
            settings,
            archive,
            target_database_url=target_url,
            target_data_dir=target_data,
        )


def test_postgres_restore_verifies_copied_file_members(tmp_path: Path, monkeypatch) -> None:
    settings = _postgres_settings(tmp_path)
    _fake_postgres_tools(monkeypatch)
    (settings.uploads_dir / "source.txt").parent.mkdir(parents=True)
    (settings.uploads_dir / "source.txt").write_text("source", encoding="utf-8")
    archive = create_postgres_backup(
        settings, tmp_path / "postgres.tar.gz", quiesced=True
    )
    original_copy = backup_service._copy_tree

    def corrupt_copy(source: Path, destination: Path) -> None:
        original_copy(source, destination)
        if destination.name == "uploads":
            (destination / "source.txt").write_text("corrupted", encoding="utf-8")

    monkeypatch.setattr(backup_service, "_copy_tree", corrupt_copy)
    with pytest.raises(BackupError, match="restored member checksum mismatch"):
        restore_postgres_backup(
            settings,
            archive,
            target_database_url="postgresql+asyncpg://db/agentcanvas_restore",
            target_data_dir=tmp_path / "restored-data",
        )
