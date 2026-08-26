import { useCallback, useEffect, useState, type ReactElement } from "react";
import {
  CircleCheck,
  CircleX,
  History,
  Loader2,
  MinusCircle,
  RefreshCw,
  X,
} from "lucide-react";

import { listExecutions, type ExecutionDTO } from "@/api/endpoints/workflows";
import { cn } from "@/utils/cn";

const STATUS_ICON: Record<string, ReactElement> = {
  succeeded: <CircleCheck size={12} className="text-ok" />,
  failed: <CircleX size={12} className="text-bad" />,
  cancelled: <MinusCircle size={12} className="text-warn" />,
  running: <Loader2 size={12} className="animate-spin text-pulse" />,
};

const SOURCE_LABEL: Record<string, string> = {
  manual: "MAN",
  webhook: "HOOK",
  schedule: "CRON",
  api: "API",
};

function formatTime(iso?: string | null): string {
  if (!iso) return "--:--:--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--:--";
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}

function duration(start?: string | null, end?: string | null): string {
  if (!start || !end) return "";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

interface Props {
  workflowId: string | null;
}

/** Header popover listing past executions of the current workflow. */
export function ExecutionHistory({ workflowId }: Props) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<ExecutionDTO[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!workflowId) return;
    setLoading(true);
    try {
      setRows((await listExecutions(workflowId)).items);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [workflowId]);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  // C6-3 evaluation outcome: the execution-history popover is a low-frequency
  // surface and the canvas already streams the live run over its own SSE
  // channel, so a dedicated workflow-level summary push channel would cost
  // more (new endpoint + cross-execution fan-out + connection management)
  // than it returns. While the popover is open we instead poll only ACTIVE
  // executions (C6-1's ?status= cold/hot filter) every few seconds; any
  // still-running row triggers a full list refresh so terminal states appear
  // without a manual reload. Polling stops when the popover closes.
  useEffect(() => {
    if (!open || !workflowId) return;
    const interval = window.setInterval(() => {
      void (async () => {
        try {
          const active = await listExecutions(workflowId, {
            status: "running,queued,waiting_approval",
            limit: 10,
          });
          if (active.items.length > 0) void refresh();
        } catch {
          // Transient polling errors are non-fatal; next tick retries.
        }
      })();
    }, 5_000);
    return () => window.clearInterval(interval);
  }, [open, workflowId, refresh]);

  if (!workflowId) return null;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="执行历史"
        className={cn(
          "flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-xs transition-all active:scale-95",
          open
            ? "border-pulse/50 bg-pulse/10 text-pulse"
            : "bg-ink/80 text-ice hover:border-ghost/50 hover:bg-line/60",
        )}
      >
        <History size={13} />
        历史
      </button>

      {open && (
        <div className="glass absolute right-0 top-11 z-40 w-96 animate-fade-up rounded-xl border border-line shadow-card">
          <div className="flex items-center justify-between border-b border-line/60 px-4 py-2.5">
            <span className="font-mono text-[10px] uppercase tracking-[0.25em] text-ghost">
              Execution History
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => void refresh()}
                className="rounded-md p-1 text-ghost transition hover:bg-line/60 hover:text-ice"
                title="刷新"
              >
                <RefreshCw size={12} className={cn(loading && "animate-spin")} />
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded-md p-1 text-ghost transition hover:bg-line/60 hover:text-ice"
                title="关闭"
              >
                <X size={12} />
              </button>
            </div>
          </div>

          <div className="max-h-80 overflow-auto p-2">
            {rows.length === 0 && !loading && (
              <p className="px-3 py-6 text-center font-mono text-[11px] text-ghost/50">
                暂无执行记录
              </p>
            )}
            <ul className="space-y-1">
              {rows.map((r) => (
                <li
                  key={r.id}
                  className="flex items-center gap-2.5 rounded-lg border border-transparent px-3 py-2 transition hover:border-line hover:bg-void/50"
                >
                  {STATUS_ICON[r.status] ?? (
                    <MinusCircle size={12} className="text-ghost" />
                  )}
                  <span className="font-mono text-[11px] text-ice/90">
                    {r.id.slice(0, 8)}
                  </span>
                  <span className="font-mono text-[10px] text-ghost/60">
                    {formatTime(r.started_at)}
                  </span>
                  <span
                    className="rounded-sm border border-line bg-void/70 px-1 font-mono text-[8px] text-ghost/75"
                    title={`触发来源：${r.trigger_source}`}
                  >
                    {SOURCE_LABEL[r.trigger_source] ?? r.trigger_source.toUpperCase()}
                  </span>
                  {r.workflow_version_number && (
                    <span
                      className="rounded-xs border border-volt/30 bg-volt/10 px-1.5 font-mono text-[9px] text-volt"
                      title={r.workflow_version_id ?? undefined}
                    >
                      v{r.workflow_version_number}
                    </span>
                  )}
                  <span className="ml-auto font-mono text-[10px] text-ghost/50">
                    {duration(r.started_at, r.finished_at ?? undefined)}
                  </span>
                  {r.error && (
                    <span
                      className="max-w-32 truncate text-[10px] text-bad/80"
                      title={r.error}
                    >
                      {r.error}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
