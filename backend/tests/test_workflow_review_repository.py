"""Repository invariants for durable workflow review transitions."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.actor import ActorIdentity
from app.core.config import Settings
from app.core.workflow_reviews import ReviewDecision
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.repositories import WorkflowRepo, WorkflowReviewRepo, WorkflowVersionRepo


async def test_stale_review_decision_cannot_rewrite_terminal_status(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    try:
        async with sessions() as setup:
            workflow = await WorkflowRepo(setup).create(
                "Review target",
                {
                    "version": "1.0",
                    "name": "Review target",
                    "nodes": [],
                    "edges": [],
                },
            )
            version = await WorkflowVersionRepo(setup).get_by_number(workflow.id, 1)
            assert version is not None
            review = await WorkflowReviewRepo(setup).create(
                workflow_id=workflow.id,
                version_id=version.id,
                summary="Ready",
                requester=ActorIdentity(
                    key="user:requester",
                    subject="Requester",
                    user_id=None,
                ),
            )
            workflow_id = workflow.id
            review_id = review.id
            await setup.commit()

        async with sessions() as first, sessions() as second:
            first_row = await WorkflowReviewRepo(first).get_for_workflow(workflow_id, review_id)
            second_row = await WorkflowReviewRepo(second).get_for_workflow(workflow_id, review_id)
            assert first_row is not None and second_row is not None
            assert first_row.status == second_row.status == "open"

            await WorkflowReviewRepo(first).decide(
                first_row,
                decision=ReviewDecision.APPROVED,
                summary="Approved",
                actor=ActorIdentity(
                    key="user:reviewer-one",
                    subject="Reviewer one",
                    user_id=None,
                ),
            )
            await first.commit()

            with pytest.raises(ValueError, match="terminal decision"):
                await WorkflowReviewRepo(second).decide(
                    second_row,
                    decision=ReviewDecision.CHANGES_REQUESTED,
                    summary="Too late",
                    actor=ActorIdentity(
                        key="user:reviewer-two",
                        subject="Reviewer two",
                        user_id=None,
                    ),
                )
            await second.rollback()

        async with sessions() as verify:
            persisted = await WorkflowReviewRepo(verify).get_for_workflow(workflow_id, review_id)
            assert persisted is not None
            assert persisted.status == "approved"
            assert persisted.decided_by_subject == "Reviewer one"
    finally:
        await engine.dispose()
