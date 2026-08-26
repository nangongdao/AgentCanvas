import { apiGet } from "@/api/client";
import { type PageQuery, type PageResult, withQuery } from "@/api/pagination";

export interface AuditLogDTO {
  id: string;
  organization_id?: string | null;
  project_id?: string | null;
  actor_user_id?: string | null;
  actor_key: string;
  actor_subject: string;
  auth_method: string;
  action: string;
  resource_type: string;
  resource_id: string;
  resource_name?: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface AuditLogQuery extends PageQuery {
  organization_id?: string;
  project_id?: string;
  actor_key?: string;
  action?: string;
  resource_type?: string;
}

export function listAuditLogs(query: AuditLogQuery = {}) {
  return apiGet<PageResult<AuditLogDTO>>(withQuery("/api/audit-logs", query));
}
