import type { ProviderCapability } from "@/api/endpoints/meta";

export const CAPABILITY_LABELS: Record<ProviderCapability, string> = {
  stream: "流式",
  tools: "工具",
  vision: "视觉",
  json_mode: "JSON",
  reasoning: "推理",
  usage: "用量",
  cost: "成本",
};
