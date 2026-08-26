import { apiGet, apiSend } from "@/api/client";
import type { NodeType } from "@/types/dsl";

export interface JsonSchema {
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
  anyOf?: JsonSchema[];
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  additionalProperties?: boolean | JsonSchema;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
}

export interface NodeTypeDTO {
  type: NodeType;
  label: string;
  description?: string;
  config_schema: JsonSchema;
  output_schema?: JsonSchema;
  plugin?: {
    id: string;
    version: string;
    api_version: string;
    protocol_version: string;
    ui_hints: { icon: string; color: string; inspector_group: string };
    permissions: { network: string[]; filesystem: string[]; commands: string[] };
  };
}

export const CAPABILITY_NAMES = [
  "stream",
  "tools",
  "vision",
  "json_mode",
  "reasoning",
  "usage",
  "cost",
] as const;

export type ProviderCapability = (typeof CAPABILITY_NAMES)[number];
export type ProviderCapabilityProfile = Record<ProviderCapability, boolean>;
export type ProviderCapabilityOverrides = Partial<ProviderCapabilityProfile>;

export interface ProviderDescriptorDTO {
  id: string;
  capabilities: ProviderCapabilityProfile;
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

export interface ResilienceStatusDTO {
  resources: ResilienceSnapshotDTO[];
  summary: {
    total: number;
    open: number;
    half_open: number;
  };
}

export interface ModelConfigDTO {
  id: string;
  name: string;
  provider: string;
  model_name: string;
  base_url?: string | null;
  kind: string;
  is_default: boolean;
  has_api_key: boolean;
  api_key_source: "none" | "stored" | "env" | "docker" | "external";
  prompt_price_per_million_usd?: string | null;
  completion_price_per_million_usd?: string | null;
  pricing_version?: string | null;
  capability_overrides: ProviderCapabilityOverrides;
  capabilities: ProviderCapabilityProfile;
  capability_error?: string | null;
}

export interface ModelConfigCreate {
  id?: string;
  name: string;
  provider: string;
  model_name: string;
  base_url?: string | null;
  api_key?: string;
  params?: Record<string, unknown>;
  prompt_price_per_million_usd?: string | null;
  completion_price_per_million_usd?: string | null;
  pricing_version?: string | null;
  capability_overrides?: ProviderCapabilityOverrides;
  kind: "chat" | "embedding";
  is_default?: boolean;
}

export interface ModelConfigUpdate {
  name?: string;
  provider?: string;
  model_name?: string;
  base_url?: string | null;
  api_key?: string;
  params?: Record<string, unknown>;
  prompt_price_per_million_usd?: string | null;
  completion_price_per_million_usd?: string | null;
  pricing_version?: string | null;
  capability_overrides?: ProviderCapabilityOverrides;
  kind?: "chat" | "embedding";
  is_default?: boolean;
}

export function listNodeTypes() {
  return apiGet<NodeTypeDTO[]>("/api/node-types");
}

export function listModels() {
  return apiGet<ModelConfigDTO[]>("/api/models");
}

export function listProviders() {
  return apiGet<string[]>("/api/models/providers");
}

export function listProviderCapabilities() {
  return apiGet<ProviderDescriptorDTO[]>("/api/models/provider-capabilities");
}

export function getResilienceStatus() {
  return apiGet<ResilienceStatusDTO>("/api/resilience");
}

export function createModel(body: ModelConfigCreate) {
  return apiSend<ModelConfigDTO>("/api/models", "POST", body);
}

export function updateModel(id: string, body: ModelConfigUpdate) {
  return apiSend<ModelConfigDTO>(`/api/models/${id}`, "PUT", body);
}

export function deleteModel(id: string) {
  return apiSend<void>(`/api/models/${id}`, "DELETE");
}
