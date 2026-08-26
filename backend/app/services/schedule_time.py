"""Cron validation and timezone-aware schedule calculations."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter


def validate_cron_expression(expression: str) -> str:
    normalized = " ".join(expression.split())
    if len(normalized.split(" ")) != 5 or not croniter.is_valid(normalized):
        raise ValueError("cron_expression must be a valid five-field cron expression")
    return normalized


def validate_timezone(value: str) -> str:
    normalized = value.strip()
    try:
        ZoneInfo(normalized)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("timezone must be a valid IANA timezone") from exc
    return normalized


def next_cron_run(expression: str, timezone: str, after: datetime) -> datetime:
    """Return the first cron occurrence after ``after`` as an aware UTC datetime."""
    zone = ZoneInfo(validate_timezone(timezone))
    base = after if after.tzinfo is not None else after.replace(tzinfo=UTC)
    local_next = croniter(validate_cron_expression(expression), base.astimezone(zone)).get_next(
        datetime
    )
    if local_next.tzinfo is None:
        local_next = local_next.replace(tzinfo=zone)
    return local_next.astimezone(UTC)


__all__ = ["next_cron_run", "validate_cron_expression", "validate_timezone"]
