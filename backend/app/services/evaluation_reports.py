"""Stable aggregate definitions for evaluation and A/B reports."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from app.services.rag_evaluation import summarize_rag_metrics


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def summarize_run(case_results: Iterable[Any]) -> dict[str, Any]:
    rows = list(case_results)
    counts = {status: 0 for status in ("passed", "failed", "error")}
    scores: list[float] = []
    durations: list[int] = []
    known_cost = Decimal(0)
    error_bound = Decimal(0)
    known_cases = 0
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    price_versions: set[str] = set()
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
        if row.score is not None:
            scores.append(row.score)
        if row.duration_ms is not None:
            durations.append(row.duration_ms)
        prompt_tokens += row.prompt_tokens or 0
        completion_tokens += row.completion_tokens or 0
        total_tokens += row.total_tokens or 0
        price_versions.update(row.price_versions_json or [])
        if row.cost_known:
            known_cases += 1
            known_cost += _decimal(row.estimated_cost_usd)
            error_bound += _decimal(row.cost_error_bound_usd)

    total = len(rows)
    all_costs_known = known_cases == total
    summary: dict[str, Any] = {
        "total": total,
        **counts,
        "pass_rate": counts["passed"] / total if total else 0,
        "failure_rate": counts["error"] / total if total else 0,
        "average_score": sum(scores) / len(scores) if scores else None,
        "average_duration_ms": sum(durations) / len(durations) if durations else None,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "known_cost_usd": _decimal_text(known_cost),
        "estimated_cost_usd": _decimal_text(known_cost) if all_costs_known else None,
        "cost_coverage": known_cases / total if total else 1,
        "cost_rounding_error_bound_usd": _decimal_text(error_bound),
        "price_versions": sorted(price_versions),
        "cost_definition": "workflow agent usage only; USD rounded once per case to 1e-12",
    }
    rag = summarize_rag_metrics(rows)
    if rag is not None:
        summary["rag"] = rag
    return summary


def _number_delta(left: Any, right: Any) -> float | None:
    if left is None or right is None:
        return None
    return float(right) - float(left)


def build_comparison_summary(run_a: Any, run_b: Any) -> dict[str, Any]:
    a = dict(run_a.summary_json or {})
    b = dict(run_b.summary_json or {})
    cost_a = a.get("estimated_cost_usd")
    cost_b = b.get("estimated_cost_usd")
    cost_delta = (
        _decimal_text(_decimal(cost_b) - _decimal(cost_a))
        if cost_a is not None and cost_b is not None
        else None
    )
    quality_a = float(a.get("pass_rate") or 0)
    quality_b = float(b.get("pass_rate") or 0)
    winner = (
        "variant_a" if quality_a > quality_b else "variant_b" if quality_b > quality_a else "tie"
    )
    delta: dict[str, Any] = {
        "pass_rate": _number_delta(a.get("pass_rate"), b.get("pass_rate")),
        "average_score": _number_delta(a.get("average_score"), b.get("average_score")),
        "average_duration_ms": _number_delta(
            a.get("average_duration_ms"), b.get("average_duration_ms")
        ),
        "failure_rate": _number_delta(a.get("failure_rate"), b.get("failure_rate")),
        "estimated_cost_usd": cost_delta,
        "cost_rounding_error_bound_usd": _decimal_text(
            _decimal(a.get("cost_rounding_error_bound_usd"))
            + _decimal(b.get("cost_rounding_error_bound_usd"))
        ),
    }
    raw_rag_a = a.get("rag")
    raw_rag_b = b.get("rag")
    rag_a: dict[str, Any] = dict(raw_rag_a) if isinstance(raw_rag_a, dict) else {}
    rag_b: dict[str, Any] = dict(raw_rag_b) if isinstance(raw_rag_b, dict) else {}
    if rag_a or rag_b:
        delta["rag"] = {
            key: _number_delta(rag_a.get(key), rag_b.get(key))
            for key in (
                "recall_at_k",
                "mrr",
                "citation_coverage",
                "no_answer_rate",
                "no_answer_accuracy",
                "correct_abstention_rate",
                "false_no_answer_rate",
                "false_answer_rate",
            )
        }
    return {
        "variant_a": {
            "run_id": run_a.id,
            "workflow_version_id": run_a.workflow_version_id,
            **a,
        },
        "variant_b": {
            "run_id": run_b.id,
            "workflow_version_id": run_b.workflow_version_id,
            **b,
        },
        "delta_b_minus_a": delta,
        "quality_winner": winner,
    }


__all__ = ["build_comparison_summary", "summarize_run"]
