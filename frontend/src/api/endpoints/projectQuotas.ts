import { apiGet, apiSend } from "@/api/client";

export interface ProjectDTO {
  id: string;
  organization_id: string;
  name: string;
  slug: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProjectQuotaDTO {
  project_id: string;
  period_start: string;
  can_update: boolean;
  concurrent_execution_limit: number | null;
  storage_bytes_limit: number | null;
  monthly_embedding_input_bytes_limit: number | null;
  monthly_model_cost_usd_limit: string | null;
  stdio_mcp_process_limit: number | null;
  concurrent_executions: number;
  storage_bytes: number;
  embedding_input_bytes: number;
  model_cost_usd: string;
  stdio_mcp_processes: number;
  concurrent_executions_remaining: number | null;
  storage_bytes_remaining: number | null;
  embedding_input_bytes_remaining: number | null;
  model_cost_usd_remaining: string | null;
  stdio_mcp_processes_remaining: number | null;
}

export interface ProjectQuotaUpdate {
  concurrent_execution_limit?: number | null;
  storage_bytes_limit?: number | null;
  monthly_embedding_input_bytes_limit?: number | null;
  monthly_model_cost_usd_limit?: string | null;
  stdio_mcp_process_limit?: number | null;
}

export function listProjects() {
  return apiGet<ProjectDTO[]>("/api/projects");
}

export function getProjectQuotas(projectId: string) {
  return apiGet<ProjectQuotaDTO>(`/api/projects/${projectId}/quotas`);
}

export function updateProjectQuotas(
  projectId: string,
  update: ProjectQuotaUpdate,
) {
  return apiSend<ProjectQuotaDTO>(
    `/api/projects/${projectId}/quotas`,
    "PUT",
    update,
  );
}
