"""Reusable, stable keyset pagination for SQLAlchemy repositories."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

SortOrder = Literal["asc", "desc"]


class PaginationError(ValueError):
    """The caller supplied a malformed or incompatible cursor."""


@dataclass(frozen=True, slots=True)
class CursorPosition:
    value: object
    row_id: str


@dataclass(frozen=True, slots=True)
class PageSpec:
    limit: int
    sort: str
    order: SortOrder
    search: str | None = None
    scope: str | None = None
    position: CursorPosition | None = None


@dataclass(frozen=True, slots=True)
class PageSlice[RowT]:
    rows: list[RowT]
    next_cursor: str | None
    has_more: bool


async def paginate_select[RowT](
    session: AsyncSession,
    statement: Any,
    *,
    id_column: Any,
    columns: Mapping[str, Any],
    spec: PageSpec,
    search_columns: Sequence[Any] = (),
) -> PageSlice[RowT]:
    """Apply a bounded search and stable `(sort, id)` keyset to a query."""
    sort_column = columns[spec.sort]

    if spec.search and search_columns:
        pattern = f"%{_escape_like(spec.search)}%"
        statement = statement.where(
            or_(*(column.ilike(pattern, escape="\\") for column in search_columns))
        )

    if spec.position is not None:
        cursor_value = _coerce_value(spec.position.value, sort_column)
        if spec.sort == "id":
            boundary = (
                sort_column > cursor_value if spec.order == "asc" else sort_column < cursor_value
            )
        elif spec.order == "asc":
            boundary = or_(
                sort_column > cursor_value,
                and_(
                    sort_column == cursor_value,
                    id_column > spec.position.row_id,
                ),
            )
        else:
            boundary = or_(
                sort_column < cursor_value,
                and_(
                    sort_column == cursor_value,
                    id_column < spec.position.row_id,
                ),
            )
        statement = statement.where(boundary)

    direction = "asc" if spec.order == "asc" else "desc"
    order_by = getattr(sort_column, direction)()
    statement = statement.order_by(order_by)
    if spec.sort != "id":
        statement = statement.order_by(getattr(id_column, direction)())
    result = await session.execute(statement.limit(spec.limit + 1))
    fetched = list(result.scalars().all())
    has_more = len(fetched) > spec.limit
    rows = fetched[: spec.limit]
    next_token = None
    if has_more and rows:
        last = rows[-1]
        next_token = encode_cursor(
            sort=spec.sort,
            order=spec.order,
            search=spec.search,
            scope=spec.scope,
            value=getattr(last, spec.sort),
            row_id=str(getattr(last, id_column.key)),
        )
    return PageSlice(rows=rows, next_cursor=next_token, has_more=has_more)


def encode_cursor(
    *,
    sort: str,
    order: SortOrder,
    search: str | None,
    scope: str | None,
    value: object,
    row_id: str,
) -> str:
    payload = {
        "v": 1,
        "sort": sort,
        "order": order,
        "search": search,
        "scope": scope,
        "value": value.isoformat() if isinstance(value, datetime) else value,
        "id": row_id,
    }
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(
    token: str | None,
    *,
    sort: str,
    order: SortOrder,
    search: str | None,
    scope: str | None,
) -> CursorPosition | None:
    if token is None:
        return None
    try:
        padding = "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(token + padding))
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError
        if payload.get("sort") != sort or payload.get("order") != order:
            raise PaginationError("cursor does not match the requested sort order")
        if payload.get("search") != search:
            raise PaginationError("cursor does not match the requested search")
        if payload.get("scope") != scope:
            raise PaginationError("cursor does not match the requested filter")
        row_id = payload.get("id")
        if not isinstance(row_id, str) or not row_id or "value" not in payload:
            raise ValueError
        return CursorPosition(value=payload["value"], row_id=row_id)
    except PaginationError:
        raise
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise PaginationError("invalid pagination cursor") from exc


def _coerce_value(value: object, column: Any) -> object:
    try:
        python_type = column.type.python_type
        if python_type is datetime:
            if not isinstance(value, str):
                raise ValueError
            return datetime.fromisoformat(value)
        return python_type(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise PaginationError("pagination cursor contains an invalid sort value") from exc


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
