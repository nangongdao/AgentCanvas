import type { ReactNode } from "react";
import {
  BookOpenCheck,
  CheckCircle2,
  ShieldCheck,
  XCircle,
} from "lucide-react";

import type { ExecutionCitation } from "@/stores/executionStore";
import { cn } from "@/utils/cn";

export function CitationList({ citations }: { citations: ExecutionCitation[] }) {
  return (
    <section className="border-t border-line/70 pt-3 animate-fade-up">
      <div className="mb-2 flex items-center gap-2">
        <BookOpenCheck size={12} className="text-ok" />
        <span className="font-mono text-[9px] uppercase text-ghost">
          Sources / {citations.length}
        </span>
      </div>
      <div className="grid gap-1.5 lg:grid-cols-2">
        {citations.map((citation) => (
          <details
            key={citation.id}
            className="group rounded-md border border-line bg-void/55 px-3 py-2"
          >
            <summary className="flex cursor-pointer list-none items-center gap-2 text-[11px]">
              <span className="font-mono text-ok">{citation.label}</span>
              <span className="min-w-0 flex-1 truncate text-ice">{citation.filename}</span>
              <span className="font-mono text-[9px] text-ghost/50">
                {citation.score.toFixed(3)}
              </span>
            </summary>
            <p className="mt-2 text-[10px] leading-4 text-ghost/75">
              {citation.page ? `page ${citation.page} / ` : ""}chunk {citation.chunkIndex}
            </p>
            <p className="mt-1 whitespace-pre-wrap text-[11px] leading-5 text-ice/80">
              {citation.text}
            </p>
          </details>
        ))}
      </div>
    </section>
  );
}

export function PaneHeader({
  icon,
  title,
  count,
  actions,
}: {
  icon: ReactNode;
  title: string;
  count: number | null;
  actions?: ReactNode;
}) {
  return (
    <div className="flex items-center gap-2 px-4 py-2">
      <span className="text-ghost/60">{icon}</span>
      <span className="font-mono text-[9px] uppercase tracking-[0.25em] text-ghost/60">
        {title}
      </span>
      {count !== null && (
        <span
          className={cn(
            "rounded-xs bg-line/50 px-1.5 font-mono text-[9px] text-ghost/60",
            !actions && "ml-auto",
          )}
        >
          {count}
        </span>
      )}
      {actions && <span className="ml-auto">{actions}</span>}
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    running: "border-pulse/40 bg-pulse/10 text-pulse",
    succeeded: "border-ok/40 bg-ok/10 text-ok",
    failed: "border-bad/40 bg-bad/10 text-bad",
    cancelled: "border-warn/40 bg-warn/10 text-warn",
    waiting_approval: "border-warn/50 bg-warn/10 text-warn",
    idle: "border-line bg-void/50 text-ghost",
  };
  return (
    <span
      className={cn(
        "rounded-md border px-2 py-0.5 font-mono text-[9px] font-medium uppercase tracking-[0.2em]",
        map[status] ?? map.idle,
      )}
    >
      {status}
    </span>
  );
}

export function ApprovalPanel({
  title,
  instruction,
  nodeId,
  resuming,
  onDecide,
}: {
  title: string;
  instruction: string;
  nodeId: string | null;
  resuming: boolean;
  onDecide: (approved: boolean) => void;
}) {
  return (
    <div className="animate-fade-up rounded-lg border border-warn/40 bg-warn/5 px-3 py-3">
      <div className="mb-2 flex items-center gap-2">
        <ShieldCheck size={13} className="text-warn" />
        <span className="font-mono text-[10px] uppercase tracking-widest text-warn">
          人工审批 · {nodeId ?? "human"}
        </span>
      </div>
      <p className="mb-1 text-[12px] font-medium text-ice">{title}</p>
      <p className="mb-3 whitespace-pre-wrap text-[11.5px] leading-5 text-ghost/80">
        {instruction}
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={resuming}
          onClick={() => onDecide(true)}
          className="flex items-center gap-1.5 rounded-md border border-ok/50 bg-ok/15 px-3 py-1.5 text-[11px] font-medium text-ok transition hover:bg-ok/25 disabled:opacity-50"
        >
          <CheckCircle2 size={13} />
          批准
        </button>
        <button
          type="button"
          disabled={resuming}
          onClick={() => onDecide(false)}
          className="flex items-center gap-1.5 rounded-md border border-bad/50 bg-bad/15 px-3 py-1.5 text-[11px] font-medium text-bad transition hover:bg-bad/25 disabled:opacity-50"
        >
          <XCircle size={13} />
          驳回
        </button>
        {resuming && (
          <span className="ml-1 self-center font-mono text-[10px] text-ghost/60">
            resuming…
          </span>
        )}
      </div>
    </div>
  );
}
