import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import {
  GitFork,
  Loader2,
  RefreshCw,
  Rocket,
  X,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  cloneWorkflow,
  diffWorkflowVersions,
  exportWorkflowVersion,
  listWorkflowVersions,
  publishWorkflow,
  rollbackWorkflow,
  type WorkflowDiffDTO,
  type WorkflowVersionDTO,
} from "@/api/endpoints/workflows";
import { useDialogFocus } from "@/components/useDialogFocus";
import { cn } from "@/utils/cn";

import {
  WorkflowDiffSummary,
  WorkflowVersionRow,
} from "./WorkflowVersionParts";

interface Props {
  workflowId: string | null;
  currentVersion: number;
  canEdit: boolean;
  onEnsureSaved: () => Promise<string | null>;
  onReload: () => Promise<void>;
  onOpenWorkflow: (id: string | null) => Promise<boolean>;
  onNotify: (message: string) => void;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string"
      ? error.detail
      : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

export function WorkflowVersionPanel(props: Props) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<WorkflowVersionDTO[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [diff, setDiff] = useState<WorkflowDiffDTO | null>(null);
  const [loading, setLoading] = useState(false);
  const [action, setAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useDialogFocus<HTMLElement>({
    open,
    onClose: () => setOpen(false),
  });

  const load = useCallback(async () => {
    if (!props.workflowId) return;
    setLoading(true);
    setError(null);
    try {
      setRows(await listWorkflowVersions(props.workflowId));
    } catch (loadError) {
      setError(errorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [props.workflowId]);

  useEffect(() => {
    if (open) void load();
  }, [load, open, props.currentVersion]);

  const current = useMemo(
    () => rows.find((row) => row.number === props.currentVersion),
    [props.currentVersion, rows],
  );

  const toggleCompare = async (versionId: string) => {
    const next = selected.includes(versionId)
      ? selected.filter((id) => id !== versionId)
      : [...selected.slice(-1), versionId];
    setSelected(next);
    setDiff(null);
    if (next.length !== 2 || !props.workflowId) return;
    setAction("diff");
    try {
      setDiff(await diffWorkflowVersions(props.workflowId, next[0], next[1]));
    } catch (compareError) {
      setError(errorMessage(compareError));
    } finally {
      setAction(null);
    }
  };

  const publish = async () => {
    if (!props.workflowId) return;
    setAction("publish");
    setError(null);
    try {
      if (!(await props.onEnsureSaved())) return;
      await publishWorkflow(props.workflowId);
      await load();
      props.onNotify("当前版本已发布");
    } catch (publishError) {
      setError(errorMessage(publishError));
    } finally {
      setAction(null);
    }
  };

  const rollback = async (row: WorkflowVersionDTO) => {
    if (!props.workflowId) return;
    if (!window.confirm(`将 v${row.number} 恢复为新的草稿版本？`)) return;
    setAction(`rollback:${row.id}`);
    setError(null);
    try {
      await rollbackWorkflow(
        props.workflowId,
        row.id,
        `Rollback to version ${row.number}`,
      );
      await props.onReload();
      await load();
      window.dispatchEvent(new Event("agentcanvas:workflows-changed"));
      props.onNotify(`已从 v${row.number} 创建恢复草稿`);
    } catch (rollbackError) {
      setError(errorMessage(rollbackError));
    } finally {
      setAction(null);
    }
  };

  const clone = async (row: WorkflowVersionDTO) => {
    if (!props.workflowId) return;
    setAction(`clone:${row.id}`);
    setError(null);
    try {
      const workflow = await cloneWorkflow(props.workflowId, {
        version_id: row.id,
        name: `${row.name} (Copy)`,
      });
      window.dispatchEvent(new Event("agentcanvas:workflows-changed"));
      props.onNotify(`已从 v${row.number} 创建副本`);
      setOpen(false);
      await props.onOpenWorkflow(workflow.id);
    } catch (cloneError) {
      setError(errorMessage(cloneError));
    } finally {
      setAction(null);
    }
  };

  const exportVersion = async (row: WorkflowVersionDTO) => {
    if (!props.workflowId) return;
    setAction(`export:${row.id}`);
    setError(null);
    try {
      const bundle = await exportWorkflowVersion(props.workflowId, row.id);
      const blob = new Blob([JSON.stringify(bundle, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      const basename = row.name.replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-|-$/g, "");
      anchor.href = url;
      anchor.download = `${basename || "workflow"}-v${row.number}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
      props.onNotify(`已导出 v${row.number}`);
    } catch (exportError) {
      setError(errorMessage(exportError));
    } finally {
      setAction(null);
    }
  };

  if (!props.workflowId) return null;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex h-8 items-center gap-1.5 rounded-md border border-line bg-ink/80 px-2.5 text-xs text-ice transition hover:border-volt/40 hover:bg-volt/10 hover:text-volt"
        title="版本历史"
      >
        <GitFork size={13} />
        <span className="hidden xl:inline">版本</span>
      </button>

      {open &&
        createPortal(
          <div className="fixed inset-0 z-50 bg-void/75 backdrop-blur-xs">
            <button
              type="button"
              aria-label="关闭版本历史"
              className="absolute inset-0 h-full w-full cursor-default"
              onClick={() => setOpen(false)}
            />
            <section
              ref={dialogRef}
              tabIndex={-1}
              role="dialog"
              aria-modal="true"
              aria-labelledby="workflow-version-title"
              className="glass absolute right-0 flex h-full w-[min(480px,100vw)] animate-slide-in flex-col border-l border-line shadow-card"
            >
              <header className="flex min-h-16 items-center gap-3 border-b border-line px-4">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-volt/35 bg-volt/10 text-volt">
                  <GitFork size={17} />
                </span>
                <div className="min-w-0">
                  <h2 id="workflow-version-title" className="text-sm font-semibold text-ice">
                    版本历史
                  </h2>
                  <p className="font-mono text-[9px] uppercase text-ghost">
                    current v{props.currentVersion} · {rows.length} snapshots
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void load()}
                  disabled={loading}
                  className="ml-auto flex h-8 w-8 items-center justify-center rounded-md text-ghost hover:bg-line hover:text-ice disabled:opacity-40"
                  title="刷新"
                >
                  <RefreshCw size={14} className={cn(loading && "animate-spin")} />
                </button>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="flex h-8 w-8 items-center justify-center rounded-md text-ghost hover:bg-line hover:text-ice"
                  title="关闭"
                >
                  <X size={15} />
                </button>
              </header>

              {props.canEdit && current?.status !== "published" && (
                <div className="flex items-center gap-3 border-b border-line px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium text-ice">发布当前草稿</p>
                    <p className="mt-0.5 text-[10px] text-ghost/65">
                      draft v{props.currentVersion} · production candidate
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={action !== null}
                    onClick={() => void publish()}
                    className="flex h-8 shrink-0 items-center gap-1.5 rounded-md border border-ok/40 bg-ok/10 px-3 text-xs text-ok hover:bg-ok/20 disabled:opacity-40"
                  >
                    {action === "publish" ? (
                      <Loader2 size={13} className="animate-spin" />
                    ) : (
                      <Rocket size={13} />
                    )}
                    发布
                  </button>
                </div>
              )}

              {error && (
                <p className="border-b border-bad/30 bg-bad/10 px-4 py-2 text-xs text-bad">
                  {error}
                </p>
              )}
              {action === "diff" && (
                <p className="border-b border-line px-4 py-2 font-mono text-[10px] text-ghost">
                  comparing snapshots…
                </p>
              )}
              {diff && <WorkflowDiffSummary diff={diff} />}

              <div className="min-h-0 flex-1 overflow-auto">
                {rows.length === 0 && !loading && (
                  <p className="px-5 py-12 text-center text-xs text-ghost/60">
                    暂无版本快照
                  </p>
                )}
                <ul className="divide-y divide-line/70">
                  {rows.map((row) => (
                    <WorkflowVersionRow
                      key={row.id}
                      row={row}
                      currentVersion={props.currentVersion}
                      selectedForDiff={selected.includes(row.id)}
                      canEdit={props.canEdit}
                      action={action}
                      onToggleCompare={(id) => void toggleCompare(id)}
                      onExport={(version) => void exportVersion(version)}
                      onRollback={(version) => void rollback(version)}
                      onClone={(version) => void clone(version)}
                    />
                  ))}
                </ul>
              </div>
            </section>
          </div>,
          document.body,
        )}
    </>
  );
}
