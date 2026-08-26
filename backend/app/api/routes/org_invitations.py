"""Organization membership invitations (C7-4).

Invitations are single-use token URLs issued by organization admins.
Without SMTP configuration the raw link is returned to the inviting admin
(``delivery: "manual"``); with SMTP configured it is emailed and the
response still discloses the link to the admin who created it. Accepting
binds the authenticated principal's user to the organization with the
invited role, burns the invitation, and writes an audit row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ViewerDep, get_container, get_session
from app.api.tenant_deps import require_org_member
from app.core.auth import Role
from app.services.audit import record_audit
from app.services.invitations import InvitationError, InvitationService
from app.services.mailer import build_mailer

router = APIRouter(tags=["org invitations"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class InvitationCreate(BaseModel):
    # Deliberately a bounded string with a light shape check: full RFC
    # validation is not worth an email-validator dependency for invitations.
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Literal["viewer", "editor", "admin"] = "viewer"


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organization_id: str
    email: str
    role: str
    invited_by: str
    expires_at: datetime
    accepted_at: datetime | None = None
    accepted_by_user_id: str | None = None
    created_at: datetime


class InvitationIssueOut(BaseModel):
    invitation: InvitationOut
    accept_url: str
    delivery: str
    delivery_detail: str


class InvitationAccept(BaseModel):
    token: str = Field(min_length=20, max_length=200)


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.post(
    "/api/organizations/{org_id}/invitations",
    response_model=InvitationIssueOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    org_id: str,
    body: InvitationCreate,
    request: Request,
    session: SessionDep,
    principal: ViewerDep,
) -> InvitationIssueOut:
    await require_org_member(session, principal, org_id, required=Role.ADMIN)
    container = get_container(request)
    service = InvitationService(session, container.settings)
    service.bind_mailer(build_mailer(container.settings))
    issued = await service.issue(
        organization_id=org_id,
        email=body.email.lower().strip(),
        role=body.role,
        invited_by=principal.subject,
        base_url=_base_url(request),
    )
    await record_audit(
        session,
        principal,
        action="invitation.created",
        resource_type="org_invitation",
        resource_id=issued.invitation.id,
        organization_id=org_id,
        details={"email": issued.invitation.email, "role": issued.invitation.role},
    )
    return InvitationIssueOut(
        invitation=InvitationOut.model_validate(issued.invitation),
        accept_url=f"{_base_url(request)}/invitations/accept?token={issued.token}",
        delivery=issued.delivery.delivery,
        delivery_detail=issued.delivery.detail,
    )


@router.get(
    "/api/organizations/{org_id}/invitations",
    response_model=list[InvitationOut],
)
async def list_invitations(
    org_id: str, session: SessionDep, principal: ViewerDep
) -> list[InvitationOut]:
    await require_org_member(session, principal, org_id, required=Role.ADMIN)
    rows = await InvitationService(session).list_for_org(org_id)
    return [InvitationOut.model_validate(row) for row in rows]


@router.delete(
    "/api/organizations/{org_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_invitation(
    org_id: str, invitation_id: str, session: SessionDep, principal: ViewerDep
) -> None:
    await require_org_member(session, principal, org_id, required=Role.ADMIN)
    service = InvitationService(session)
    try:
        invitation = await service.revoke(invitation_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="invitation not found"
        ) from None
    if invitation.organization_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invitation not found")
    await record_audit(
        session,
        principal,
        action="invitation.revoked",
        resource_type="org_invitation",
        resource_id=invitation.id,
        organization_id=org_id,
        details={"email": invitation.email},
    )


@router.post("/api/organizations/invitations/accept", response_model=InvitationOut)
async def accept_invitation(
    body: InvitationAccept, session: SessionDep, principal: ViewerDep
) -> InvitationOut:
    """Burn one invitation and join its organization."""
    if principal.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an interactive user session is required to accept invitations",
        )
    service = InvitationService(session)
    try:
        invitation = await service.accept(raw_token=body.token, user_id=principal.user_id)
    except InvitationError as exc:
        message = str(exc)
        code = (
            status.HTTP_409_CONFLICT
            if "already a member" in message or "already used" in message
            else status.HTTP_410_GONE
        )
        raise HTTPException(status_code=code, detail=message) from exc
    await record_audit(
        session,
        principal,
        action="invitation.accepted",
        resource_type="org_invitation",
        resource_id=invitation.id,
        organization_id=invitation.organization_id,
        details={"email": invitation.email, "role": invitation.role},
    )
    return InvitationOut.model_validate(invitation)


__all__ = ["router"]
