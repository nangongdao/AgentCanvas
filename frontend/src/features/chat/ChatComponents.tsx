import { Bot, User, X } from "lucide-react";

import { useDialogFocus } from "@/components/useDialogFocus";
import type { WorkflowDTO } from "@/api/endpoints/workflows";
import { CitationCards } from "@/features/citations/CitationCards";
import { cn } from "@/utils/cn";

export interface Turn {
  role: string;
  content: string;
  streaming?: boolean;
  /** Durable message id; absent while a turn is still being streamed. */
  id?: string;
  citations?: Record<string, unknown>[];
}

export function TurnBubble({ turn }: { turn: Turn }) {
  const isUser = turn.role === "user";
  return (
    <div className={cn("flex gap-3 animate-fade-up", isUser && "flex-row-reverse")}>
      <span
        className={cn(
          "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border",
          isUser
            ? "border-pulse/40 bg-pulse/10 text-pulse"
            : "border-volt/40 bg-volt/10 text-volt",
        )}
      >
        {isUser ? <User size={13} /> : <Bot size={13} />}
      </span>
      <div className="min-w-0 max-w-[80%]">
        <div
          className={cn(
            "whitespace-pre-wrap rounded-lg border px-3.5 py-2.5 text-[13px] leading-relaxed",
            isUser
              ? "border-pulse/30 bg-pulse/5 text-ice"
              : "border-line bg-ink/60 text-ice/90",
          )}
        >
          {turn.content || (turn.streaming ? "…" : "")}
          {turn.streaming && (
            <span className="ml-0.5 inline-block h-3 w-1.5 animate-pulse bg-pulse align-middle" />
          )}
        </div>
        {!isUser && !turn.streaming && <CitationCards citations={turn.citations} />}
      </div>
    </div>
  );
}

export function WorkflowPicker({
  workflows,
  onClose,
  onPick,
}: {
  workflows: WorkflowDTO[];
  onClose: () => void;
  onPick: (id: string) => void;
}) {
  const dialogRef = useDialogFocus<HTMLDivElement>({ open: true, onClose });

  return (
    <div
      ref={dialogRef}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-labelledby="chat-workflow-picker-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-void/70 p-4 backdrop-blur-xs"
    >
      <div className="glass w-full max-w-md rounded-xl border border-line p-5 shadow-card animate-fade-up">
        <div className="mb-4 flex items-center justify-between">
          <h3 id="chat-workflow-picker-title" className="font-display text-sm font-semibold text-ice">
            选择工作流发起对话
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="flex h-7 w-7 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice"
            title="关闭"
          >
            <X size={15} />
          </button>
        </div>
        <div className="max-h-80 space-y-1 overflow-auto">
          {workflows.length === 0 ? (
            <p className="text-xs text-ghost/50">
              暂无工作流。请先在画布中创建一个含 agent 节点的工作流。
            </p>
          ) : (
            workflows.map((workflow, index) => (
              <button
                key={workflow.id}
                type="button"
                data-dialog-initial-focus={index === 0 || undefined}
                onClick={() => onPick(workflow.id)}
                className="flex w-full items-center gap-2 rounded-md border border-line bg-ink/60 px-3 py-2.5 text-left transition hover:border-pulse/40 hover:bg-pulse/5"
              >
                <Bot size={14} className="text-pulse" />
                <span className="min-w-0 flex-1 truncate text-[13px] text-ice">{workflow.name}</span>
                <span className="font-mono text-[9px] text-ghost/40">{workflow.id.slice(0, 8)}</span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
