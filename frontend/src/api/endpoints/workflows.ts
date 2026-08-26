/** Workflow / execution API endpoints. */

import { apiGet, apiSend } from "@/api/client";
import type { ApiContractSchema } from "@/api/contracts";
import {
  type PageQuery,
  type PageResult,
  withQuery,
} from "@/api/pagination";
import type { WorkflowDSL } from "@/types/dsl";

export interface WorkflowDTO
  extends Omit<ApiContractSchema<"WorkflowOut">, "dsl"> {
  dsl: WorkflowDSL;
}

export type ExecutionDTO = ApiContractSchema<"ExecutionOut">;

export type DebugRunOptions = ApiContractSchema<"DebugRunOptions">;

export type NodeDryRunResponse = ApiContractSchema<"NodeDryRunResponse">;

export interface NodeDryRunRequest {
  node_id: string;
  node_type: string;
  node_config: Record<string, unknown>;
  inputs: Record<string, unknown>;
}

export type NodeAttemptStatus =
  | "running"
  | "succeeded"
  | "failed"
  | "interrupted"
  | "cancelled";

export interface NodeAttemptDTO
  extends Omit<ApiContractSchema<"NodeAttemptOut">, "status"> {
  status: NodeAttemptStatus;
}

export interface ExecutionInspectionDTO
  extends Omit<ApiContractSchema<"ExecutionInspectionOut">, "attempts"> {
  attempts: NodeAttemptDTO[];
}

export type WorkflowVersionStatus = "draft" | "published" | "archived";

export interface WorkflowVersionDTO {
  id: string;
  workflow_id: string;
  number: number;
  status: WorkflowVersionStatus;
  name: string;
  description: string;
  dsl: WorkflowDSL;
  change_summary: string;
  created_at?: string;
  published_at?: string | null;
  archived_at?: string | null;
}

export interface WorkflowDiffDTO {
  base_id: string;
  target_id: string;
  added_nodes: string[];
  removed_nodes: string[];
  changed_nodes: string[];
  added_edges: string[];
  removed_edges: string[];
  changed_edges: string[];
  settings_changed: boolean;
  variables_changed: boolean;
  canvas_changed: boolean;
}

export interface WorkflowMergeValueDTO {
  present: boolean;
  value: unknown;
}

export interface WorkflowMergeConflictDTO {
  path: string;
  base: WorkflowMergeValueDTO;
  local: WorkflowMergeValueDTO;
  remote: WorkflowMergeValueDTO;
}

export interface WorkflowMergeDTO {
  status: "merged" | "conflict";
  workflow_id: string;
  base_version: number;
  remote_version: number;
  saved_version: number | null;
  name: string;
  dsl: WorkflowDSL;
  conflicts: WorkflowMergeConflictDTO[];
}

export interface WorkflowExportDTO {
  format: "agentcanvas-workflow";
  format_version: 1;
  name: string;
  description: string;
  source_workflow_id: string;
  source_version_number: number;
  source_version_status: WorkflowVersionStatus;
  dsl: WorkflowDSL;
}

export interface WorkflowImportPayload {
  format?: "agentcanvas-workflow";
  format_version?: 1;
  name?: string;
  description?: string;
  dsl: Record<string, unknown>;
}

export function listWorkflows(query: PageQuery = {}) {
  return apiGet<PageResult<WorkflowDTO>>(withQuery("/api/workflows", query));
}

export function createWorkflow(name: string, dsl: WorkflowDSL, description = "") {
  return apiSend<WorkflowDTO>("/api/workflows", "POST", { name, description, dsl });
}

export function updateWorkflow(
  id: string,
  body: {
    name?: string;
    dsl?: WorkflowDSL;
    version?: number;
    description?: string;
    change_summary?: string;
  },
) {
  return apiSend<WorkflowDTO>(`/api/workflows/${id}`, "PUT", body);
}

export function listWorkflowVersions(id: string) {
  return apiGet<WorkflowVersionDTO[]>(`/api/workflows/${id}/versions`);
}

export function getWorkflowVersion(id: string, versionId: string) {
  return apiGet<WorkflowVersionDTO>(
    `/api/workflows/${id}/versions/${versionId}`,
  );
}

export function exportWorkflowVersion(id: string, versionId: string) {
  return apiGet<WorkflowExportDTO>(
    `/api/workflows/${id}/versions/${versionId}/export`,
  );
}

export function importWorkflow(body: WorkflowImportPayload) {
  return apiSend<WorkflowDTO>("/api/workflows/import", "POST", body);
}

export function publishWorkflow(id: string) {
  return apiSend<WorkflowVersionDTO>(`/api/workflows/${id}/publish`, "POST");
}

export function rollbackWorkflow(
  id: string,
  versionId: string,
  changeSummary = "",
) {
  return apiSend<WorkflowVersionDTO>(
    `/api/workflows/${id}/versions/${versionId}/rollback`,
    "POST",
    { change_summary: changeSummary },
  );
}

export function cloneWorkflow(
  id: string,
  body: { version_id?: string; name?: string; description?: string },
) {
  return apiSend<WorkflowDTO>(`/api/workflows/${id}/clone`, "POST", body);
}

export function diffWorkflowVersions(
  id: string,
  baseId: string,
  targetId: string,
) {
  return apiGet<WorkflowDiffDTO>(
    withQuery(`/api/workflows/${id}/versions/diff`, {
      base_id: baseId,
      target_id: targetId,
    }),
  );
}

export function mergeWorkflowChanges(
  id: string,
  body: {
    base_version_id: string;
    remote_version: number;
    local_name: string;
    local_dsl: WorkflowDSL;
    change_summary?: string;
  },
): Promise<WorkflowMergeDTO> {
  return apiSend<WorkflowMergeDTO>(
    `/api/workflows/${id}/versions/merge`,
    "POST",
    body,
  );
}

export function getWorkflow(id: string) {
  return apiGet<WorkflowDTO>(`/api/workflows/${id}`);
}

export function archiveWorkflow(id: string) {
  return apiSend<void>(`/api/workflows/${id}`, "DELETE");
}

export function runWorkflow(
  id: string,
  inputs: Record<string, unknown> = {},
  sessionId?: string,
  debug?: DebugRunOptions | null,
) {
  return apiSend<ExecutionDTO>(`/api/workflows/${id}/run`, "POST", {
    inputs,
    session_id: sessionId,
    debug: debug ?? null,
  });
}

export function dryRunNode(
  workflowId: string,
  request: NodeDryRunRequest,
) {
  return apiSend<NodeDryRunResponse>(
    `/api/workflows/${workflowId}/dry-run`,
    "POST",
    request,
  );
}

export function resumeExecution(
  executionId: string,
  decision: Record<string, unknown>,
  debug?: DebugRunOptions | null,
) {
  return apiSend<ExecutionDTO>(
    `/api/executions/${executionId}/resume`,
    "POST",
    { decision, debug: debug ?? null },
  );
}

export function rerunExecution(executionId: string, nodeId?: string) {
  return apiSend<ExecutionDTO>(
    `/api/executions/${executionId}/rerun`,
    "POST",
    { node_id: nodeId },
  );
}

export function getExecution(id: string) {
  return apiGet<ExecutionDTO>(`/api/executions/${id}`);
}

export function getExecutionInspection(id: string) {
  return apiGet<ExecutionInspectionDTO>(`/api/executions/${id}/inspection`);
}

export function cancelExecution(executionId: string) {
  return apiSend<{ cancelled: boolean }>(
    `/api/executions/${executionId}/cancel`,
    "POST",
  );
}

export function listExecutions(workflowId: string, query: PageQuery = {}) {
  return apiGet<PageResult<ExecutionDTO>>(
    withQuery(`/api/workflows/${workflowId}/executions`, query),
  );
}
