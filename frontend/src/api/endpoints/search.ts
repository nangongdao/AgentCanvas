import { apiGet } from "@/api/client";
import type { ApiContractSchema } from "@/api/contracts";

export type GlobalSearchResultDTO = ApiContractSchema<"GlobalSearchResultOut">;
export type GlobalSearchDTO = ApiContractSchema<"GlobalSearchOut">;

export function searchGlobalResources(
  query: string,
  options: { limit?: number; signal?: AbortSignal } = {},
) {
  const params = new URLSearchParams({
    q: query,
    limit: String(options.limit ?? 12),
  });
  return apiGet<GlobalSearchDTO>(`/api/search?${params.toString()}`, {
    signal: options.signal,
  });
}
