import { memo, useCallback, useMemo, useState } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";
import { ShieldCheck } from "lucide-react";

import { useExecutionStore } from "@/stores/executionStore";

/**
 * C2-8: 自定义 React Flow edge,运行时在边的中点悬浮显示流经的数据摘要。
 *
 * 摘要取自 source 节点的 bounded output 快照(executionStore.nodeOutputs),
 * 该值由后端 `bounded_json_snapshot` 生成且已脱敏,因此这里直接展示并标注
 * redacted。视觉状态(edge-active/edge-done/edge-taken)由 edge 对象上的
 * className 通过 `.react-flow__edge` 容器应用到 `.react-flow__edge-path`,
 * 因此自定义组件无需自行接收 className。
 */

function summarize(value: unknown): string {
  if (value === undefined || value === null) return "--";
  if (typeof value === "string") {
    return value.length > 80 ? `${value.slice(0, 80)}…` : value;
  }
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  let text: string;
  try {
    text = JSON.stringify(value);
  } catch {
    text = String(value);
  }
  if (text.length > 120) return `${text.slice(0, 120)}…`;
  return text;
}

interface DataFlowEdgeData extends Record<string, unknown> {}

type DataFlowEdgeProps = EdgeProps<Edge<DataFlowEdgeData>>;

function DataFlowEdgeComponent({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  source,
  label,
  style,
}: DataFlowEdgeProps) {
  const [hovered, setHovered] = useState(false);
  const nodeOutputs = useExecutionStore((state) => state.nodeOutputs);
  const sourceOutput = source ? nodeOutputs[source] : undefined;
  const showLabel = hovered && sourceOutput !== undefined;
  const summary = showLabel ? summarize(sourceOutput) : "";

  // Cache the bezier path; it only depends on edge geometry, not on run state.
  const [edgePath, labelX, labelY] = useMemo(
    () =>
      getSmoothStepPath({
        sourceX,
        sourceY,
        targetX,
        targetY,
        sourcePosition,
        targetPosition,
      }),
    [sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition],
  );

  const onMouseEnter = useCallback(() => setHovered(true), []);
  const onMouseLeave = useCallback(() => setHovered(false), []);

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        style={style}
        interactionWidth={20}
      />
      <EdgeLabelRenderer>
        <div
          className="absolute"
          style={{
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            pointerEvents: "all",
          }}
          onMouseEnter={onMouseEnter}
          onMouseLeave={onMouseLeave}
        >
          {typeof label === "string" && label.length > 0 && (
            <div className="glass mb-1 max-w-52 rounded border border-pulse/40 bg-pulse/10 px-2 py-1 font-mono text-[9px] text-pulse shadow-card">
              {label}
            </div>
          )}
          {showLabel && (
            <div className="glass animate-fade-up flex max-w-64 items-center gap-1.5 rounded-md border border-line px-2 py-1 shadow-card">
              <ShieldCheck size={9} className="shrink-0 text-ok/70" />
              <code className="truncate font-mono text-[9px] text-ice/80">{summary}</code>
            </div>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

export const dataFlowEdgeTypes = {
  dataflow: memo(DataFlowEdgeComponent),
};

export type DataFlowEdge = Edge<DataFlowEdgeData>;
