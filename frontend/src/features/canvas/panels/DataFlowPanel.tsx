import { memo, useEffect, useMemo, useState } from "react";
import { ArrowRight, Braces, GitBranch, ShieldCheck } from "lucide-react";

import { listNodeTypes, type JsonSchema } from "@/api/endpoints/meta";
import { useExecutionStore } from "@/stores/executionStore";
import { useWorkflowStore } from "@/stores/workflowStore";
import type { NodeType } from "@/types/dsl";
import {
  describeOutputSchema,
  extractDataFlowReferences,
  referencedInputNames,
  referencedVariableNames,
  upstreamNodeIds,
  type DataFlowReference,
} from "@/features/canvas/dataFlow";
import { cn } from "@/utils/cn";

/**
 * C2-8: 变量与数据流窗格。
 *
 * 选中节点时显示其输入来源(上游变量引用解析结果)、output schema 与运行
 * 后内联的实际值。运行值来自 executionStore 的 nodeOutputs,该数据由后端
 * `bounded_json_snapshot` 生成且已脱敏,因此这里直接展示并标注 redacted。
 */

interface DataFlowPanelProps {
  nodeType: NodeType;
  config: Record<string, unknown>;
  nodeId: string;
}

function formatJson(value: unknown): string {
  if (value === undefined || value === null) return "--";
  return JSON.stringify(value, null, 2);
}

function summarizeValue(value: unknown): string {
  if (value === undefined || value === null) return "--";
  if (typeof value === "string") return value.length > 120 ? `${value.slice(0, 120)}…` : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    const text = JSON.stringify(value);
    return text.length > 160 ? `${text.slice(0, 160)}…` : text;
  } catch {
    return String(value);
  }
}

function resolveReferenceValue(
  ref: DataFlowReference,
  inputs: Record<string, unknown>,
  variables: { name: string; default?: unknown }[],
  nodeOutputs: Record<string, unknown>,
): unknown {
  if (ref.source === "input") {
    let cursor: unknown = inputs;
    for (const segment of ref.path) {
      if (cursor && typeof cursor === "object" && segment in (cursor as Record<string, unknown>)) {
        cursor = (cursor as Record<string, unknown>)[segment];
      } else {
        return undefined;
      }
    }
    return cursor;
  }
  if (ref.source === "vars") {
    const variable = variables.find((item) => item.name === ref.path[0]);
    return variable?.default ?? undefined;
  }
  if (ref.source === "nodes" && ref.nodeId) {
    const output = nodeOutputs[ref.nodeId];
    let cursor: unknown = output;
    // 后端 build_context 把节点输出规范化为 {output: ..., **output},
    // 因此 {{nodes.id.output.x}} 取 output.x,{{nodes.id.x}} 取 x。
    for (const segment of ref.path) {
      if (cursor && typeof cursor === "object") {
        cursor = (cursor as Record<string, unknown>)[segment];
      } else {
        return undefined;
      }
    }
    return cursor;
  }
  return undefined;
}

function DataFlowPanelComponent({ nodeType, config, nodeId }: DataFlowPanelProps) {
  const nodes = useWorkflowStore((state) => state.nodes);
  const variables = useWorkflowStore((state) => state.variables);
  const nodeOutputs = useExecutionStore((state) => state.nodeOutputs);
  const runInputs = useExecutionStore((state) => state.runInputs);
  const executionId = useExecutionStore((state) => state.executionId);
  const [schemas, setSchemas] = useState<Partial<Record<NodeType, JsonSchema>>>({});
  const [schemaLoading, setSchemaLoading] = useState(true);

  useEffect(() => {
    let active = true;
    void listNodeTypes()
      .then((rows) => {
        if (!active) return;
        setSchemas(
          Object.fromEntries(
            rows.map((row) => [row.type, row.output_schema]).filter(([, schema]) => schema),
          ) as Partial<Record<NodeType, JsonSchema>>,
        );
        setSchemaLoading(false);
      })
      .catch(() => {
        if (active) setSchemaLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const refs = useMemo(() => extractDataFlowReferences(config), [config]);
  const upstreamIds = useMemo(() => upstreamNodeIds(refs), [refs]);
  const inputNames = useMemo(() => referencedInputNames(refs), [refs]);
  const varNames = useMemo(() => referencedVariableNames(refs), [refs]);
  const outputFields = useMemo(
    () => describeOutputSchema(schemas[nodeType]),
    [schemas, nodeType],
  );

  const nodeLabel = (id: string): string => {
    const match = nodes.find((node) => node.id === id);
    return match?.data.label ?? id;
  };

  const upstreamNodes = useMemo(
    () => upstreamIds.map((id) => ({ id, label: nodeLabel(id) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [upstreamIds, nodes],
  );

  const liveOutput = executionId ? nodeOutputs[nodeId] : undefined;
  const hasRefs = refs.length > 0;
  const hasOutput = outputFields.length > 0 || liveOutput !== undefined;

  if (!hasRefs && !hasOutput) {
    return null;
  }

  return (
    <section className="rounded-md border border-line/70 bg-void/40 p-3">
      <header className="mb-3 flex items-center gap-2">
        <GitBranch size={12} className="text-pulse" />
        <h3 className="font-mono text-[9px] uppercase tracking-[0.2em] text-ghost">
          Data Flow
        </h3>
        {executionId && (
          <span className="ml-auto flex items-center gap-1 font-mono text-[9px] text-ok/70">
            <ShieldCheck size={10} /> redacted
          </span>
        )}
      </header>

      <div className="space-y-4">
        {hasRefs && (
          <div>
            <p className="mb-1.5 font-mono text-[9px] uppercase tracking-[0.2em] text-ghost/60">
              输入来源
            </p>
            <ul className="space-y-1.5">
              {upstreamNodes.map((node) => (
                <li key={node.id} className="flex items-center gap-1.5 text-[11px]">
                  <ArrowRight size={10} className="text-ghost/50" />
                  <span className="truncate text-ice/80">{node.label}</span>
                  <span className="font-mono text-[9px] text-ghost/45">{node.id}</span>
                </li>
              ))}
            </ul>
            <ul className="mt-1.5 space-y-1">
              {inputNames.map((name) => (
                <li key={`input-${name}`} className="flex items-center gap-1.5 text-[11px]">
                  <span className="font-mono text-[9px] text-pulse/70">input</span>
                  <span className="truncate text-ice/80">{name}</span>
                </li>
              ))}
              {varNames.map((name) => (
                <li key={`var-${name}`} className="flex items-center gap-1.5 text-[11px]">
                  <span className="font-mono text-[9px] text-volt/70">vars</span>
                  <span className="truncate text-ice/80">{name}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {outputFields.length > 0 && (
          <div>
            <p className="mb-1.5 font-mono text-[9px] uppercase tracking-[0.2em] text-ghost/60">
              输出 schema
            </p>
            <ul className="space-y-1">
              {outputFields.map((field) => (
                <li key={field.name} className="text-[11px]">
                  <span className="font-mono text-[10px] text-ice/80">{field.name}</span>
                  {field.type && (
                    <span className="ml-1.5 font-mono text-[9px] text-ghost/50">
                      {field.type}
                    </span>
                  )}
                  {field.description && (
                    <span className="ml-1.5 text-[10px] text-ghost/60">
                      {field.description}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {liveOutput !== undefined && (
          <div>
            <p className="mb-1.5 flex items-center gap-1 font-mono text-[9px] uppercase tracking-[0.2em] text-ghost/60">
              <Braces size={10} /> 运行值
            </p>
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded border border-line/50 bg-void/60 p-2 font-mono text-[10px] leading-4 text-ice/80">
              {formatJson(liveOutput)}
            </pre>
          </div>
        )}

        {hasRefs && executionId && (
          <div>
            <p className="mb-1.5 font-mono text-[9px] uppercase tracking-[0.2em] text-ghost/60">
              引用解析
            </p>
            <ul className="space-y-1">
              {refs.slice(0, 6).map((ref, index) => {
                const value = resolveReferenceValue(
                  ref,
                  runInputs ?? {},
                  variables,
                  nodeOutputs,
                );
                return (
                  <li
                    // eslint-disable-next-line react/no-array-index-key
                    key={`${ref.raw}-${index}`}
                    className="flex min-w-0 items-baseline gap-1.5 text-[10px]"
                  >
                    <code className="truncate font-mono text-ghost/60">{ref.raw}</code>
                    <span className="shrink-0 text-ghost/40">→</span>
                    <code
                      className={cn(
                        "truncate font-mono",
                        value === undefined ? "text-warn/60" : "text-ok/70",
                      )}
                    >
                      {summarizeValue(value)}
                    </code>
                  </li>
                );
              })}
              {refs.length > 6 && (
                <li className="text-[10px] text-ghost/50">+{refs.length - 6} 条引用</li>
              )}
            </ul>
          </div>
        )}
      </div>

      {schemaLoading && (
        <p className="mt-2 text-[10px] text-ghost/50">加载输出 schema…</p>
      )}
    </section>
  );
}

export const DataFlowPanel = memo(DataFlowPanelComponent);
