import { apiGet, apiSend } from "@/api/client";

export type OrgStatus = "active" | "disabled";
export type AnnouncementLevel = "info" | "warning" | "critical";
export type OrgDeletionStatus = "none" | "requested" | "purging" | "purged";

export interface OrgDeletionState {
  status: OrgDeletionStatus;
  requested_at: string | null;
  requested_by: string | null;
  purge_due_at: string | null;
}

export interface AdminOrganization {
  id: string;
  name: string;
  slug: string;
  status: OrgStatus;
  plan_id: string | null;
  plan_slug: string | null;
  plan_name: string | null;
  project_count: number;
  member_count: number;
  deletion: OrgDeletionState;
  created_at: string;
}

export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  role: string;
  status: string;
  created_at: string;
  last_login_at: string | null;
}

export interface AdminQueueItem {
  id: string;
  execution_id: string;
  kind: string;
  status: string;
  attempt: number;
  lease_generation: number;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface AdminQueue {
  depth: Record<string, number>;
  dead_letters: AdminQueueItem[];
}

export interface Announcement {
  id: string;
  message: string;
  level: AnnouncementLevel;
  is_active: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface UsageFact {
  day: string;
  organization_id: string;
  project_id: string;
  app_id: string | null;
  model_config_id: string | null;
  executions: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_unknown_executions: number;
  storage_bytes_delta: number;
  retrievals: number;
  estimated_cost_usd: string | null;
}

export interface UsageDaily {
  from_day: string;
  to_day: string;
  facts: UsageFact[];
}

export interface ResilienceResource {
  key: string;
  state: string;
  consecutive_failures: number;
  total_failures: number;
  total_successes: number;
  retry_budget_used: number;
  retry_budget_limit: number;
  cooldown_remaining_seconds: number;
  injected_failures_remaining: number;
}

export interface ResilienceSnapshot {
  resources: ResilienceResource[];
  summary: { total: number; open: number; half_open: number };
}

export function listAdminOrganizations(): Promise<AdminOrganization[]> {
  return apiGet("/api/admin/organizations");
}

export function setOrganizationStatus(
  organizationId: string,
  status: OrgStatus,
): Promise<AdminOrganization> {
  return apiSend(
    `/api/admin/organizations/${organizationId}/status`,
    "PUT",
    { status },
  );
}

export function listAdminUsers(): Promise<AdminUser[]> {
  return apiGet("/api/admin/users");
}

export function setUserStatus(
  userId: string,
  status: "active" | "disabled",
): Promise<AdminUser> {
  return apiSend(`/api/admin/users/${userId}/status`, "PUT", { status });
}

export function getAdminQueue(): Promise<AdminQueue> {
  return apiGet("/api/admin/queue");
}

export function replayDeadLetter(itemId: string): Promise<AdminQueueItem> {
  return apiSend(`/api/admin/queue/${itemId}/replay`, "POST");
}

export function listAnnouncements(): Promise<Announcement[]> {
  return apiGet("/api/admin/announcements");
}

export function createAnnouncement(body: {
  message: string;
  level: AnnouncementLevel;
}): Promise<Announcement> {
  return apiSend("/api/admin/announcements", "POST", body);
}

export function updateAnnouncement(
  announcementId: string,
  body: { message?: string; level?: AnnouncementLevel; is_active?: boolean },
): Promise<Announcement> {
  return apiSend(`/api/admin/announcements/${announcementId}`, "PUT", body);
}

export function deleteAnnouncement(announcementId: string): Promise<void> {
  return apiSend(`/api/admin/announcements/${announcementId}`, "DELETE");
}

export function listActiveAnnouncements(): Promise<Announcement[]> {
  return apiGet("/api/announcements/active");
}

export function getUsageDaily(fromDay: string, toDay: string): Promise<UsageDaily> {
  const params = new URLSearchParams({ from_day: fromDay, to_day: toDay });
  return apiGet(`/api/usage/daily?${params.toString()}`);
}

export function getResilience(): Promise<ResilienceSnapshot> {
  return apiGet("/api/resilience");
}

export function getOrgDeletionStatus(
  organizationId: string,
): Promise<OrgDeletionState> {
  return apiGet(`/api/admin/organizations/${organizationId}/deletion`);
}

export function requestOrgDeletion(organizationId: string): Promise<OrgDeletionState> {
  return apiSend(`/api/admin/organizations/${organizationId}/deletion/request`, "POST");
}

export function cancelOrgDeletion(organizationId: string): Promise<OrgDeletionState> {
  return apiSend(`/api/admin/organizations/${organizationId}/deletion`, "DELETE");
}

export function purgeOrgNow(organizationId: string): Promise<Record<string, number>> {
  return apiSend(`/api/admin/organizations/${organizationId}/deletion/purge`, "POST");
}
