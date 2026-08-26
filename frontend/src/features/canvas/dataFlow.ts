import type { JsonSchema } from "@/api/endpoints/meta";

/**
 * C2-8: 变量与数据流窗格的前端分析层。
 *
 * 解析节点 config 中的 `{{...}}` 模板引用,将其分类为
 * `input` / `vars` / `nodes` 三类来源,供 DataFlowPanel 与自定义 edge
 * 渲染使用。后端 `build_context()` 支持的根键为 `input`/`inputs`/`nodes`/
 * `vars`(注意不是 `variables`),此处仅识别这些合法键,避免把无关花括号
 * 误判为引用。
 */

const TEMPLATE_PATTERN = /\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}/g;

/** 模板引用的来源类别。 */
export type ReferenceSource = "input" | "vars" | "nodes";

/** 单条模板引用的解析结果。 */
export interface DataFlowReference {
  /** 原始模板表达式,如 `{{nodes.addone.output.value}}`。 */
  raw: string;
  /** 来源类别。 */
  source: ReferenceSource;
  /** 被引用节点 id(source 为 `nodes` 时)。 */
  nodeId: string | null;
  /** 路径片段,如 `["value"]` 或 `["user_query"]`。 */
  path: string[];
  /** 人类可读的展示标签,如 `addone.output.value`。 */
  label: string;
  /** 引用所在 config 字段路径,如 `user_prompt` 或 `inputs.query`。 */
  fieldPath: string;
}

function classify(root: string): ReferenceSource | null {
  if (root === "input" || root === "inputs") return "input";
  if (root === "nodes") return "nodes";
  if (root === "vars") return "vars";
  return null;
}

function buildLabel(source: ReferenceSource, nodeId: string | null, path: string[]): string {
  if (source === "nodes" && nodeId) {
    return ["nodes", nodeId, ...path].join(".");
  }
  if (source === "input") {
    return ["input", ...path].join(".");
  }
  return [source, ...path].join(".");
}

function parseExpression(expr: string): {
  source: ReferenceSource;
  nodeId: string | null;
  path: string[];
} | null {
  const segments = expr.split(".");
  const root = segments[0];
  const source = classify(root);
  if (source === null) return null;
  if (source === "nodes") {
    // {{nodes.id.output.field}} 或 {{nodes.id.field}}
    if (segments.length < 2) return null;
    const nodeId = segments[1];
    const path = segments.slice(2);
    return { source, nodeId, path };
  }
  // {{input.x}} / {{vars.x}}
  if (segments.length < 2) return null;
  return { source, nodeId: null, path: segments.slice(1) };
}

function walkValue(value: unknown, fieldPath: string, out: DataFlowReference[]): void {
  if (typeof value === "string") {
    let match: RegExpExecArray | null;
    TEMPLATE_PATTERN.lastIndex = 0;
    while ((match = TEMPLATE_PATTERN.exec(value)) !== null) {
      const expr = match[1];
      const parsed = parseExpression(expr);
      if (parsed === null) continue;
      out.push({
        raw: match[0],
        source: parsed.source,
        nodeId: parsed.nodeId,
        path: parsed.path,
        label: buildLabel(parsed.source, parsed.nodeId, parsed.path),
        fieldPath,
      });
    }
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => {
      walkValue(item, `${fieldPath}[${index}]`, out);
    });
    return;
  }
  if (value && typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      const nextField = fieldPath ? `${fieldPath}.${key}` : key;
      walkValue(child, nextField, out);
    }
  }
}

/** 解析节点 config,返回其中所有合法的模板引用。 */
export function extractDataFlowReferences(
  config: Record<string, unknown>,
): DataFlowReference[] {
  const refs: DataFlowReference[] = [];
  walkValue(config, "", refs);
  return refs;
}

/** 返回被引用的上游节点 id 集合(去重)。 */
export function upstreamNodeIds(refs: DataFlowReference[]): string[] {
  const ids = new Set<string>();
  for (const ref of refs) {
    if (ref.source === "nodes" && ref.nodeId) ids.add(ref.nodeId);
  }
  return [...ids];
}

/** 返回被引用的 workflow 变量名集合(去重)。 */
export function referencedVariableNames(refs: DataFlowReference[]): string[] {
  const names = new Set<string>();
  for (const ref of refs) {
    if (ref.source === "vars" && ref.path[0]) names.add(ref.path[0]);
  }
  return [...names];
}

/** 返回被引用的工作流输入名集合(去重)。 */
export function referencedInputNames(refs: DataFlowReference[]): string[] {
  const names = new Set<string>();
  for (const ref of refs) {
    if (ref.source === "input" && ref.path[0]) names.add(ref.path[0]);
  }
  return [...names];
}

/** output schema 字段节点,用于在数据流窗格中展示输出形状。 */
export interface OutputSchemaField {
  name: string;
  description?: string;
  type?: string | string[];
}

/** 从 JsonSchema 提取顶层字段,供数据流窗格预览输出形状。 */
export function describeOutputSchema(
  schema: JsonSchema | undefined,
): OutputSchemaField[] {
  if (!schema || schema.type !== "object") return [];
  const props = schema.properties;
  if (!props) return [];
  return Object.entries(props).map(([name, prop]) => ({
    name,
    description: prop?.description,
    type: prop?.type,
  }));
}
