"""Workflow review state vocabulary shared by API and persistence layers."""

from __future__ import annotations

from enum import StrEnum


class ReviewStatus(StrEnum):
    OPEN = "open"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    DISMISSED = "dismissed"


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    DISMISSED = "dismissed"


__all__ = ["ReviewDecision", "ReviewStatus"]
