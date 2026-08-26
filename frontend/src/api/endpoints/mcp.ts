/** MCP server registry, discovery, and connection-check endpoints. */

import { apiGet, apiSend } from "@/api/client";

export type McpTransport = "stdio" | "sse" | "streamable_http";

export interface McpToolDTO {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface McpServerDTO {
  id: string;
  name: string;
  transport: McpTransport;
  command?: string | null;
  args: string[];
  env: Record<string, string>;
  env_sources: Record<string, string>;
  url?: string | null;
  headers: Record<string, string>;
  headers_sources: Record<string, string>;
  enabled: boolean;
  catalog_entry_id?: string | null;
  catalog_version_id?: string | null;
  tools: McpToolDTO[];
  tools_cached_at?: string | null;
  last_status?: string | null;
  connected: boolean;
  created_at?: string | null;
}

export interface McpServerInput {
  name: string;
  transport: McpTransport;
  command?: string | null;
  args: string[];
  env: Record<string, string>;
  url?: string | null;
  headers: Record<string, string>;
  enabled: boolean;
}

export interface ResilienceSnapshotDTO {
  key: string;
  state: "closed" | "open" | "half_open";
  consecutive_failures: number;
  total_failures: number;
  total_successes: number;
  retry_budget_used: number;
  retry_budget_limit: number;
  cooldown_remaining_seconds: number;
  injected_failures_remaining: number;
}

export interface McpHealthDTO {
  server_id: string;
  state: "healthy" | "degraded";
  connected?: boolean;
  tool_count?: number;
  tools?: McpToolDTO[];
  duration_ms: number;
  error?: string;
  resilience?: ResilienceSnapshotDTO | null;
}

export type McpCatalogVersionStatus = "draft" | "approved" | "superseded" | "revoked";

export interface McpCatalogManifestDTO {
  transport: McpTransport;
  permissions: {
    network: string[];
    filesystem: string[];
    commands: string[];
  };
}

export interface McpCatalogVersionDTO {
  id: string;
  entry_id: string;
  version: string;
  source_ref: string;
  manifest: McpCatalogManifestDTO;
  status: McpCatalogVersionStatus;
  approved_at?: string | null;
  approved_by?: string | null;
  created_at?: string | null;
}

export interface McpCatalogEntryDTO {
  id: string;
  name: string;
  description: string;
  source_url?: string | null;
  versions: McpCatalogVersionDTO[];
  created_at?: string | null;
  updated_at?: string | null;
}

export interface McpCatalogVersionDiffDTO {
  entry_id: string;
  from_version_id?: string | null;
  from_version?: string | null;
  to_version_id: string;
  to_version: string;
  source_changed: boolean;
  transport_changed: boolean;
  permission_added: McpCatalogManifestDTO["permissions"];
  permission_removed: McpCatalogManifestDTO["permissions"];
  changed_fields: string[];
}

export interface McpCatalogRolloutServerDTO {
  server_id: string;
  name: string;
  project_id?: string | null;
  current_version_id?: string | null;
  current_version?: string | null;
  compatible: boolean;
  reason?: string | null;
}

export interface McpCatalogRolloutPreviewDTO {
  entry_id: string;
  version_id: string;
  version: string;
  servers: McpCatalogRolloutServerDTO[];
  compatible_count: number;
  incompatible_count: number;
}

export interface McpCatalogRolloutDTO {
  entry_id: string;
  version_id: string;
  version: string;
  updated_server_ids: string[];
  unchanged_server_ids: string[];
  skipped: McpCatalogRolloutServerDTO[];
}

export function listMcpServers() {
  return apiGet<McpServerDTO[]>("/api/mcp/servers");
}

export function createMcpServer(body: McpServerInput) {
  return apiSend<McpServerDTO>("/api/mcp/servers", "POST", body);
}

export function updateMcpServer(id: string, body: Partial<McpServerInput>) {
  return apiSend<McpServerDTO>(`/api/mcp/servers/${id}`, "PUT", body);
}

export function deleteMcpServer(id: string) {
  return apiSend<void>(`/api/mcp/servers/${id}`, "DELETE");
}

export function listMcpTools(id: string, refresh = false) {
  const query = refresh ? "?refresh=true" : "";
  return apiGet<McpToolDTO[]>(`/api/mcp/servers/${id}/tools${query}`);
}

export function testMcpServer(id: string) {
  return apiSend<{ status: "connected"; tools: McpToolDTO[] }>(
    `/api/mcp/servers/${id}/test`,
    "POST",
  );
}

export function healthMcpServer(id: string) {
  return apiGet<McpHealthDTO>(`/api/mcp/servers/${id}/health`);
}

export function listMcpCatalog() {
  return apiGet<McpCatalogEntryDTO[]>("/api/mcp/catalog");
}

export function bindMcpServerCatalog(id: string, versionId: string | null) {
  return apiSend<McpServerDTO>(`/api/mcp/servers/${id}/catalog`, "PUT", {
    version_id: versionId,
  });
}

export function createMcpCatalogEntry(body: {
  id: string;
  name: string;
  description: string;
  source_url?: string | null;
  version: string;
  source_ref: string;
  manifest: McpCatalogManifestDTO;
}) {
  return apiSend<McpCatalogEntryDTO>("/api/mcp/catalog", "POST", body);
}

export function createMcpCatalogVersion(
  entryId: string,
  body: { version: string; source_ref: string; manifest: McpCatalogManifestDTO },
) {
  return apiSend<McpCatalogVersionDTO>(
    `/api/mcp/catalog/${entryId}/versions`,
    "POST",
    body,
  );
}

export function approveMcpCatalogVersion(entryId: string, versionId: string) {
  return apiSend<McpCatalogVersionDTO>(
    `/api/mcp/catalog/${entryId}/versions/${versionId}/approve`,
    "POST",
  );
}

export function revokeMcpCatalogVersion(entryId: string, versionId: string) {
  return apiSend<McpCatalogVersionDTO>(
    `/api/mcp/catalog/${entryId}/versions/${versionId}/revoke`,
    "POST",
  );
}

export function getMcpCatalogVersionDiff(
  entryId: string,
  versionId: string,
  fromVersionId?: string | null,
) {
  const query = fromVersionId ? `?from_version_id=${encodeURIComponent(fromVersionId)}` : "";
  return apiGet<McpCatalogVersionDiffDTO>(
    `/api/mcp/catalog/${entryId}/versions/${versionId}/diff${query}`,
  );
}

export function previewMcpCatalogRollout(
  entryId: string,
  versionId: string,
  serverIds: string[] = [],
) {
  return apiSend<McpCatalogRolloutPreviewDTO>(
    `/api/mcp/catalog/${entryId}/rollout/preview`,
    "POST",
    { version_id: versionId, server_ids: serverIds },
  );
}

export function rolloutMcpCatalogVersion(
  entryId: string,
  versionId: string,
  serverIds: string[] = [],
) {
  return apiSend<McpCatalogRolloutDTO>(
    `/api/mcp/catalog/${entryId}/rollout`,
    "POST",
    { version_id: versionId, server_ids: serverIds },
  );
}
