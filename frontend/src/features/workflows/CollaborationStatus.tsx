import {
  Eye,
  Loader2,
  LockKeyhole,
  RefreshCw,
  UnlockKeyhole,
  Users,
} from "lucide-react";

import type { WorkflowCollaborationState } from "@/features/workflows/useWorkflowCollaboration";
import { cn } from "@/utils/cn";

interface Props {
  collaboration: WorkflowCollaborationState;
  roleCanEdit: boolean;
}

function initial(subject: string): string {
  return subject.trim().charAt(0).toUpperCase() || "?";
}

export function CollaborationStatus({ collaboration, roleCanEdit }: Props) {
  if (!collaboration.workflowId) return null;

  const { connection, editingAllowed, snapshot, takeoverBusy } = collaboration;
  const participants = snapshot?.participants ?? [];
  const lock = snapshot?.lock;
  const disconnected = connection === "offline";
  const connecting = connection === "connecting";

  return (
    <div
      className="flex h-8 shrink-0 items-center gap-1.5 rounded-md border border-line bg-ink/80 px-2"
      data-testid="collaboration-status"
    >
      <div
        className="flex items-center gap-1 text-ghost"
        title={`${participants.length} 位成员在线`}
      >
        <Users size={13} />
        <span
          className="font-mono text-[9px]"
          data-testid="collaboration-participant-count"
        >
          {participants.length}
        </span>
        <span className="hidden -space-x-1.5 xl:flex">
          {participants.slice(0, 3).map((participant) => (
            <span
              key={participant.client_id}
              className={cn(
                "flex h-5 w-5 items-center justify-center rounded-full border border-void bg-line text-[8px] font-semibold text-ice",
                participant.is_self && "bg-pulse text-void",
              )}
              title={participant.subject}
            >
              {initial(participant.subject)}
            </span>
          ))}
        </span>
      </div>

      <span className="h-4 w-px bg-line" />

      {connecting && (
        <span className="flex items-center gap-1 text-[10px] text-ghost">
          <Loader2 size={11} className="animate-spin" /> 连接中
        </span>
      )}
      {disconnected && (
        <button
          type="button"
          onClick={collaboration.retry}
          className="flex items-center gap-1 text-[10px] text-bad hover:text-ice"
          title="重新连接协作服务"
        >
          <RefreshCw size={11} /> 重连
        </button>
      )}
      {connection === "online" && editingAllowed && (
        <span className="flex items-center gap-1 text-[10px] text-ok">
          <LockKeyhole size={11} /> 我在编辑
        </span>
      )}
      {connection === "online" && !editingAllowed && (
        <span
          className="hidden max-w-32 truncate text-[10px] text-warn lg:inline"
          data-testid="collaboration-lock-owner"
          title={lock?.subject ?? "当前无人编辑"}
        >
          {lock ? `${lock.subject} 正在编辑` : "当前无人编辑"}
        </span>
      )}

      {connection === "online" && roleCanEdit && !editingAllowed && (
        <button
          type="button"
          disabled={takeoverBusy}
          onClick={() => void collaboration.takeover()}
          className="flex items-center gap-1 text-[10px] font-semibold text-pulse hover:text-ice disabled:opacity-40"
          title={lock ? "接管工作流编辑权" : "开始编辑工作流"}
          data-testid="collaboration-takeover"
        >
          {takeoverBusy ? (
            <Loader2 size={11} className="animate-spin" />
          ) : lock ? (
            <UnlockKeyhole size={11} />
          ) : (
            <LockKeyhole size={11} />
          )}
          {lock ? "接管" : "编辑"}
        </button>
      )}
      {connection === "online" && !roleCanEdit && (
        <span className="flex items-center gap-1 text-[10px] text-ghost">
          <Eye size={11} /> 只读
        </span>
      )}
    </div>
  );
}
