"""PostgreSQL restore-drill inventory comparison contracts."""

from __future__ import annotations

from dataclasses import replace

from app.db.migrations import CURRENT_REVISION
from app.services.restore_drill import DatabaseInventory, compare_inventories


def _inventory() -> DatabaseInventory:
    return DatabaseInventory(
        revision=CURRENT_REVISION,
        table_counts={"executions": 3, "execution_events": 12, "document_chunks": 2},
        checkpoint_counts={
            "checkpoint_migrations": 1,
            "checkpoints": 1,
            "checkpoint_blobs": 1,
            "checkpoint_writes": 2,
        },
        waiting_approval_ids=("approval-1",),
        waiting_checkpoint_threads=("approval-1",),
        vector_extension_version="0.8.1",
        vector_probe_distance="0",
        vector_probe_top_id="chunk-1",
    )


def test_restore_drill_accepts_matching_inventory() -> None:
    inventory = _inventory()
    assert compare_inventories(inventory, inventory) == []


def test_restore_drill_reports_state_and_vector_probe_mismatches() -> None:
    source = _inventory()
    target = replace(
        source,
        table_counts={**source.table_counts, "execution_events": 11},
        waiting_checkpoint_threads=(),
        vector_probe_distance=None,
    )
    mismatches = compare_inventories(source, target)
    assert any("table_counts" in mismatch for mismatch in mismatches)
    assert any("waiting_checkpoint_threads" in mismatch for mismatch in mismatches)
    assert any("pgvector retrieval probe" in mismatch for mismatch in mismatches)
