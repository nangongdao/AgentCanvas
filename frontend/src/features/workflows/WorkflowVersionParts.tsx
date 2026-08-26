import {
  Check,
  Copy,
  Download,
  GitCompareArrows,
  Loader2,
  RotateCcw,
} from "lucide-react";

import type {
  WorkflowDiffDTO,
  WorkflowVersionDTO,
  WorkflowVersionStatus,
} from "@/api/endpoints/workflows";
import { cn } from "@/utils/cn";

const STATUS_STYLE: Record<WorkflowVersionStatus, string> = {
  draft: "border-pulse/40 bg-pulse/10 text-pulse",
  published: "border-ok/40 bg-ok/10 text-ok",
  archived: "border-line bg-line/35 text-ghost",
};

function formatDate(value?: string): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function WorkflowDiffSummary({ diff }: { diff: WorkflowDiffDTO }) {
  const rows = [
    ["节点新增", diff.added_nodes],
    ["节点移除", diff.removed_nodes],
    ["节点变更", diff.changed_nodes],
    ["连线新增", diff.added_edges],
    ["连线移除", diff.removed_edges],
    ["连线变更", diff.changed_edges],
  ] as const;
  const empty = rows.every(([, items]) => items.length === 0);

  return (
    <section className="border-b border-line bg-void/45 px-4 py-3">
      <div className="mb-2 flex items-center gap-2">
        <GitCompareArrows size={13} className="text-volt" />
        <span className="font-mono text-[10px] uppercase text-ghost">
          Version diff
        </span>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
        {rows.map(([label, items]) => (
          <p key={label} className="flex min-w-0 gap-2 text-ghost/75">
            <span className="shrink-0">{label}</span>
            <span className="truncate font-mono text-ice" title={items.join(", ")}>
              {items.length ? items.join(", ") : "--"}
            </span>
          </p>
        ))}
      </div>
      <p className="mt-2 font-mono text-[10px] text-ghost/60">
        {diff.settings_changed ? "settings changed" : "settings stable"} ·{" "}
        {diff.variables_changed ? "variables changed" : "variables stable"}
        {" · "}
        {diff.canvas_changed ? "canvas objects changed" : "canvas stable"}
        {empty && !diff.settings_changed && !diff.variables_changed
          && !diff.canvas_changed
          ? " · identical"
          : ""}
      </p>
    </section>
  );
}

interface VersionRowProps {
  row: WorkflowVersionDTO;
  currentVersion: number;
  selectedForDiff: boolean;
  canEdit: boolean;
  action: string | null;
  onToggleCompare: (versionId: string) => void;
  onExport: (row: WorkflowVersionDTO) => void;
  onRollback: (row: WorkflowVersionDTO) => void;
  onClone: (row: WorkflowVersionDTO) => void;
}

export function WorkflowVersionRow({
  row,
  currentVersion,
  selectedForDiff,
  canEdit,
  action,
  onToggleCompare,
  onExport,
  onRollback,
  onClone,
}: VersionRowProps) {
  const isCurrent = row.number === currentVersion;

  return (
    <li className="px-4 py-3 hover:bg-ink/65">
      <div className="flex items-start gap-3">
        <button
          type="button"
          onClick={() => onToggleCompare(row.id)}
          className={cn(
            "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-xs border",
            selectedForDiff
              ? "border-volt bg-volt text-void"
              : "border-line text-transparent hover:border-volt/60",
          )}
          title="选择用于对比"
          aria-label={`选择版本 ${row.number} 用于对比`}
        >
          <Check size={12} />
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm font-semibold text-ice">
              v{row.number}
            </span>
            <span
              className={cn(
                "rounded-xs border px-1.5 py-0.5 font-mono text-[9px] uppercase",
                STATUS_STYLE[row.status],
              )}
            >
              {row.status}
            </span>
            {isCurrent && (
              <span className="font-mono text-[9px] uppercase text-pulse">
                current
              </span>
            )}
          </div>
          <p className="mt-1 truncate text-xs text-ice/80">{row.name}</p>
          <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-ghost/70">
            {row.change_summary || "No change note"}
          </p>
          <p className="mt-2 font-mono text-[9px] text-ghost/50">
            {formatDate(row.created_at)} · {row.dsl.nodes.length} nodes ·{" "}
            {row.dsl.edges.length} edges
          </p>
        </div>
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          disabled={action !== null}
          onClick={() => onExport(row)}
          className="flex h-7 w-7 items-center justify-center rounded-md border border-line text-ghost hover:border-ice/35 hover:text-ice disabled:opacity-40"
          title={`导出 v${row.number}`}
          aria-label={`导出版本 ${row.number}`}
        >
          {action === `export:${row.id}` ? (
            <Loader2 size={11} className="animate-spin" />
          ) : (
            <Download size={11} />
          )}
        </button>
        {canEdit && !isCurrent && (
          <button
            type="button"
            disabled={action !== null}
            onClick={() => onRollback(row)}
            className="flex h-7 items-center gap-1.5 rounded-md border border-line px-2.5 text-[10px] text-ghost hover:border-warn/40 hover:text-warn disabled:opacity-40"
          >
            {action === `rollback:${row.id}` ? (
              <Loader2 size={11} className="animate-spin" />
            ) : (
              <RotateCcw size={11} />
            )}
            恢复
          </button>
        )}
        {canEdit && (
          <button
            type="button"
            disabled={action !== null}
            onClick={() => onClone(row)}
            className="flex h-7 items-center gap-1.5 rounded-md border border-line px-2.5 text-[10px] text-ghost hover:border-pulse/40 hover:text-pulse disabled:opacity-40"
          >
            {action === `clone:${row.id}` ? (
              <Loader2 size={11} className="animate-spin" />
            ) : (
              <Copy size={11} />
            )}
            克隆
          </button>
        )}
      </div>
    </li>
  );
}
