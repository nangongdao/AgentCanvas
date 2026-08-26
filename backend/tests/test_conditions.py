"""Unit tests for the safe condition evaluator."""

from __future__ import annotations

from typing import Any, Literal

from app.engine.conditions import eval_group, eval_rule, pick_branch
from app.schemas.dsl import ConditionConfig, ConditionGroup, ConditionRule

ConditionOperator = Literal[
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "not_contains",
    "regex",
    "is_empty",
    "not_empty",
]


def rule(left: str, op: ConditionOperator, right: Any = None) -> ConditionRule:
    return ConditionRule(left=left, operator=op, right=right)


CTX = {
    "input": {"score": "72", "name": "alice", "tags": ["a", "b"], "empty": ""},
    "nodes": {"n1": {"output": "hello world"}},
    "vars": {},
}


class TestEvalRule:
    def test_numeric_gt(self) -> None:
        assert eval_rule(rule("{{input.score}}", "gt", 50), CTX)
        assert not eval_rule(rule("{{input.score}}", "gt", 100), CTX)

    def test_numeric_boundaries(self) -> None:
        assert eval_rule(rule("{{input.score}}", "gte", 72), CTX)
        assert eval_rule(rule("{{input.score}}", "lte", 72), CTX)
        assert not eval_rule(rule("{{input.score}}", "lt", 72), CTX)

    def test_eq_numeric_coercion(self) -> None:
        assert eval_rule(rule("{{input.score}}", "eq", 72), CTX)
        assert eval_rule(rule("{{input.score}}", "eq", "72.0"), CTX)
        assert eval_rule(rule("{{input.name}}", "eq", "alice"), CTX)
        assert eval_rule(rule("{{input.name}}", "ne", "bob"), CTX)

    def test_contains(self) -> None:
        assert eval_rule(rule("{{nodes.n1.output}}", "contains", "world"), CTX)
        assert eval_rule(rule("{{nodes.n1.output}}", "not_contains", "mars"), CTX)

    def test_regex(self) -> None:
        assert eval_rule(rule("{{nodes.n1.output}}", "regex", r"^hello\s"), CTX)
        # Invalid regex must not raise
        assert not eval_rule(rule("{{nodes.n1.output}}", "regex", "["), CTX)

    def test_empty_checks(self) -> None:
        assert eval_rule(rule("{{input.empty}}", "is_empty"), CTX)
        assert eval_rule(rule("{{input.name}}", "not_empty"), CTX)

    def test_non_numeric_gt_is_false(self) -> None:
        assert not eval_rule(rule("{{input.name}}", "gt", 5), CTX)

    def test_unknown_operator_rejected_by_schema(self) -> None:
        import pydantic
        import pytest

        with pytest.raises(pydantic.ValidationError):
            ConditionRule.model_validate(
                {"left": "{{input.name}}", "operator": "startswith", "right": "a"}
            )


class TestEvalGroup:
    def test_and(self) -> None:
        g = ConditionGroup(
            op="and",
            rules=[rule("{{input.score}}", "gt", 50), rule("{{input.name}}", "eq", "alice")],
        )
        assert eval_group(g, CTX)

    def test_or(self) -> None:
        g = ConditionGroup(
            op="or",
            rules=[rule("{{input.score}}", "gt", 100), rule("{{input.name}}", "eq", "alice")],
        )
        assert eval_group(g, CTX)

    def test_empty_rules_false(self) -> None:
        assert not eval_group(ConditionGroup(op="and", rules=[]), CTX)


class TestPickBranch:
    def test_first_match_wins(self) -> None:
        cfg = ConditionConfig.model_validate(
            {
                "branches": [
                    {
                        "id": "high",
                        "group": {
                            "op": "and",
                            "rules": [rule("{{input.score}}", "gt", 90).model_dump()],
                        },
                    },
                    {
                        "id": "mid",
                        "group": {
                            "op": "and",
                            "rules": [rule("{{input.score}}", "gt", 50).model_dump()],
                        },
                    },
                ],
                "default_branch": "low",
            }
        )
        assert pick_branch(cfg, CTX) == "mid"

    def test_default_when_no_match(self) -> None:
        cfg = ConditionConfig.model_validate(
            {
                "branches": [
                    {
                        "id": "high",
                        "group": {
                            "op": "and",
                            "rules": [rule("{{input.score}}", "gt", 90).model_dump()],
                        },
                    }
                ],
                "default_branch": "low",
            }
        )
        assert pick_branch(cfg, CTX) == "low"
