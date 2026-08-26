"""Release changelog date validation tests."""

from __future__ import annotations

from datetime import date

import pytest

from scripts.check_release_metadata import _six_months_later, validate_changelog


def test_six_months_later_preserves_day_when_possible() -> None:
    assert _six_months_later(date(2026, 8, 12)) == date(2027, 2, 12)


def test_six_months_later_clamps_short_month() -> None:
    assert _six_months_later(date(2026, 8, 31)) == date(2027, 2, 28)


def test_validate_changelog_requires_exact_support_date(tmp_path) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "## [1.0.0] - 2026-08-12\n\nSupport through: 2027-02-12\n",
        encoding="utf-8",
    )
    assert validate_changelog(path, "1.0.0") == (date(2026, 8, 12), date(2027, 2, 12))

    path.write_text(
        "## [1.0.0] - 2026-08-12\n\nSupport through: 2027-02-11\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not equal six months"):
        validate_changelog(path, "1.0.0")
