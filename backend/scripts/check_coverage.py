"""Enforce AgentCanvas's tiered line and critical-branch coverage gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

FULL_LINE_MIN = 75.0
CRITICAL_BRANCH_MIN = 80.0
CRITICAL_FILES = frozenset({"app/core/security.py", "app/engine/events.py"})
CRITICAL_PREFIXES = ("app/mcphub/", "app/rag/")


def _percent(covered: int, total: int) -> float:
    return 100.0 if total == 0 else covered * 100.0 / total


def _is_critical(filename: str) -> bool:
    normalized = filename.replace("\\", "/")
    return normalized in CRITICAL_FILES or normalized.startswith(CRITICAL_PREFIXES)


def evaluate(payload: dict[str, Any]) -> tuple[float, float]:
    totals = payload["totals"]
    full_line = _percent(int(totals["covered_lines"]), int(totals["num_statements"]))

    covered_branches = 0
    total_branches = 0
    for filename, details in payload["files"].items():
        if not _is_critical(str(filename)):
            continue
        summary = details["summary"]
        covered_branches += int(summary["covered_branches"])
        total_branches += int(summary["num_branches"])
    if total_branches == 0:
        raise ValueError("coverage report contains no critical-module branches")
    return full_line, _percent(covered_branches, total_branches)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path, nargs="?", default=Path("coverage.json"))
    args = parser.parse_args()
    payload = json.loads(args.report.read_text(encoding="utf-8"))
    full_line, critical_branch = evaluate(payload)
    print(f"full application line coverage: {full_line:.2f}% (required {FULL_LINE_MIN:.0f}%)")
    print(
        "critical module branch coverage: "
        f"{critical_branch:.2f}% (required {CRITICAL_BRANCH_MIN:.0f}%)"
    )
    return int(full_line < FULL_LINE_MIN or critical_branch < CRITICAL_BRANCH_MIN)


if __name__ == "__main__":
    raise SystemExit(main())
