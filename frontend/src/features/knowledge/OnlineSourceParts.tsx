import type { LucideIcon } from "lucide-react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Globe2,
  Loader2,
  Pencil,
  RefreshCw,
  Trash2,
} from "lucide-react";

import type { OnlineSourceDTO, OnlineSourceStatus } from "@/api/endpoints/knowledge";
import { cn } from "@/utils/cn";

export interface SourceDraft {
  url: string;
  maxPages: number;
  depth: number;
  interval: string;
}

export const EMPTY_DRAFT: SourceDraft = {
  url: "",
  maxPages: 1,
  depth: 0,
  interval: "",
};

export const SCHEDULES = [
  { value: "", label: "手动" },
  { value: "15", label: "每 15 分钟" },
  { value: "60", label: "每小时" },
  { value: "360", label: "每 6 小时" },
  { value: "1440", label: "每天" },
  { value: "10080", label: "每周" },
] as const;

export const STATUS: Record<
  OnlineSourceStatus,
  { label: string; className: string; icon: LucideIcon }
> = {
  pending: { label: "pending", className: "text-warn", icon: Clock3 },
  syncing: { label: "syncing", className: "text-pulse", icon: Loader2 },
  ready: { label: "ready", className: "text-ok", icon: CheckCircle2 },
  failed: { label: "failed", className: "text-bad", icon: AlertTriangle },
};

export function intervalValue(value?: number | null): string {
  return value == null ? "" : String(value);
}

export function dateLabel(value?: string | null): string {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function SourceRow(props: {
  source: OnlineSourceDTO;
  canEdit: boolean;
  working: string | null;
  onEdit: () => void;
  onSync: () => void;
  onDelete: () => void;
}) {
  const status = STATUS[props.source.status];
  const StatusIcon = status.icon;
  const busy = props.working?.endsWith(props.source.id) ?? false;
  const schedule =
    SCHEDULES.find(
      (option) => option.value === intervalValue(props.source.sync_interval_minutes),
    )?.label ?? `${props.source.sync_interval_minutes} 分钟`;

  return (
    <article className="grid min-w-0 gap-3 py-3 md:grid-cols-[minmax(0,1fr)_180px_auto] md:items-center">
      <div className="flex min-w-0 items-start gap-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line bg-void/60 text-ghost">
          <Globe2 size={14} />
        </span>
        <div className="min-w-0">
          <p className="truncate text-xs font-medium text-ice" title={props.source.url}>
            {props.source.url}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[9px] text-ghost/50">
            <span className={cn("flex items-center gap-1 uppercase", status.className)}>
              <StatusIcon
                size={10}
                className={cn((busy || props.source.status === "syncing") && "animate-spin")}
              />
              {busy ? "working" : status.label}
            </span>
            <span>{props.source.max_pages} pages</span>
            <span>depth {props.source.depth}</span>
            <span>{schedule}</span>
          </div>
          {props.source.error && (
            <p className="mt-1 wrap-break-word text-[10px] leading-4 text-bad/85">
              {props.source.error}
            </p>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 font-mono text-[9px] text-ghost/50">
        <span>
          <strong className="block font-medium uppercase text-ghost/35">last</strong>
          {dateLabel(props.source.last_synced_at)}
        </span>
        <span>
          <strong className="block font-medium uppercase text-ghost/35">next</strong>
          {dateLabel(props.source.next_sync_at)}
        </span>
      </div>
      {props.canEdit && (
        <div className="flex shrink-0 items-center gap-0.5 md:justify-end">
          <button
            type="button"
            onClick={props.onSync}
            disabled={busy || props.source.status === "syncing"}
            className="flex h-7 w-7 items-center justify-center rounded-md text-ghost transition hover:bg-pulse/10 hover:text-pulse disabled:opacity-40"
            title="立即同步"
            aria-label={`立即同步 ${props.source.url}`}
          >
            <RefreshCw size={12} className={cn(busy && "animate-spin")} />
          </button>
          <button
            type="button"
            onClick={props.onEdit}
            disabled={busy || props.source.status === "syncing"}
            className="flex h-7 w-7 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice disabled:opacity-40"
            title="编辑在线源"
            aria-label={`编辑 ${props.source.url}`}
          >
            <Pencil size={12} />
          </button>
          <button
            type="button"
            onClick={props.onDelete}
            disabled={busy || props.source.status === "syncing"}
            className="flex h-7 w-7 items-center justify-center rounded-md text-ghost transition hover:bg-bad/10 hover:text-bad disabled:opacity-40"
            title="删除在线源"
            aria-label={`删除 ${props.source.url}`}
          >
            <Trash2 size={12} />
          </button>
        </div>
      )}
    </article>
  );
}
