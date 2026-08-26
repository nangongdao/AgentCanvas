"""Safe condition rule evaluator (no eval)."""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

from app.engine.templates import render_value
from app.schemas.dsl import ConditionGroup, ConditionRule

logger = logging.getLogger(__name__)


class _BranchConfig(Protocol):
    """Structural type for any config exposing ordered branches + a default."""

    @property
    def branches(self) -> list[Any]: ...

    @property
    def default_branch(self) -> str: ...


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, list, dict, tuple)):
        return len(value) == 0
    return False


def eval_rule(rule: ConditionRule, ctx: dict[str, Any]) -> bool:
    """Evaluate a single rule; left side is template-rendered against ctx."""
    left = render_value(rule.left, ctx)
    right = render_value(rule.right, ctx) if isinstance(rule.right, str) else rule.right
    op = rule.operator

    if op == "is_empty":
        return _is_empty(left)
    if op == "not_empty":
        return not _is_empty(left)

    if op in ("eq", "ne"):
        # Numeric compare when both coercible, else string compare
        ln, rn = _to_number(left), _to_number(right)
        equal = ln == rn if ln is not None and rn is not None else str(left) == str(right)
        return equal if op == "eq" else not equal

    if op in ("gt", "gte", "lt", "lte"):
        ln, rn = _to_number(left), _to_number(right)
        if ln is None or rn is None:
            return False
        if op == "gt":
            return ln > rn
        if op == "gte":
            return ln >= rn
        if op == "lt":
            return ln < rn
        return ln <= rn

    if op in ("contains", "not_contains"):
        try:
            found = (
                right in left
                if isinstance(left, (list, tuple))
                else str(right) in str(left)
            )
        except TypeError:
            found = False
        return found if op == "contains" else not found

    if op == "regex":
        try:
            return re.search(str(right), str(left)) is not None
        except re.error:
            logger.warning("invalid regex in condition: %r", right)
            return False

    logger.warning("unknown operator: %s", op)
    return False


def eval_group(group: ConditionGroup, ctx: dict[str, Any]) -> bool:
    if not group.rules:
        return False
    results = (eval_rule(r, ctx) for r in group.rules)
    return all(results) if group.op == "and" else any(results)


def pick_branch(cfg: _BranchConfig, ctx: dict[str, Any]) -> str:
    """Return the id of the first matching branch, else the default branch.

    Works with any config exposing ``branches`` (list of objects with ``id``
    and ``group``) and ``default_branch`` — ``SwitchConfig`` reuses this so the
    multi-way routing logic stays in one place.
    """
    for branch in cfg.branches:
        if eval_group(branch.group, ctx):
            return branch.id
    return cfg.default_branch or "else"
