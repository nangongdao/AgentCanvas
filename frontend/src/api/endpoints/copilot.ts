/** Workflow AI Copilot: natural language in, validated DSL draft out. */

import { apiSend } from "@/api/client";
import type { ApiContractSchema } from "@/api/contracts";
import type { WorkflowDSL } from "@/types/dsl";

export type CopilotUsageDTO = ApiContractSchema<"CopilotUsageOut">;

/** The draft's `dsl` is narrowed from the generic contract object to the
 * canvas's own DSL type so it can be applied without a cast. `errors` and
 * `warnings` are narrowed to required arrays because the backend always
 * serializes them (they default to empty lists). */
export type CopilotDraftDTO = Omit<
  ApiContractSchema<"CopilotDraftOut">,
  "dsl" | "errors" | "warnings"
> & {
  dsl: WorkflowDSL;
  errors: string[];
  warnings: string[];
};

export interface CopilotDraftRequest {
  prompt: string;
  /** Send the canvas as context so the model can modify it in place. */
  base_dsl?: WorkflowDSL;
  project_id?: string;
  model_config_id?: string;
}

export function draftWorkflowWithCopilot(
  body: CopilotDraftRequest,
): Promise<CopilotDraftDTO> {
  return apiSend<CopilotDraftDTO>("/api/workflows/copilot/draft", "POST", body);
}
