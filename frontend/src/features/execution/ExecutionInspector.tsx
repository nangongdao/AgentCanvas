import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Clock3,
  Loader2,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  XCircle,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  getExecutionInspection,
  rerunExecution,
  type ExecutionInspectionDTO,
  type NodeAttemptDTO,
} from "@/api/endpoints/workflows";
import { useAuth } from "@/features/auth/AuthProvider";
import { useExecutionStore } from "@/stores/executionStore";
import { cn } from "@/utils/cn";

interface Props {
  executionId: string;
  executionStatus: string;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string"
      ? error.detail
      : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

function formatJson(value: unknown): string {
  if (value === undefined || value === null) return "--";
  return JSON.stringify(value, null, 2);
}

function attemptKey(attempt: NodeAttemptDTO): string {
  return `${attempt.node_id}:${attempt.attempt}`;
}

function AttemptStatus({ attempt }: { attempt: NodeAttemptDTO }) {
  if (attempt.status === "succeeded") {
    return <CheckCircle2 size={12} className="text-ok" />;
  }
  if (attempt.status === "failed") {
    return <XCircle size={12} className="text-bad" />;
  }
  if (attempt.status === "running") {
    return <Loader2 size={12} className="animate-spin text-pulse" />;
  }
  return <AlertCircle size={12} className="text-warn" />;
}

function SnapshotBlock({ label, value }: { label: string; value: unknown }) {
  return (
    <section className="border-t border-line/70 py-3 first:border-t-0 first:pt-0">
      <p className="mb-1.5 font-mono text-[9px] uppercase text-ghost/60">{label}</p>
      <pre className="max-h-36 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-4 text-ice/80">
        {formatJson(value)}
      </pre>
    </section>
  );
}

export function ExecutionInspector({ executionId, executionStatus }: Props) {
  const { can } = useAuth();
  const beginExecution = useExecutionStore((state) => state.begin);
  const [inspection, setInspection] = useState<ExecutionInspectionDTO | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rerunning, setRerunning] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await getExecutionInspection(executionId);
      setInspection(next);
      setSelectedKey((current) => {
        if (current && next.attempts.some((item) => attemptKey(item) === current)) {
          return current;
        }
        const failed = next.attempts.find((item) => item.status === "failed");
        const fallback = failed ?? next.attempts.at(-1);
        return fallback ? attemptKey(fallback) : null;
      });
    } catch (loadError) {
      setError(errorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [executionId]);

  useEffect(() => {
    void load();
  }, [load, executionStatus]);

  const selected = useMemo(
    () =>
      inspection?.attempts.find((item) => attemptKey(item) === selectedKey) ??
      null,
    [inspection, selectedKey],
  );

  const handleRerun = useCallback(async () => {
    if (!selected || selected.status !== "failed") return;
    setRerunning(true);
    setError(null);
    try {
      const execution = await rerunExecution(executionId, selected.node_id);
      beginExecution(execution.id, execution.workflow_id);
    } catch (rerunError) {
      setError(errorMessage(rerunError));
    } finally {
      setRerunning(false);
    }
  }, [beginExecution, executionId, selected]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-9 items-center gap-3 border-b border-line/60 px-3">
        <span className="flex items-center gap-1 font-mono text-[9px] text-ghost/65">
          <Clock3 size={11} /> {inspection?.total_duration_ms ?? 0} ms
        </span>
        <span className="font-mono text-[9px] text-ghost/65">
          {inspection?.total_tokens ?? 0} tokens
        </span>
        <span className="flex items-center gap-1 font-mono text-[9px] text-ok/75">
          <ShieldCheck size={11} /> redacted
        </span>
        {inspection?.rerun_from_node_id && (
          <span
            className="flex min-w-0 items-center gap-1 font-mono text-[9px] text-warn/80"
            title={`从 ${inspection.rerun_from_node_id} 节点重跑`}
          >
            <RotateCcw size={10} />
            <span className="max-w-20 truncate">{inspection.rerun_from_node_id}</span>
          </span>
        )}
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="ml-auto flex h-7 w-7 items-center justify-center rounded-md text-ghost hover:bg-line hover:text-ice disabled:opacity-40"
          title="刷新节点检查器"
        >
          <RefreshCw size={12} className={cn(loading && "animate-spin")} />
        </button>
      </div>

      {error && <p className="border-b border-bad/30 px-3 py-2 text-[10px] text-bad">{error}</p>}

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(130px,38%)_minmax(0,1fr)]">
        <ul className="min-h-0 overflow-auto border-r border-line/60">
          {inspection?.attempts.map((attempt) => {
            const key = attemptKey(attempt);
            return (
              <li key={key}>
                <button
                  type="button"
                  onClick={() => setSelectedKey(key)}
                  className={cn(
                    "flex w-full min-w-0 items-center gap-2 border-b border-line/50 px-3 py-2 text-left",
                    selectedKey === key ? "bg-volt/10" : "hover:bg-line/35",
                  )}
                >
                  <AttemptStatus attempt={attempt} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[11px] text-ice">
                      {attempt.node_label}
                    </span>
                    <span className="block truncate font-mono text-[9px] text-ghost/55">
                      {attempt.node_type} · #{attempt.attempt}
                    </span>
                  </span>
                  <span className="shrink-0 font-mono text-[9px] text-ghost/50">
                    {attempt.duration_ms ?? "--"} ms
                  </span>
                </button>
              </li>
            );
          })}
          {!loading && inspection?.attempts.length === 0 && (
            <li className="px-3 py-8 text-center text-[10px] text-ghost/50">
              暂无节点尝试
            </li>
          )}
        </ul>

        <div className="min-h-0 overflow-auto px-3 py-3">
          {selected && (
            <>
              <div className="mb-3 flex min-w-0 flex-wrap items-center gap-2">
                <AttemptStatus attempt={selected} />
                <span className="min-w-20 flex-1 truncate text-xs font-medium text-ice">
                  {selected.node_label}
                </span>
                <span className="shrink-0 font-mono text-[9px] text-ghost/55">
                  {selected.prompt_tokens} in · {selected.completion_tokens} out
                </span>
                {can("editor") &&
                  executionStatus === "failed" &&
                  selected.status === "failed" && (
                    <button
                      type="button"
                      onClick={() => void handleRerun()}
                      disabled={rerunning}
                      className="flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-bad/35 bg-bad/10 px-2 text-[10px] font-medium text-bad hover:border-bad/60 hover:bg-bad/15 disabled:opacity-45"
                      title="复用上游快照，从此失败节点创建新执行"
                    >
                      {rerunning ? (
                        <Loader2 size={11} className="animate-spin" />
                      ) : (
                        <RotateCcw size={11} />
                      )}
                      重跑
                    </button>
                  )}
              </div>
              <SnapshotBlock label="Input" value={selected.input} />
              <SnapshotBlock label="Output" value={selected.output} />
              {selected.error && <SnapshotBlock label="Error" value={selected.error} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
