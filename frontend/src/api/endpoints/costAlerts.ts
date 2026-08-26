import { apiGet, apiSend } from "@/api/client";
import { type PageQuery, type PageResult, withQuery } from "@/api/pagination";

export interface CostAlertDTO {
  id: string;
  execution_id?: string | null;
  workflow_id?: string | null;
  kind: string;
  severity: string;
  status: string;
  limit_value: string;
  actual_value: string;
  message: string;
  created_at?: string | null;
}

export interface CostGovernanceDTO {
  max_tokens_per_execution: number;
  max_cost_usd_per_execution?: string | null;
  max_concurrent_per_execution: number;
  max_calls_per_execution: number;
  summary: {
    total: number;
    open: number;
    acknowledged: number;
    critical: number;
    warning: number;
    open_by_kind: Record<string, number>;
  };
}

export function listCostAlerts(query: PageQuery = {}) {
  return apiGet<PageResult<CostAlertDTO>>(
    withQuery("/api/cost-alerts", query),
  );
}

export function getCostGovernance() {
  return apiGet<CostGovernanceDTO>("/api/cost-alerts/governance");
}

export function acknowledgeCostAlert(id: string) {
  return apiSend<CostAlertDTO>(`/api/cost-alerts/${id}/ack`, "POST");
}
