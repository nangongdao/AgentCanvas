"""Migration and database constraints for workflow review aggregates."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.core.actor import ActorIdentity
from app.core.config import BACKEND_DIR, Settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import CURRENT_REVISION, upgrade_database
from app.db.repositories import (
    WorkflowCommentRepo,
    WorkflowRepo,
    WorkflowReviewRepo,
    WorkflowVersionRepo,
)

ACTOR = ActorIdentity(key="test:actor", subject="Test actor", user_id=None)
DSL = {"version": "1.0", "name": "Review", "nodes": [], "edges": []}


def _alembic_config(settings: Settings) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.effective_database_url)
    return config


async def _schema(settings: Settings) -> tuple[set[str], str | None]:
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
            version = None
            if "alembic_version" in tables:
                result = await connection.exec_driver_sql("SELECT version_num FROM alembic_version")
                version = result.scalar_one()
            return tables, version
    finally:
        await engine.dispose()


async def test_workflow_review_migration_downgrades_and_reapplies(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    config = _alembic_config(settings)
    await upgrade_database(settings)

    tables, version = await _schema(settings)
    assert version == CURRENT_REVISION
    assert {"workflow_comments", "workflow_reviews", "audit_logs"} <= tables

    await asyncio.to_thread(command.downgrade, config, "0016_identity_hardening")
    tables, version = await _schema(settings)
    assert version == "0016_identity_hardening"
    assert "workflow_comments" not in tables
    assert "workflow_reviews" not in tables
    assert "audit_logs" not in tables

    await upgrade_database(settings)
    tables, version = await _schema(settings)
    assert version == CURRENT_REVISION
    assert {"workflow_comments", "workflow_reviews", "audit_logs"} <= tables


async def test_database_rejects_cross_workflow_versions_and_parent_comments(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    try:
        async with sessions() as setup:
            first = await WorkflowRepo(setup).create("First", {**DSL, "name": "First"})
            second = await WorkflowRepo(setup).create("Second", {**DSL, "name": "Second"})
            first_version = await WorkflowVersionRepo(setup).get_by_number(first.id, 1)
            second_version = await WorkflowVersionRepo(setup).get_by_number(second.id, 1)
            assert first_version is not None and second_version is not None
            parent = await WorkflowCommentRepo(setup).create(
                workflow_id=first.id,
                version_id=first_version.id,
                body="Parent",
                author=ACTOR,
            )
            first_id = first.id
            second_id = second.id
            first_version_id = first_version.id
            second_version_id = second_version.id
            parent_id = parent.id
            await setup.commit()

        async with sessions() as mismatch_comment:
            with pytest.raises(IntegrityError):
                await WorkflowCommentRepo(mismatch_comment).create(
                    workflow_id=first_id,
                    version_id=second_version_id,
                    body="Wrong version aggregate",
                    author=ACTOR,
                )

        async with sessions() as mismatch_review:
            with pytest.raises(IntegrityError):
                await WorkflowReviewRepo(mismatch_review).create(
                    workflow_id=first_id,
                    version_id=second_version_id,
                    summary="Wrong version aggregate",
                    requester=ACTOR,
                )

        async with sessions() as mismatch_parent:
            with pytest.raises(IntegrityError):
                await WorkflowCommentRepo(mismatch_parent).create(
                    workflow_id=second_id,
                    version_id=second_version_id,
                    parent_comment_id=parent_id,
                    body="Wrong parent aggregate",
                    author=ACTOR,
                )

        async with sessions() as valid_child:
            child = await WorkflowCommentRepo(valid_child).create(
                workflow_id=first_id,
                version_id=first_version_id,
                parent_comment_id=parent_id,
                body="Valid child",
                author=ACTOR,
            )
            assert child.parent_comment_id == parent_id
    finally:
        await engine.dispose()
