/** Canvas-facing refinements of the backend-generated workflow DSL contract. */

import type { WorkflowDSLContract } from "@/api/contracts";

export type BuiltinNodeType =
  | "start"
  | "agent"
  | "tool"
  | "condition"
  | "switch"
  | "rag"
  | "human"
  | "iteration"
  | "http"
  | "code"
  | "subworkflow"
  | "end";

/** Server-registered plugin nodes are preserved as opaque string types. */
export type NodeType = BuiltinNodeType | (string & {});

/** React Flow uses one stable renderer for all dynamically loaded plugins. */
export const PLUGIN_CANVAS_NODE_TYPE = "plugin";

export function canvasNodeType(type: NodeType): string {
  return type.startsWith("plugin.") ? PLUGIN_CANVAS_NODE_TYPE : type;
}

export interface Position {
  x: number;
  y: number;
}

export interface CanvasGroup {
  id: string;
  name: string;
  position: Position;
  width: number;
  height: number;
  node_ids: string[];
  collapsed: boolean;
  color: string;
}

export interface CanvasNote {
  id: string;
  text: string;
  position: Position;
  width: number;
  height: number;
  color: string;
}

export interface CanvasMetadata {
  groups: CanvasGroup[];
  notes: CanvasNote[];
}

export interface CanvasNodeData extends Record<string, unknown> {
  label: string;
  nodeType: NodeType;
  config: Record<string, unknown>;
}

/** Serialized snapshot of a canvas selection for copy/paste. Node and edge ids
 * are the originals; paste rebuilds them so a pasted subgraph never collides
 * with existing canvas ids (even across workflows). */
export interface SubgraphClipboard {
  nodes: Array<{
    id: string;
    type: NodeType;
    position: { x: number; y: number };
    data: CanvasNodeData;
  }>;
  edges: Array<{
    id: string;
    source: string;
    target: string;
    sourceHandle?: string | null;
    targetHandle?: string | null;
    label?: string;
  }>;
}

export type WorkflowVariable = NonNullable<WorkflowDSLContract["variables"]>[number];

export type WorkflowSettings = Required<
  NonNullable<WorkflowDSLContract["settings"]>
>;

type GeneratedDslNode = NonNullable<WorkflowDSLContract["nodes"]>[number];

export interface DslNode extends Omit<GeneratedDslNode, "type" | "position" | "config"> {
  id: string;
  type: NodeType;
  position: Position;
  config: Record<string, unknown>;
}

export type DslEdge = NonNullable<WorkflowDSLContract["edges"]>[number];

export interface WorkflowDSL extends Omit<
  WorkflowDSLContract,
  "version" | "name" | "variables" | "settings" | "nodes" | "edges" | "canvas"
> {
  version: NonNullable<WorkflowDSLContract["version"]>;
  name: string;
  variables: WorkflowVariable[];
  settings: WorkflowSettings;
  nodes: DslNode[];
  edges: DslEdge[];
  canvas: CanvasMetadata;
}

export const DEFAULT_SETTINGS: WorkflowSettings = {
  max_loop_iterations: 20,
  timeout_seconds: 300,
  recursion_limit: 50,
};

export const NODE_META: Record<
  string,
  { label: string; color: string; description: string }
> = {
  start: { label: "开始", color: "#34d399", description: "工作流入口" },
  agent: { label: "Agent", color: "#22d3ee", description: "LLM 智能节点" },
  tool: { label: "工具", color: "#8b5cf6", description: "MCP 工具调用" },
  condition: { label: "条件", color: "#fbbf24", description: "分支路由" },
  switch: { label: "Switch", color: "#facc15", description: "多路分支聚合" },
  rag: { label: "RAG", color: "#2dd4bf", description: "知识库检索" },
  human: { label: "人工", color: "#fb7185", description: "人工审批" },
  iteration: { label: "迭代", color: "#f97316", description: "数组批处理子流程" },
  http: { label: "HTTP", color: "#0ea5e9", description: "出站 HTTP 请求" },
  code: { label: "Code", color: "#a3e635", description: "沙箱化代码片段" },
  subworkflow: {
    label: "Subworkflow",
    color: "#38bdf8",
    description: "引用已发布版本作为子流程",
  },
  end: { label: "结束", color: "#8b93a7", description: "工作流出口" },
};
