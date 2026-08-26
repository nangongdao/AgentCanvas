"""Release migration coverage for the v0.x SQLite relational store."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import Table, select

from app.core.config import Settings
from app.db.base import create_engine
from app.db.migrations import upgrade_database
from app.db.models import Execution, RefreshToken, Workflow, WorkflowComment, WorkflowVersion
from app.services.migrate_sqlite import _copy_application_data, _ordered_rows


def test_self_references_are_ordered_parent_first() -> None:
    rows: list[dict[str, Any]] = [
        {"id": "child", "parent_execution_id": "parent"},
        {"id": "parent", "parent_execution_id": None},
    ]

    ordered = _ordered_rows(cast(Table, Execution.__table__), rows)

    assert [row["id"] for row in ordered] == ["parent", "child"]

    refresh_rows: list[dict[str, Any]] = [
        {"id": "old-token", "replaced_by_id": "new-token"},
        {"id": "new-token", "replaced_by_id": None},
    ]
    ordered_refresh = _ordered_rows(cast(Table, RefreshToken.__table__), refresh_rows)
    assert [row["id"] for row in ordered_refresh] == ["new-token", "old-token"]

    with pytest.raises(RuntimeError, match="missing or cyclic"):
        _ordered_rows(
            cast(Table, WorkflowComment.__table__),
            [{"id": "orphan", "parent_comment_id": "missing"}],
        )


async def test_relational_copy_requires_empty_current_head_target(tmp_path: Path) -> None:
    source_settings = Settings(data_dir=tmp_path / "source")
    target_settings = Settings(data_dir=tmp_path / "target")
    await upgrade_database(source_settings)
    await upgrade_database(target_settings)

    source_engine = create_engine(source_settings)
    workflow_table = cast(Table, Workflow.__table__)
    version_table = cast(Table, WorkflowVersion.__table__)
    try:
        async with source_engine.begin() as connection:
            await connection.execute(
                workflow_table.insert(),
                {
                    "id": "workflow-1",
                    "name": "Migrated workflow",
                    "description": "",
                    "dsl_json": {"version": "1.0", "nodes": [], "edges": []},
                    "version": 1,
                    "is_archived": False,
                    "project_id": None,
                },
            )
            await connection.execute(
                version_table.insert(),
                {
                    "id": "version-1",
                    "workflow_id": "workflow-1",
                    "number": 1,
                    "status": "published",
                    "name": "Migrated workflow",
                    "description": "",
                    "dsl_json": {"version": "1.0", "nodes": [], "edges": []},
                    "change_summary": "legacy import",
                },
            )
    finally:
        await source_engine.dispose()

    result = await _copy_application_data(source_settings, target_settings)

    assert result.table_counts["workflows"] == 1
    assert result.table_counts["workflow_versions"] == 1
    target_engine = create_engine(target_settings)
    try:
        async with target_engine.connect() as connection:
            names = list((await connection.execute(select(Workflow.name))).scalars())
        assert names == ["Migrated workflow"]
    finally:
        await target_engine.dispose()

    with pytest.raises(RuntimeError, match="must be empty"):
        await _copy_application_data(source_settings, target_settings)
