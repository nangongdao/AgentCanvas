import { apiGet, apiSend } from "@/api/client";
import { withQuery, type PageQuery, type PageResult } from "@/api/pagination";

export type AppType = "chatbot" | "completion" | "api";
export type AppVisibility = "project" | "link" | "public";
export type AppStatus = "active" | "disabled";

export interface InputFieldDef {
  name: string;
  type: string;
  required: boolean;
  default: unknown;
}

export interface AppDTO {
  id: string;
  project_id: string;
  workflow_id: string | null;
  published_version_id: string | null;
  published_version_number: number | null;
  name: string;
  icon: string | null;
  type: AppType;
  welcome_message: string | null;
  suggested_questions: string[];
  input_form: InputFieldDef[];
  visibility: AppVisibility;
  status: AppStatus;
  has_public_access: boolean;
  token_prefix: string | null;
  slug: string;
  theme_color: string | null;
  embed_allowed_origins: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface AppIssueOut {
  app: AppDTO;
  public_url: string | null;
  token: string | null;
}

export interface AppCreate {
  project_id: string;
  workflow_id?: string | null;
  name: string;
  icon?: string | null;
  type?: AppType;
  welcome_message?: string | null;
  suggested_questions?: string[];
  visibility?: AppVisibility;
  theme_color?: string | null;
  embed_allowed_origins?: string[] | null;
}

export interface AppUpdate {
  name?: string;
  icon?: string | null;
  type?: AppType;
  welcome_message?: string | null;
  suggested_questions?: string[];
  visibility?: AppVisibility;
  status?: AppStatus;
  theme_color?: string | null;
  embed_allowed_origins?: string[] | null;
}

export interface AppVersionSwitch {
  version_id: string;
}

export function listApps(projectId: string, query: PageQuery = {}) {
  return apiGet<PageResult<AppDTO>>(
    withQuery("/api/apps", { project_id: projectId, ...query }),
  );
}

export function getApp(appId: string) {
  return apiGet<AppDTO>(`/api/apps/${appId}`);
}

export function createApp(body: AppCreate) {
  return apiSend<AppIssueOut>("/api/apps", "POST", body);
}

export function updateApp(appId: string, body: AppUpdate) {
  return apiSend<AppIssueOut>(`/api/apps/${appId}`, "PUT", body);
}

export function switchAppVersion(appId: string, body: AppVersionSwitch) {
  return apiSend<AppDTO>(`/api/apps/${appId}/version`, "POST", body);
}

export function rotateAppToken(appId: string) {
  return apiSend<AppIssueOut>(`/api/apps/${appId}/token/rotate`, "POST");
}

export function deleteApp(appId: string) {
  return apiSend<void>(`/api/apps/${appId}`, "DELETE");
}

// ---- C3-5: per-application usage view ----

export interface AppUsageDayDTO {
  date: string;
  sessions: number;
  messages: number;
  executions: number;
  total_tokens: number;
  estimated_cost_usd: string | null;
}

export interface AppUsageDTO {
  app_id: string;
  days: number;
  since: string;
  sessions: number;
  user_messages: number;
  assistant_messages: number;
  executions: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost_usd: string | null;
  cost_known: boolean;
  positive_feedback: number;
  negative_feedback: number;
  feedback_rate: number | null;
  available_citations: number;
  referenced_citations: number;
  citation_coverage: number | null;
  daily: AppUsageDayDTO[];
}

export function getAppUsage(appId: string, days = 30) {
  return apiGet<AppUsageDTO>(`/api/apps/${appId}/usage?days=${days}`);
}
