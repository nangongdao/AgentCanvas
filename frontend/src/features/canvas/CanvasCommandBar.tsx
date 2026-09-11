import {
  Check,
  Cloud,
  Loader2,
  Play,
  Redo2,
  Save,
  TriangleAlert,
  Undo2,
} from "lucide-react";

import { ExecutionHistory } from "@/features/execution/ExecutionHistory";
import { CanvasObjectActions } from "@/features/canvas/CanvasObjectActions";
import { ClipboardActions } from "@/features/canvas/ClipboardActions";
import { CanvasLayoutMenu } from "@/features/canvas/CanvasLayoutMenu";
import { CopilotDialog } from "@/features/canvas/CopilotDialog";
import type {
  AlignmentMode,
  DistributionMode,
} from "@/features/canvas/canvasLayout";
import { WorkflowVariablesDialog } from "@/features/canvas/WorkflowVariablesDialog";
import { McpManagerPanel } from "@/features/mcp/McpManagerPanel";
import { WorkflowTemplateGallery } from "@/features/templates/WorkflowTemplateGallery";
import { CollaborationStatus } from "@/features/workflows/CollaborationStatus";
import { WorkflowNavigator } from "@/features/workflows/WorkflowNavigator";
import { WorkflowReviewPanel } from "@/features/workflows/WorkflowReviewPanel";
import { WorkflowTriggerPanel } from "@/features/workflows/WorkflowTriggerPanel";
import { WorkflowVersionPanel } from "@/features/workflows/WorkflowVersionPanel";
import type { WorkflowCollaborationState } from "@/features/workflows/useWorkflowCollaboration";
import type { SaveState } from "@/features/workflows/useWorkflowPersistence";
import { cn } from "@/utils/cn";

interface Props {
  name: string;
  workflowId: string | null;
  version: number;
  dirty: boolean;
  saveState: SaveState;
  busy: boolean;
  running: boolean;
  canEdit: boolean;
  editingAllowed: boolean;
  canAdmin: boolean;
  collaboration: WorkflowCollaborationState;
  canUndo: boolean;
  canRedo: boolean;
  selectionCount: number;
  canPaste: boolean;
  onAddGroup: () => void;
  onAddNote: () => void;
  onCopy: () => void;
  onPaste: () => void;
  onUndo: () => void;
  onRedo: () => void;
  onAutoLayout: () => void;
  onAlign: (mode: AlignmentMode) => void;
  onDistribute: (mode: DistributionMode) => void;
  onNameChange: (name: string) => void;
  onSave: () => void;
  onRun: () => void;
  onOpenWorkflow: (id: string | null) => Promise<boolean>;
  onEnsureSaved: () => Promise<string | null>;
  onReloadWorkflow: () => Promise<void>;
  onNotify: (message: string) => void;
  onActiveArchived: () => void;
}

function SaveIndicator(props: Pick<Props, "dirty" | "saveState" | "workflowId">) {
  if (props.saveState === "saving") {
    return (
      <span className="flex items-center gap-1 font-mono text-[9px] uppercase text-pulse">
        <Loader2 size={10} className="animate-spin" /> saving
      </span>
    );
  }
  if (props.saveState === "conflict") {
    return (
      <span className="flex items-center gap-1 font-mono text-[9px] uppercase text-warn">
        <TriangleAlert size={10} /> conflict
      </span>
    );
  }
  if (props.dirty) {
    return (
      <span className="flex items-center gap-1 font-mono text-[9px] uppercase text-warn">
        <Cloud size={10} /> pending
      </span>
    );
  }
  if (props.workflowId) {
    return (
      <span className="flex items-center gap-1 font-mono text-[9px] uppercase text-ok">
        <Check size={10} /> synced
      </span>
    );
  }
  return null;
}

export function CanvasCommandBar(props: Props) {
  return (
    <header role="toolbar" aria-label="工作流命令栏" className="glass relative z-20 flex min-h-14 flex-wrap items-center gap-2 border-b border-line px-3 py-2 lg:gap-3 lg:px-5">
      <WorkflowNavigator
        activeId={props.workflowId}
        canEdit={props.canEdit}
        onOpen={props.onOpenWorkflow}
        onActiveArchived={props.onActiveArchived}
      />

      <div className="hidden h-5 w-px bg-line lg:block" />

      <div className="flex min-w-0 flex-1 items-center gap-2 sm:flex-none">
        <input
          className="min-w-20 flex-1 bg-transparent font-display text-sm font-medium text-ice outline-hidden transition placeholder:text-ghost/40 focus:text-pulse sm:w-44 sm:flex-none"
          aria-label="工作流名称"
          value={props.name}
          onChange={(event) => props.onNameChange(event.target.value)}
          readOnly={!props.editingAllowed}
          placeholder="工作流名称"
          title={
            props.editingAllowed
              ? "工作流名称"
              : props.canEdit
                ? "需要先取得工作流编辑权"
                : "只读角色"
          }
        />
        <SaveIndicator
          dirty={props.dirty}
          saveState={props.saveState}
          workflowId={props.workflowId}
        />
        {props.workflowId && (
          <span className="hidden font-mono text-[9px] text-ghost/50 xl:inline">
            {props.workflowId.slice(0, 8)} · v{props.version}
          </span>
        )}
      </div>

      {props.canEdit && (
        <div
          role="group"
          aria-label="编辑历史"
          className="flex shrink-0 items-center overflow-hidden rounded-md border border-line bg-ink/80"
        >
          <button
            type="button"
            aria-label="撤销"
            aria-keyshortcuts="Control+Z Meta+Z"
            disabled={!props.editingAllowed || !props.canUndo}
            onClick={props.onUndo}
            className="flex h-8 w-8 items-center justify-center text-ghost transition hover:bg-line/60 hover:text-ice focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-pulse disabled:cursor-not-allowed disabled:opacity-35"
            title={props.editingAllowed ? "撤销 (Ctrl+Z)" : "需要先取得工作流编辑权"}
          >
            <Undo2 size={14} />
          </button>
          <span className="h-4 w-px bg-line" />
          <button
            type="button"
            aria-label="重做"
            aria-keyshortcuts="Control+Y Meta+Shift+Z"
            disabled={!props.editingAllowed || !props.canRedo}
            onClick={props.onRedo}
            className="flex h-8 w-8 items-center justify-center text-ghost transition hover:bg-line/60 hover:text-ice focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-pulse disabled:cursor-not-allowed disabled:opacity-35"
            title={props.editingAllowed ? "重做 (Ctrl+Y)" : "需要先取得工作流编辑权"}
          >
            <Redo2 size={14} />
          </button>
        </div>
      )}

      <WorkflowVariablesDialog
        canEdit={props.canEdit}
        editingAllowed={props.editingAllowed}
      />

      <CopilotDialog
        canEdit={props.canEdit}
        editingAllowed={props.editingAllowed}
        onNotify={props.onNotify}
      />

      <CanvasLayoutMenu
        editingAllowed={props.editingAllowed}
        selectionCount={props.selectionCount}
        onAutoLayout={props.onAutoLayout}
        onAlign={props.onAlign}
        onDistribute={props.onDistribute}
      />

      <CanvasObjectActions
        editingAllowed={props.editingAllowed}
        selectionCount={props.selectionCount}
        onAddGroup={props.onAddGroup}
        onAddNote={props.onAddNote}
      />

      <ClipboardActions
        editingAllowed={props.editingAllowed}
        selectionCount={props.selectionCount}
        canPaste={props.canPaste}
        onCopy={props.onCopy}
        onPaste={props.onPaste}
      />

      <CollaborationStatus
        collaboration={props.collaboration}
        roleCanEdit={props.collaboration.canEdit}
      />

      <div className="ml-auto flex min-w-0 items-center gap-2">
        <div className="flex min-w-0 items-center gap-2 overflow-x-auto whitespace-nowrap">
          <WorkflowTemplateGallery
            workflowId={props.workflowId}
            workflowName={props.name}
            canEdit={props.canEdit}
            onOpenWorkflow={props.onOpenWorkflow}
            onNotify={props.onNotify}
          />
          <WorkflowVersionPanel
            workflowId={props.workflowId}
            currentVersion={props.version}
            canEdit={props.editingAllowed}
            onEnsureSaved={props.onEnsureSaved}
            onReload={props.onReloadWorkflow}
            onOpenWorkflow={props.onOpenWorkflow}
            onNotify={props.onNotify}
          />
          <WorkflowReviewPanel
            workflowId={props.workflowId}
            currentVersion={props.version}
            canEdit={props.collaboration.canEdit}
            onNotify={props.onNotify}
          />
          <WorkflowTriggerPanel
            workflowId={props.workflowId}
            canEdit={props.editingAllowed}
            canAdmin={props.canAdmin}
            currentVersion={props.version}
            onNotify={props.onNotify}
          />
          <ExecutionHistory workflowId={props.workflowId} />
          {props.canAdmin && <McpManagerPanel />}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {props.editingAllowed && (
            <button
              type="button"
              disabled={props.busy}
              onClick={props.onSave}
              className="flex h-8 items-center gap-1.5 rounded-md border border-line bg-ink/80 px-2.5 text-xs text-ice transition hover:border-ghost/50 hover:bg-line/60 active:scale-95 disabled:opacity-40"
              title="保存工作流"
            >
              {props.saveState === "saving" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Save size={13} />
              )}
              <span className="hidden sm:inline">保存</span>
            </button>
          )}

          {props.editingAllowed && (
            <button
              type="button"
              disabled={props.busy || props.running}
              onClick={props.onRun}
              className={cn(
                "btn-primary flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-semibold transition active:scale-95",
                props.running
                  ? "cursor-not-allowed bg-pulse/20 text-pulse"
                  : "bg-pulse text-void shadow-glow-cyan hover:brightness-110",
              )}
            >
              {props.running ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Play size={13} fill="currentColor" />
              )}
              <span>{props.running ? "运行中" : "运行"}</span>
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
