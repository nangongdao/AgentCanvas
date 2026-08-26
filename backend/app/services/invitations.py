"""Organization invitation issue/revoke/accept lifecycle (C7-4)."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import Membership, OrganizationInvitation
from app.services.mailer import DeliveryReport, NullMailer


def hash_invitation_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class InvitationError(RuntimeError):
    """Raised for expired, used, or revoked invitations."""


@dataclass(frozen=True)
class IssuedInvitation:
    invitation: OrganizationInvitation
    token: str
    delivery: DeliveryReport


class InvitationService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        # Only ``issue`` needs settings (expiry + mailer); read-only and
        # accept paths work without them.
        self.settings = settings
        self.mailer = NullMailer()  # replaced with the container mailer when wired

    def bind_mailer(self, mailer: object) -> None:
        self.mailer = mailer  # type: ignore[assignment]

    async def issue(
        self,
        *,
        organization_id: str,
        email: str,
        role: str,
        invited_by: str,
        base_url: str,
        now: datetime | None = None,
    ) -> IssuedInvitation:
        if self.settings is None:
            raise RuntimeError("issuing invitations requires settings")
        moment = now or datetime.now(UTC)
        raw = secrets.token_urlsafe(32)
        invitation = OrganizationInvitation(
            organization_id=organization_id,
            email=email,
            role=role,
            token_hash=hash_invitation_token(raw),
            invited_by=invited_by,
            expires_at=moment + timedelta(days=self.settings.invitation_expire_days),
        )
        self.session.add(invitation)
        await self.session.flush()
        link = f"{base_url.rstrip('/')}/invitations/accept?token={raw}"
        report = await self.mailer.send(
            to=email,
            subject="AgentCanvas 组织邀请",
            body=(
                "您被邀请加入 AgentCanvas 组织。\n\n"
                f"打开以下链接并登录后即可加入(角色:{role}):\n{link}\n\n"
                "该链接单次有效且会过期。若与您无关请忽略本邮件。"
            ),
        )
        return IssuedInvitation(invitation=invitation, token=raw, delivery=report)

    async def list_for_org(
        self, organization_id: str, *, include_accepted: bool = True
    ) -> list[OrganizationInvitation]:
        stmt = (
            select(OrganizationInvitation)
            .where(OrganizationInvitation.organization_id == organization_id)
            .order_by(OrganizationInvitation.created_at.desc())
        )
        if not include_accepted:
            stmt = stmt.where(OrganizationInvitation.accepted_at.is_(None))
        return list((await self.session.scalars(stmt)).all())

    async def revoke(self, invitation_id: str) -> OrganizationInvitation:
        invitation = await self.session.get(OrganizationInvitation, invitation_id)
        if invitation is None:
            raise KeyError(invitation_id)
        await self.session.delete(invitation)
        await self.session.flush()
        return invitation

    async def accept(
        self, *, raw_token: str, user_id: str, now: datetime | None = None
    ) -> OrganizationInvitation:
        """Burn one invitation and bind the accepting user to its organization."""
        moment = now or datetime.now(UTC)
        invitation = (
            await self.session.scalars(
                select(OrganizationInvitation).where(
                    OrganizationInvitation.token_hash == hash_invitation_token(raw_token)
                )
            )
        ).first()
        if invitation is None:
            raise InvitationError("invitation not found")
        if invitation.accepted_at is not None:
            raise InvitationError("invitation already used")
        if invitation.expires_at.replace(tzinfo=UTC) <= moment:
            raise InvitationError("invitation expired")
        existing = (
            await self.session.scalars(
                select(Membership).where(
                    Membership.organization_id == invitation.organization_id,
                    Membership.user_id == user_id,
                )
            )
        ).first()
        if existing is not None:
            # Already a member: burn the invitation so it cannot be reused,
            # but do not duplicate or downgrade the membership.
            invitation.accepted_at = moment
            invitation.accepted_by_user_id = user_id
            await self.session.flush()
            raise InvitationError("already a member of this organization")
        membership = Membership(
            organization_id=invitation.organization_id,
            user_id=user_id,
            role=invitation.role,
        )
        self.session.add(membership)
        invitation.accepted_at = moment
        invitation.accepted_by_user_id = user_id
        await self.session.flush()
        return invitation


__all__ = ["InvitationError", "InvitationService", "IssuedInvitation", "hash_invitation_token"]
