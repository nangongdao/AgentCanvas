"""Coverage gate calculations use distinct line and branch metrics."""

from __future__ import annotations

import pytest

from scripts.check_coverage import evaluate


def test_coverage_gate_aggregates_only_critical_branches() -> None:
    payload = {
        "totals": {"covered_lines": 75, "num_statements": 100},
        "files": {
            "app\\engine\\events.py": {
                "summary": {"covered_branches": 8, "num_branches": 10}
            },
            "app/rag/service.py": {
                "summary": {"covered_branches": 16, "num_branches": 20}
            },
            "app/api/router.py": {
                "summary": {"covered_branches": 0, "num_branches": 100}
            },
        },
    }

    assert evaluate(payload) == (75.0, 80.0)


def test_coverage_gate_requires_critical_branch_data() -> None:
    payload = {
        "totals": {"covered_lines": 1, "num_statements": 1},
        "files": {
            "app/api/router.py": {
                "summary": {"covered_branches": 0, "num_branches": 0}
            }
        },
    }

    with pytest.raises(ValueError, match="no critical-module branches"):
        evaluate(payload)
