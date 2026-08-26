"""Optimistic workflow writes remain atomic across database sessions."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.repositories import WorkflowRepo, WorkflowVersionRepo


async def test_stale_workflow_update_fails_before_creating_duplicate_snapshot(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    try:
        async with sessions() as setup:
            workflow = await WorkflowRepo(setup).create(
                "Base",
                {"version": "1.0", "name": "Base", "nodes": [], "edges": []},
            )
            workflow_id = workflow.id
            await setup.commit()

        async with sessions() as first, sessions() as second:
            first_row = await WorkflowRepo(first).get(workflow_id)
            second_row = await WorkflowRepo(second).get(workflow_id)
            assert first_row is not None and second_row is not None
            assert first_row.version == second_row.version == 1

            await WorkflowRepo(first).update(
                first_row,
                name="First writer",
                expected_version=1,
            )
            await first.commit()

            with pytest.raises(ValueError, match="expected 1, got 2"):
                await WorkflowRepo(second).update(
                    second_row,
                    name="Stale writer",
                    expected_version=1,
                )
            await second.rollback()

        async with sessions() as verify:
            persisted = await WorkflowRepo(verify).get(workflow_id)
            versions = await WorkflowVersionRepo(verify).list_for_workflow(workflow_id)
            assert persisted is not None
            assert persisted.name == "First writer"
            assert persisted.version == 2
            assert [version.number for version in versions] == [2, 1]
    finally:
        await engine.dispose()
