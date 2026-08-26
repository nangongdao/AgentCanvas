import { useEffect, useRef, useState } from "react";
import {
  Activity,
  ChevronDown,
  ChevronUp,
  ListTree,
  Radio,
  ScanSearch,
  SquareTerminal,
  Zap,
} from "lucide-react";

import { resumeExecution, type DebugRunOptions } from "@/api/endpoints/workflows";
import {
  ApprovalPanel,
  CitationList,
  PaneHeader,
  StatusBadge,
} from "@/features/execution/ExecutionConsoleParts";
import { DebugResumePanel } from "@/features/execution/DebugResumePanel";
import { ExecutionInspector } from "@/features/execution/ExecutionInspector";
import { streamBuffer } from "@/features/execution/streamBuffer";
import { useExecutionStore } from "@/stores/executionStore";
import { cn } from "@/utils/cn";

const EVENT_COLOR: Record<string, string> = {
  workflow_started: "text-volt",
  node_started: "text-pulse",
  node_streaming: "text-ghost/60",
  node_finished: "text-ok",
  node_failed: "text-bad",
  edge_taken: "text-warn",
  workflow_finished: "text-ok",
  workflow_failed: "text-bad",
  workflow_cancelled: "text-warn",
  workflow_interrupted: "text-warn",
};

export function ExecutionDrawer() {
  const executionId = useExecutionStore((s) => s.executionId);
  const status = useExecutionStore((s) => s.status);
  const timeline = useExecutionStore((s) => s.timeline);
  const error = useExecutionStore((s) => s.error);
  const output = useExecutionStore((s) => s.output);
  const nodeStatus = useExecutionStore((s) => s.nodeStatus);
  const citations = useExecutionStore((s) => s.citations);
  const pendingApproval = useExecutionStore((s) => s.pendingApproval);
  const debugOptions = useExecutionStore((s) => s.debugOptions);
  const resuming = useExecutionStore((s) => s.resuming);
  const setResuming = useExecutionStore((s) => s.setResuming);

  const [, bump] = useState(0);
  const [open, setOpen] = useState(true);
  const [rightView, setRightView] = useState<"stream" | "inspector">("stream");
  const timelineRef = useRef<HTMLDivElement>(null);
  const streamRef = useRef<HTMLDivElement>(null);

  useEffect(() => streamBuffer.subscribeAll(() => bump((n) => n + 1)), []);
  useEffect(() => setRightView("stream"), [executionId]);

  // Auto-scroll both panes
  useEffect(() => {
    timelineRef.current?.scrollTo({ top: timelineRef.current.scrollHeight });
  }, [timeline.length]);
  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight });
  });

  const compact = timeline.filter(
    (item) => item.type !== "node_streaming" || item.payload?.kind !== "text",
  );
  const streamingNodes = Object.keys(nodeStatus).filter(
    (id) => nodeStatus[id] === "streaming" || nodeStatus[id] === "succeeded",
  );
  const isLive = status === "running";
  const isWaiting = status === "waiting_approval" && pendingApproval !== null;
  const isBreakpoint = isWaiting && pendingApproval?.breakpoint !== null;

  const handleResume = async (approved: boolean) => {
    if (!executionId) return;
    setResuming(true);
    try {
      await resumeExecution(
        executionId,
        { approved, decision: approved ? "approve" : "reject" },
        debugOptions ?? undefined,
      );
    } catch (err) {
      console.error("resume failed", err);
    } finally {
      setResuming(false);
    }
  };

  const handleDebugResume = async (
    decision: Record<string, unknown>,
    debug: DebugRunOptions | null,
  ) => {
    if (!executionId) return;
    setResuming(true);
    try {
      await resumeExecution(executionId, decision, debug ?? undefined);
    } catch (err) {
      console.error("debug resume failed", err);
    } finally {
      setResuming(false);
    }
  };

  if (!executionId && status === "idle") {
    return (
      <div className="glass flex items-center gap-3 border-t border-line px-5 py-2.5">
        <Radio size={13} className="text-ghost/50" />
        <span className="font-mono text-[10px] uppercase tracking-[0.25em] text-ghost/50">
          Standby — 运行工作流后此处呈现实时事件流
        </span>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "glass flex min-w-0 flex-col overflow-hidden border-t border-line transition-all duration-500",
        open ? "h-88 sm:h-72" : "h-10",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex h-10 w-full min-w-0 shrink-0 items-center justify-between gap-2 px-3 text-left sm:px-5"
      >
        <div className="flex min-w-0 flex-1 items-center gap-2 sm:gap-3">
          <span className="relative flex h-2 w-2">
            {isLive && (
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-pulse opacity-60" />
            )}
            <span
              className={cn(
                "relative inline-flex h-2 w-2 rounded-full",
                isLive
                  ? "bg-pulse"
                  : status === "succeeded"
                    ? "bg-ok"
                    : status === "failed"
                      ? "bg-bad"
                      : "bg-ghost",
              )}
            />
          </span>
          <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-ghost">
            Execution Console
          </span>
          <span className="rounded-md border border-line bg-void/60 px-2 py-0.5 font-mono text-[10px] text-ghost">
            {executionId?.slice(0, 8) ?? "--------"}
          </span>
          <StatusBadge status={status} />
          {error && (
            <span className="min-w-0 flex-1 truncate text-[11px] text-bad/90">{error}</span>
          )}
        </div>
        <span className="shrink-0 text-ghost">
          {open ? <ChevronDown size={15} /> : <ChevronUp size={15} />}
        </span>
      </button>

      {open && (
        <div className="flex min-h-0 flex-1 flex-col border-t border-line/60 sm:flex-row">
          {/* Timeline */}
          <div className="flex h-28 w-full shrink-0 flex-col border-b border-line/60 sm:h-auto sm:w-[38%] sm:border-b-0 sm:border-r">
            <PaneHeader icon={<ListTree size={12} />} title="Event Timeline" count={compact.length} />
            <div ref={timelineRef} className="min-h-0 flex-1 overflow-auto px-4 pb-3">
              <ul className="space-y-0.5">
                {compact.map((item) => {
                  const kind = item.payload?.kind;
                  const label =
                    item.type === "node_streaming" && typeof kind === "string"
                      ? kind
                      : item.type;
                  const toolName =
                    typeof item.payload?.tool_name === "string"
                      ? item.payload.tool_name
                      : undefined;
                  const result =
                    label === "tool_result" && typeof item.payload?.result === "string"
                      ? item.payload.result
                      : undefined;
                  return (
                    <li
                      key={`${item.seq}-${item.type}`}
                      className="flex min-w-0 items-baseline gap-2.5 font-mono text-[11px] animate-fade-up"
                      title={result}
                    >
                      <span className="w-7 shrink-0 text-right text-[10px] text-ghost/40">
                        {String(item.seq).padStart(3, "0")}
                      </span>
                      <span
                        className={cn(
                          "shrink-0",
                          label === "tool_call"
                            ? "text-volt"
                            : label === "tool_result"
                              ? "text-ok"
                              : label === "retrieval"
                                ? "text-ok"
                              : EVENT_COLOR[item.type] ?? "text-ghost",
                        )}
                      >
                        {label}
                      </span>
                      <span className="min-w-0 truncate text-ghost/50">
                        {[item.nodeId, toolName, result].filter(Boolean).join(" · ")}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          </div>

          {/* Stream output */}
          <div className="flex min-w-0 flex-1 flex-col">
            <PaneHeader
              icon={
                rightView === "stream" ? (
                  <SquareTerminal size={12} />
                ) : (
                  <ScanSearch size={12} />
                )
              }
              title={rightView === "stream" ? "Token Stream" : "Node Inspector"}
              count={rightView === "stream" ? streamingNodes.length : null}
              actions={
                <div className="flex items-center rounded-md border border-line bg-void/55 p-0.5" role="tablist">
                  {(["stream", "inspector"] as const).map((view) => (
                    <button
                      key={view}
                      type="button"
                      role="tab"
                      aria-selected={rightView === view}
                      onClick={() => setRightView(view)}
                      className={cn(
                        "h-5 rounded-xs px-2 font-mono text-[8px] uppercase",
                        rightView === view
                          ? "bg-line text-ice"
                          : "text-ghost/55 hover:text-ice",
                      )}
                    >
                      {view}
                    </button>
                  ))}
                </div>
              }
            />
            {rightView === "inspector" && executionId ? (
              <ExecutionInspector executionId={executionId} executionStatus={status} />
            ) : (
              <div
                ref={streamRef}
                className="min-h-0 flex-1 space-y-3 overflow-auto px-4 pb-3"
              >
                {streamingNodes.length === 0 && (
                  <p className="flex items-center gap-2 font-mono text-[11px] text-ghost/40">
                    <Activity size={12} className="animate-pulse" />
                    awaiting tokens…
                  </p>
                )}
                {streamingNodes.map((id) => {
                  const live = nodeStatus[id] === "streaming";
                  return (
                    <div key={id} className="animate-fade-up">
                      <div className="mb-1 flex items-center gap-2">
                        <Zap size={11} className={live ? "text-pulse" : "text-ok"} />
                        <span className="font-mono text-[10px] uppercase tracking-widest text-ghost">
                          {id}
                        </span>
                      </div>
                      <pre
                        className={cn(
                          "whitespace-pre-wrap rounded-lg border border-line/70 bg-void/70 px-3 py-2.5",
                          "font-mono text-[11.5px] leading-relaxed text-ice/90",
                          live && "stream-caret",
                        )}
                      >
                        {streamBuffer.get(id) || " "}
                      </pre>
                    </div>
                  );
                })}
                {output && (
                  <div className="animate-fade-up">
                    <div className="mb-1 flex items-center gap-2">
                      <span className="font-mono text-[10px] uppercase tracking-widest text-ok">
                        Final Output
                      </span>
                    </div>
                    <pre className="whitespace-pre-wrap rounded-lg border border-ok/30 bg-ok/5 px-3 py-2.5 font-mono text-[11.5px] leading-relaxed text-ok/90">
                      {JSON.stringify(output, null, 2)}
                    </pre>
                  </div>
                )}
                {isWaiting && isBreakpoint && pendingApproval && (
                  <DebugResumePanel
                    title={pendingApproval.title}
                    instruction={pendingApproval.instruction}
                    nodeId={pendingApproval.breakpoint}
                    nodeOutputs={pendingApproval.nodeOutputs}
                    debugOptions={debugOptions}
                    resuming={resuming}
                    onContinue={handleDebugResume}
                  />
                )}
                {isWaiting && !isBreakpoint && pendingApproval && (
                  <ApprovalPanel
                    title={pendingApproval.title}
                    instruction={pendingApproval.instruction}
                    nodeId={pendingApproval.nodeId}
                    resuming={resuming}
                    onDecide={handleResume}
                  />
                )}
                {citations.length > 0 && <CitationList citations={citations} />}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
