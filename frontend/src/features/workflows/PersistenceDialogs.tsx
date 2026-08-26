import { createPortal } from "react-dom";
import {
  CloudUpload,
  GitCompareArrows,
  GitMerge,
  RotateCcw,
  TriangleAlert,
} from "lucide-react";

import { useDialogFocus } from "@/components/useDialogFocus";
import type { WorkflowConflict } from "@/features/workflows/useWorkflowPersistence";

interface Props {
  conflict: WorkflowConflict | null;
  navigationBlocked: boolean;
  working: boolean;
  onReloadConflict: () => void;
  onMergeConflict: () => void;
  onOverwriteConflict: () => void;
  onStay: () => void;
  onDiscardAndContinue: () => void;
  onSaveAndContinue: () => void;
}

export function PersistenceDialogs(props: Props) {
  const open = Boolean(props.conflict || props.navigationBlocked);
  const dialogRef = useDialogFocus<HTMLDivElement>({
    open,
    onClose: props.conflict ? undefined : props.onStay,
  });
  if (!open) return null;

  return createPortal(
    <div
      ref={dialogRef}
      tabIndex={-1}
      className="fixed inset-0 z-70 flex items-center justify-center bg-void/80 p-4 backdrop-blur-xs"
    >
      {props.conflict ? (
        <section
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="workflow-conflict-title"
          className="glass w-full max-w-md rounded-lg border border-warn/40 shadow-card"
        >
          <header className="flex items-start gap-3 border-b border-line px-5 py-4">
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-warn/10 text-warn">
              <GitCompareArrows size={16} />
            </span>
            <div>
              <h2 id="workflow-conflict-title" className="text-sm font-semibold text-ice">
                工作流版本冲突
              </h2>
              <p className="mt-1 text-xs leading-5 text-ghost">
                服务器版本已更新。可先自动合并不相交修改；重叠修改会保留本地画布并列出冲突路径。
              </p>
            </div>
          </header>
          <div className="grid grid-cols-2 border-b border-line font-mono text-[10px]">
            <div className="border-r border-line px-5 py-3 text-ghost">
              本地版本 <strong className="ml-1 text-ice">v{props.conflict.localVersion}</strong>
            </div>
            <div className="px-5 py-3 text-ghost">
              远端版本 <strong className="ml-1 text-warn">v{props.conflict.remote.version}</strong>
            </div>
          </div>
          {props.conflict.mergeConflicts.length > 0 && (
            <div className="border-b border-line bg-warn/5 px-5 py-3">
              <p className="text-[11px] font-medium text-warn">
                仍有 {props.conflict.mergeConflicts.length} 处重叠修改
              </p>
              <ul className="mt-2 max-h-24 space-y-1 overflow-y-auto font-mono text-[9px] text-ghost">
                {props.conflict.mergeConflicts.slice(0, 8).map((path) => (
                  <li key={path} className="truncate" title={path}>
                    {path}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <footer className="flex flex-col-reverse gap-2 px-5 py-4 sm:flex-row sm:justify-end">
            <button
              type="button"
              onClick={props.onReloadConflict}
              disabled={props.working}
              className="flex h-9 items-center justify-center gap-2 rounded-md border border-line px-4 text-xs text-ice transition hover:bg-line disabled:opacity-50"
            >
              <RotateCcw size={13} /> 载入远端
            </button>
            <button
              type="button"
              onClick={props.onOverwriteConflict}
              disabled={props.working}
              className="flex h-9 items-center justify-center gap-2 rounded-md border border-warn/40 px-4 text-xs text-warn transition hover:bg-warn/10 disabled:opacity-50"
            >
              <CloudUpload size={13} /> 覆盖远端
            </button>
            <button
              type="button"
              autoFocus
              onClick={props.onMergeConflict}
              disabled={props.working}
              className="flex h-9 items-center justify-center gap-2 rounded-md bg-pulse px-4 text-xs font-semibold text-void transition hover:brightness-110 disabled:opacity-50"
            >
              <GitMerge size={13} /> 自动合并
            </button>
          </footer>
        </section>
      ) : (
        <section
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="unsaved-navigation-title"
          className="glass w-full max-w-md rounded-lg border border-line shadow-card"
        >
          <header className="flex items-start gap-3 border-b border-line px-5 py-4">
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-warn/10 text-warn">
              <TriangleAlert size={16} />
            </span>
            <div>
              <h2 id="unsaved-navigation-title" className="text-sm font-semibold text-ice">
                当前修改尚未保存
              </h2>
              <p className="mt-1 text-xs leading-5 text-ghost">
                可以先保存再离开，也可以放弃本次修改。
              </p>
            </div>
          </header>
          <footer className="flex flex-col-reverse gap-2 px-5 py-4 sm:flex-row sm:justify-end">
            <button
              type="button"
              onClick={props.onStay}
              className="h-9 rounded-md border border-line px-4 text-xs text-ice transition hover:bg-line"
            >
              留在此页
            </button>
            <button
              type="button"
              onClick={props.onDiscardAndContinue}
              className="h-9 rounded-md px-4 text-xs text-bad transition hover:bg-bad/10"
            >
              放弃并离开
            </button>
            <button
              type="button"
              autoFocus
              onClick={props.onSaveAndContinue}
              disabled={props.working}
              className="flex h-9 items-center justify-center gap-2 rounded-md bg-pulse px-4 text-xs font-semibold text-void transition hover:brightness-110 disabled:opacity-50"
            >
              <CloudUpload size={13} /> 保存并离开
            </button>
          </footer>
        </section>
      )}
    </div>,
    document.body,
  );
}
