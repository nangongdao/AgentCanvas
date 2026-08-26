import { apiGet, apiSend } from "@/api/client";

export type MemberRole = "viewer" | "editor" | "admin";

export interface OrganizationRef {
  id: string;
  name: string;
  slug: string;
  created_at: string;
  updated_at: string;
}

export interface Membership {
  id: string;
  organization_id: string;
  user_id: string;
  role: MemberRole;
  created_at: string | null;
}

export interface Invitation {
  id: string;
  organization_id: string;
  email: string;
  role: MemberRole;
  invited_by: string;
  expires_at: string;
  accepted_at: string | null;
  accepted_by_user_id: string | null;
  created_at: string;
}

export interface IssuedInvitation {
  invitation: Invitation;
  accept_url: string;
  delivery: "email" | "manual";
  delivery_detail: string;
}

export function listMyOrganizations(): Promise<OrganizationRef[]> {
  return apiGet("/api/organizations");
}

export function listMembers(organizationId: string): Promise<Membership[]> {
  return apiGet(`/api/organizations/${organizationId}/members`);
}

export function updateMemberRole(
  organizationId: string,
  userId: string,
  role: MemberRole,
): Promise<Membership> {
  return apiSend(`/api/organizations/${organizationId}/members/${userId}`, "PUT", {
    role,
  });
}

export function removeMember(organizationId: string, userId: string): Promise<void> {
  return apiSend(`/api/organizations/${organizationId}/members/${userId}`, "DELETE");
}

export function listInvitations(organizationId: string): Promise<Invitation[]> {
  return apiGet(`/api/organizations/${organizationId}/invitations`);
}

export function createInvitation(
  organizationId: string,
  body: { email: string; role: MemberRole },
): Promise<IssuedInvitation> {
  return apiSend(`/api/organizations/${organizationId}/invitations`, "POST", body);
}

export function revokeInvitation(
  organizationId: string,
  invitationId: string,
): Promise<void> {
  return apiSend(
    `/api/organizations/${organizationId}/invitations/${invitationId}`,
    "DELETE",
  );
}

export function acceptInvitation(token: string): Promise<Invitation> {
  return apiSend("/api/organizations/invitations/accept", "POST", { token });
}
