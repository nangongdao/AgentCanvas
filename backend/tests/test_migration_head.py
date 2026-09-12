"""Regression: the declared migration head must match the alembic graph.

A stale ``CURRENT_REVISION`` disables ``/readyz``, the scheduler, and the
event relay on any database already at head. The dedicated CI step runs the
same check; this test keeps the failure inside the normal suite too.
"""

import pytest

from scripts import check_migration_head
from scripts.check_migration_head import evaluate


def test_current_revision_matches_alembic_head() -> None:
    ok, message = evaluate()
    assert ok, message


def test_multiple_alembic_heads_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check_migration_head, "alembic_heads", lambda: ("0047_a", "0047_b"))
    ok, message = evaluate()
    assert not ok
    assert "multiple heads" in message


def test_stale_current_revision_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check_migration_head, "alembic_heads", lambda: ("0047_old",))
    ok, message = evaluate()
    assert not ok
    assert "CURRENT_REVISION is '0047_evaluation_policy'" in message
    assert "0047_old" in message


def test_single_matching_head_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check_migration_head, "alembic_heads", lambda: ("0047_evaluation_policy",))
    ok, message = evaluate()
    assert ok
    assert "parity ok" in message
