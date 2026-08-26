"""Fencing token for one claimed execution queue attempt."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkerLease:
    item_id: str
    owner_id: str
    generation: int


class LeaseLost(RuntimeError):
    """Raised when a worker tries to commit after losing its queue lease."""


__all__ = ["LeaseLost", "WorkerLease"]
