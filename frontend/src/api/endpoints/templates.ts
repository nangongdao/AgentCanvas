import { apiGet, apiSend } from "@/api/client";
import { withQuery } from "@/api/pagination";
import type { WorkflowDTO } from "@/api/endpoints/workflows";
import type { WorkflowDSL } from "@/types/dsl";

export type TemplateParameterType = "string" | "number" | "boolean";

export interface TemplateParameterDTO {
  name: string;
  label: string;
  type: TemplateParameterType;
  required: boolean;
  default?: unknown;
  description: string;
}

export interface WorkflowTemplateDTO {
  id: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  parameters: TemplateParameterDTO[];
  dsl: WorkflowDSL;
  is_official: boolean;
  created_at?: string;
  updated_at?: string;
}

export function listWorkflowTemplates(query: {
  search?: string;
  tag?: string;
  category?: string;
  official?: boolean;
} = {}) {
  return apiGet<WorkflowTemplateDTO[]>(withQuery("/api/workflow-templates", query));
}

export function createWorkflowTemplate(body: {
  name: string;
  description?: string;
  category?: string;
  tags?: string[];
  workflow_id: string;
  version_id?: string;
}) {
  return apiSend<WorkflowTemplateDTO>("/api/workflow-templates", "POST", body);
}

export function instantiateWorkflowTemplate(
  templateId: string,
  body: {
    name?: string;
    description?: string;
    parameters?: Record<string, unknown>;
  },
) {
  return apiSend<WorkflowDTO>(
    `/api/workflow-templates/${templateId}/instantiate`,
    "POST",
    body,
  );
}

export function deleteWorkflowTemplate(templateId: string) {
  return apiSend<void>(`/api/workflow-templates/${templateId}`, "DELETE");
}
