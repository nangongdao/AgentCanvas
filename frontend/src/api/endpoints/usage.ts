import { apiDownload, apiGet } from "@/api/client";
import type { ApiContractPaths } from "@/api/contracts";

/** Query contract shared by the usage fact endpoints, anchored to the
 * generated OpenAPI paths so the scope/window parameters cannot drift. */
type UsageExportQuery = NonNullable<
  ApiContractPaths["/api/usage/export"]["get"]["parameters"]["query"]
>;
type UsageReconciliationQuery = NonNullable<
  ApiContractPaths["/api/usage/reconciliation"]["get"]["parameters"]["query"]
>;

/**
 * The reconciliation handler is annotated `-> Any`, so its response body is
 * absent from the OpenAPI document and is declared here against
 * `app/schemas/usage.py`. (The export handler streams a file, so it needs no
 * body type at all.)
 */
export interface UsageReconciliationDay {
  day: string;
  rows: number;
  digest: string;
}

export interface UsageReconciliationTotals {
  executions: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_unknown_executions: number;
  storage_bytes_delta: number;
  retrievals: number;
  /** Absent when any execution in scope ran without a known price — the
   * server refuses to sum a partial total into a misleading number. */
  estimated_cost_usd?: string | null;
}

export interface UsageReconciliation {
  month: string;
  digest: string;
  days: UsageReconciliationDay[];
  totals: UsageReconciliationTotals;
  scope: { organization_id: string | null; project_id: string | null };
}

function queryString(params: Record<string, string | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, value);
  }
  return search.toString();
}

/** Deterministic month digest for billing reconciliation (weak-ETag cached). */
export function getUsageReconciliation(
  query: Pick<UsageReconciliationQuery, "month"> &
    Partial<Pick<UsageReconciliationQuery, "organization_id" | "project_id">>,
): Promise<UsageReconciliation> {
  return apiGet<UsageReconciliation>(
    `/api/usage/reconciliation?${queryString(query)}`,
  );
}

/** Download the raw metering facts for a window as CSV or JSON. */
export function downloadUsageExport(
  query: Pick<UsageExportQuery, "from_day" | "to_day"> & {
    format?: "csv" | "json";
  } & Partial<Pick<UsageExportQuery, "organization_id" | "project_id">>,
): Promise<string> {
  const format = query.format ?? "csv";
  return apiDownload(
    `/api/usage/export?${queryString({ ...query, format })}`,
    `usage-${query.from_day}-${query.to_day}.${format}`,
  );
}
