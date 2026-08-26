import { ApiError, apiGet, apiSend } from "@/api/client";
import type { ApiContractSchema } from "@/api/contracts";

export type WebhookTriggerDTO = ApiContractSchema<"WebhookTriggerOut">;
export type WebhookTriggerIssueDTO = ApiContractSchema<"WebhookTriggerIssueOut">;
export type WorkflowScheduleDTO = ApiContractSchema<"WorkflowScheduleOut">;
export type WorkflowApiDTO = ApiContractSchema<"WorkflowApiPublicationOut">;
export type WorkflowApiIssueDTO = ApiContractSchema<"WorkflowApiIssueOut">;
export type WorkflowCallbackDTO = ApiContractSchema<"WorkflowCallbackOut">;
export type WorkflowCallbackIssueDTO = ApiContractSchema<"WorkflowCallbackIssueOut">;
export type WorkflowCallbackDeliveryDTO = ApiContractSchema<"WorkflowCallbackDeliveryOut">;
export type ServiceAccountDTO = ApiContractSchema<"ServiceAccountOut">;
export type CallbackEventType =
  | "workflow_finished"
  | "workflow_failed"
  | "workflow_cancelled"
  | "dead_letter"
  | "cost_alert"
  | "quota_alert";

async function optional<T>(request: Promise<T>): Promise<T | null> {
  try {
    return await request;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export function getWebhook(workflowId: string) {
  return optional(apiGet<WebhookTriggerDTO>(`/api/workflows/${workflowId}/webhook`));
}

export function createWebhook(
  workflowId: string,
  body: { secret?: string; ip_allowlist?: string[] },
) {
  return apiSend<WebhookTriggerIssueDTO>(
    `/api/workflows/${workflowId}/webhook`,
    "POST",
    body,
  );
}

export function rotateWebhook(
  workflowId: string,
  body: { secret?: string; ip_allowlist?: string[] },
) {
  return apiSend<WebhookTriggerIssueDTO>(
    `/api/workflows/${workflowId}/webhook`,
    "PUT",
    body,
  );
}

export function disableWebhook(workflowId: string) {
  return apiSend<void>(`/api/workflows/${workflowId}/webhook`, "DELETE");
}

export function listSchedules(workflowId: string) {
  return apiGet<WorkflowScheduleDTO[]>(`/api/workflows/${workflowId}/schedules`);
}

export function createSchedule(
  workflowId: string,
  body: {
    name: string;
    cron_expression: string;
    timezone: string;
    inputs: Record<string, unknown>;
    enabled: boolean;
    misfire_policy: "skip" | "catch_up";
    failure_policy: "skip" | "retry" | "alert";
    retry_delay_seconds: number;
  },
) {
  return apiSend<WorkflowScheduleDTO>(
    `/api/workflows/${workflowId}/schedules`,
    "POST",
    body,
  );
}

export function updateSchedule(
  workflowId: string,
  scheduleId: string,
  body: { enabled: boolean },
) {
  return apiSend<WorkflowScheduleDTO>(
    `/api/workflows/${workflowId}/schedules/${scheduleId}`,
    "PUT",
    body,
  );
}

export function disableSchedule(workflowId: string, scheduleId: string) {
  return apiSend<void>(
    `/api/workflows/${workflowId}/schedules/${scheduleId}`,
    "DELETE",
  );
}

export function getWorkflowApi(workflowId: string) {
  return optional(apiGet<WorkflowApiDTO>(`/api/workflows/${workflowId}/api`));
}

export function publishWorkflowApi(workflowId: string, serviceAccountId: string) {
  return apiSend<WorkflowApiIssueDTO>(
    `/api/workflows/${workflowId}/api`,
    "POST",
    { service_account_id: serviceAccountId },
  );
}

export function rotateWorkflowApi(workflowId: string, serviceAccountId?: string) {
  return apiSend<WorkflowApiIssueDTO>(
    `/api/workflows/${workflowId}/api`,
    "PUT",
    serviceAccountId ? { service_account_id: serviceAccountId } : {},
  );
}

export function disableWorkflowApi(workflowId: string) {
  return apiSend<void>(`/api/workflows/${workflowId}/api`, "DELETE");
}

export function listServiceAccounts() {
  return apiGet<ServiceAccountDTO[]>("/api/service-accounts");
}

export function getCallback(workflowId: string) {
  return optional(apiGet<WorkflowCallbackDTO>(`/api/workflows/${workflowId}/callback`));
}

export function createCallback(
  workflowId: string,
  body: {
    url: string;
    secret?: string;
    rotate_secret?: boolean;
    event_types: CallbackEventType[];
    timeout_seconds?: number;
    max_attempts?: number;
    retry_delay_seconds?: number;
  },
) {
  return apiSend<WorkflowCallbackIssueDTO>(
    `/api/workflows/${workflowId}/callback`,
    "POST",
    body,
  );
}

export function updateCallback(
  workflowId: string,
  body: {
    url?: string;
    secret?: string;
    event_types?: CallbackEventType[];
    status?: "active" | "disabled";
    timeout_seconds?: number;
    max_attempts?: number;
    retry_delay_seconds?: number;
  },
) {
  return apiSend<WorkflowCallbackIssueDTO>(
    `/api/workflows/${workflowId}/callback`,
    "PUT",
    body,
  );
}

export function disableCallback(workflowId: string) {
  return apiSend<void>(`/api/workflows/${workflowId}/callback`, "DELETE");
}

export function listCallbackDeliveries(workflowId: string) {
  return apiGet<WorkflowCallbackDeliveryDTO[]>(
    `/api/workflows/${workflowId}/callback/deliveries?limit=50`,
  );
}
