"""Stable actor identity stored with attributable product history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.auth import Principal


@dataclass(frozen=True, slots=True)
class ActorIdentity:
    key: str
    subject: str
    user_id: str | None

    @classmethod
    def from_principal(cls, principal: Principal) -> ActorIdentity:
        key = (
            f"user:{principal.user_id}"
            if principal.user_id is not None
            else f"{principal.auth_method}:{principal.subject}"
        )
        return cls(key=key, subject=principal.subject, user_id=principal.user_id)


__all__ = ["ActorIdentity"]
