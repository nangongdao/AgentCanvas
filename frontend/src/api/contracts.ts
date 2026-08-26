/** Public contract types generated from backend-owned OpenAPI schemas. */

import type { components, paths } from "@/api/generated/openapi";

export type ApiContractPaths = paths;
export type ApiContractSchema<
  Name extends keyof components["schemas"],
> = components["schemas"][Name];
export type WorkflowDSLContract = components["schemas"]["WorkflowDSL"];
export type ExecutionEventContract = components["schemas"]["ExecutionEvent"];
export type ExecutionEventType = ExecutionEventContract["event_type"];
