import { apiGet } from "@/api/client";
import type { ApiContractSchema } from "@/api/contracts";

export type OverviewWorkflowDTO = ApiContractSchema<"OverviewWorkflowOut">;
export type OverviewExecutionSummaryDTO = ApiContractSchema<"OverviewExecutionSummaryOut">;
export type OverviewDayDTO = ApiContractSchema<"OverviewDayOut">;
export type OverviewDTO = ApiContractSchema<"OverviewOut">;

export function getOverview(projectId?: string, days = 7) {
  const query = new URLSearchParams({ days: String(days) });
  if (projectId) query.set("project_id", projectId);
  return apiGet<OverviewDTO>(`/api/overview?${query.toString()}`);
}
