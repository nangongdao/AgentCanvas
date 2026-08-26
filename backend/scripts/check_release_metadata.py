"""Validate the dated changelog metadata required by a tagged release."""

from __future__ import annotations

import argparse
import calendar
import re
from datetime import date
from pathlib import Path

_HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\] - (?P<release_date>\d{4}-\d{2}-\d{2})$")
_SUPPORT = re.compile(r"^Support through: (?P<support_date>\d{4}-\d{2}-\d{2})$")


def _six_months_later(value: date) -> date:
    month_index = value.year * 12 + value.month - 1 + 6
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def validate_changelog(path: Path, version: str) -> tuple[date, date]:
    """Return release/support dates or raise ``ValueError`` for invalid metadata."""
    lines = path.read_text(encoding="utf-8").splitlines()
    heading = next((match for line in lines if (match := _HEADING.match(line))), None)
    if heading is None or heading.group("version") != version:
        raise ValueError(f"changelog has no dated heading for [{version}]")
    release_date = date.fromisoformat(heading.group("release_date"))

    support = next((match for line in lines if (match := _SUPPORT.match(line))), None)
    if support is None:
        raise ValueError("changelog is missing 'Support through: YYYY-MM-DD'")
    support_date = date.fromisoformat(support.group("support_date"))
    expected = _six_months_later(release_date)
    if support_date != expected:
        raise ValueError(
            f"support date {support_date.isoformat()} does not equal six months "
            f"after release date {release_date.isoformat()} ({expected.isoformat()})"
        )
    return release_date, support_date


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    release_date, support_date = validate_changelog(args.changelog, args.version)
    print(
        f"{args.version}: released {release_date.isoformat()}, "
        f"supported through {support_date.isoformat()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
