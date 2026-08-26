import { memo } from "react";
import {
  Handle,
  Position,
  useConnection,
  type NodeProps,
} from "@xyflow/react";
import {
  Bot,
  CircleCheck,
  CircleDot,
  CircleX,
  Database,
  FlagTriangleRight,
  GitBranch,
  Loader2,
  Play,
  Puzzle,
  Repeat2,
  UserCheck,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import { isLegalTarget } from "@/features/canvas/connectionRules";
import type { CanvasNodeData, NodeType } from "@/types/dsl";
import { NODE_META } from "@/types/dsl";
import { useExecutionStore, type NodeRunStatus } from "@/stores/executionStore";
import { useWorkflowStore } from "@/stores/workflowStore";
import { cn } from "@/utils/cn";

/** The connection-state object React Flow passes to a `useConnection` selector. */
type ConnectionStateValue = Parameters<
  NonNullable<Parameters<typeof useConnection>[0]>
>[0];

const NODE_ICON: Record<string, LucideIcon> = {
  start: Play,
  agent: Bot,
  tool: Wrench,
  condition: GitBranch,
  rag: Database,
  human: UserCheck,
  iteration: Repeat2,
  end: FlagTriangleRight,
};

const STATUS_STYLE: Record<NodeRunStatus, string> = {
  idle: "",
  queued: "border-warn/60! shadow-glow-amber",
  running: "border-pulse! shadow-glow-cyan",
  streaming: "border-pulse! shadow-glow-cyan",
  succeeded: "border-ok/70! shadow-glow-ok",
  failed: "border-bad! shadow-glow-bad",
  skipped: "opacity-50",
};

function StatusIndicator({ status }: { status: NodeRunStatus }) {
  if (status === "idle" || status === "skipped") return null;
  if (status === "queued")
    return <CircleDot size={12} className="text-warn" />;
  if (status === "running" || status === "streaming")
    return <Loader2 size={12} className="animate-spin text-pulse" />;
  if (status === "succeeded") return <CircleCheck size={12} className="text-ok" />;
  if (status === "failed") return <CircleX size={12} className="text-bad" />;
  return <CircleDot size={12} className="text-ghost" />;
}

function conditionHandles(config: Record<string, unknown>): string[] {
  const branches = Array.isArray(config.branches) ? config.branches : [];
  const ids = branches
    .map((b) => (b && typeof b === "object" ? (b as { id?: unknown }).id : undefined))
    .filter((v): v is string => typeof v === "string" && v.length > 0);
  const fallback = typeof config.default_branch === "string" && config.default_branch
    ? config.default_branch
    : "else";
  if (!ids.includes(fallback)) ids.push(fallback);
  return ids.length > 0 ? ids : ["true", "else"];
}

/** C5-7: read the node id the user is currently dragging a connection from
 * (null when no connection is in progress). React Flow's `useConnection` hook
 * exposes the live connection state with a selector so each BaseNode only
 * re-renders when its own target-membership flips, preserving node memoization
 * on large canvases. */
const connectionSourceSelector = (
  connection: ConnectionStateValue,
): string | null =>
  connection && connection.inProgress
    ? (connection.fromHandle?.nodeId ?? null)
    : null;

function BaseNodeComponent({
  data,
  selected,
  dragging,
  id,
}: NodeProps & { data: CanvasNodeData }) {
  const meta = NODE_META[data.nodeType] ?? {
    label: data.nodeType,
    color: "#a78bfa",
    description: "外部插件节点",
  };
  const Icon = NODE_ICON[data.nodeType] ?? Puzzle;
  const isTerminal = data.nodeType === "start" || data.nodeType === "end";
  const runStatus = useExecutionStore((s) => s.nodeStatus[id] ?? "idle");
  const isQueued = useExecutionStore((s) => s.queuedNodes[id] === true);
  const isSkipped = useExecutionStore((s) => s.skippedNodes[id] === true);
  const isActive = runStatus === "running" || runStatus === "streaming";
  const effectiveStatus: NodeRunStatus = isSkipped
    ? "skipped"
    : isQueued && runStatus === "idle"
      ? "queued"
      : runStatus;

  // C5-7: highlight this node as a legal connection target while the user
  // drags a connection line from another node's source handle.
  const connectionSourceId = useConnection(connectionSourceSelector);
  const sourceNodeType: NodeType | null = useWorkflowStore((state) => {
    if (!connectionSourceId) return null;
    const source = state.nodes.find((node) => node.id === connectionSourceId);
    return source ? source.data.nodeType : null;
  });
  const isConnectionTarget =
    connectionSourceId !== null &&
    connectionSourceId !== id &&
    isLegalTarget(sourceNodeType, data.nodeType);

  return (
    <div
      className={cn(
        "node-enter group relative min-w-[168px] border bg-ink/90 backdrop-blur-md transition-all duration-300",
        dragging && "transition-none",
        isTerminal ? "rounded-full px-4 py-2.5" : "rounded-xl px-3.5 py-3",
        selected
          ? "border-pulse/80 shadow-glow-cyan"
          : "border-line hover:border-ghost/40 hover:shadow-card",
        STATUS_STYLE[effectiveStatus],
        isConnectionTarget && "ring-2 ring-pulse/70 border-pulse/60",
      )}
      data-node-id={id}
    >
      {isActive && <span className="node-running-sweep" />}

      {data.nodeType !== "start" && (
        <Handle
          type="target"
          position={Position.Left}
          className="h-2.5! w-2.5! border-2! border-void! bg-ghost!"
        />
      )}

      <div className="relative flex items-center gap-2.5">
        <span
          className={cn(
            "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border transition-colors",
            isActive ? "border-pulse/50 bg-pulse/10" : "border-line bg-void/60",
          )}
          style={{ color: meta.color }}
        >
          <Icon size={15} strokeWidth={1.8} />
        </span>
        <div className="flex min-w-0 flex-col">
          <span className="flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-[0.18em] text-ghost">
            {meta.label}
            <StatusIndicator status={effectiveStatus} />
          </span>
          <span className="truncate text-[13px] font-medium text-ice">{data.label}</span>
        </div>
      </div>

      {!isTerminal && data.nodeType !== "condition" && (
        <Handle
          type="source"
          position={Position.Right}
          className="h-2.5! w-2.5! border-2! border-void! bg-ghost!"
        />
      )}
      {data.nodeType === "condition" && (
        <>
          {conditionHandles(data.config).map((handleId, i, arr) => (
            <Handle
              key={handleId}
              type="source"
              id={handleId}
              position={Position.Right}
              style={{ top: `${Math.round(((i + 1) / (arr.length + 1)) * 100)}%` }}
              title={handleId}
              className={cn(
                "h-2.5! w-2.5! border-2! border-void!",
                i === arr.length - 1 ? "bg-bad!" : "bg-ok!",
              )}
            />
          ))}
        </>
      )}
      {data.nodeType === "start" && (
        <Handle
          type="source"
          position={Position.Right}
          className="h-2.5! w-2.5! border-2! border-void! bg-ok!"
        />
      )}
    </div>
  );
}

export const BaseNode = memo(BaseNodeComponent);
