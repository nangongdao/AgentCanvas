import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  Database,
  Hexagon,
  Loader2,
  Plus,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";

import type { KnowledgeBaseDTO, KnowledgeBaseInput } from "@/api/endpoints/knowledge";
import { useAuth } from "@/features/auth/AuthProvider";
import { KnowledgeBaseDialog } from "@/features/knowledge/KnowledgeBaseDialog";
import { KnowledgeSidebar } from "@/features/knowledge/KnowledgeSidebar";
import { KnowledgeWorkspace } from "@/features/knowledge/KnowledgeWorkspace";
import { useResourceStore } from "@/stores/resourceStore";

export function KnowledgePage() {
  const { can } = useAuth();
  const canEdit = can("editor");
  const location = useLocation();
  const citationTarget = useMemo(() => {
    const query = new URLSearchParams(location.search);
    return {
      kbId: query.get("kb_id"),
      documentId: query.get("document_id"),
    };
  }, [location.search]);
  const knowledgeBases = useResourceStore((state) => state.knowledgeBases);
  const activeId = useResourceStore((state) => state.activeKnowledgeBaseId);
  const loading = useResourceStore((state) => state.loading);
  const working = useResourceStore((state) => state.working);
  const error = useResourceStore((state) => state.error);
  const loadKnowledge = useResourceStore((state) => state.loadKnowledge);
  const selectKnowledgeBase = useResourceStore((state) => state.selectKnowledgeBase);
  const createBase = useResourceStore((state) => state.createKnowledgeBase);
  const updateBase = useResourceStore((state) => state.updateKnowledgeBase);
  const deleteBase = useResourceStore((state) => state.deleteKnowledgeBase);
  const clearError = useResourceStore((state) => state.clearError);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<KnowledgeBaseDTO | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const active = useMemo(
    () => knowledgeBases.find((row) => row.id === activeId) ?? null,
    [activeId, knowledgeBases],
  );

  useEffect(() => {
    void loadKnowledge(
      citationTarget.kbId ?? undefined,
      citationTarget.documentId ?? undefined,
    ).catch(() => undefined);
  }, [citationTarget.documentId, citationTarget.kbId, loadKnowledge]);

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 3500);
  }, []);

  const openCreate = () => {
    setEditing(null);
    setDialogOpen(true);
  };

  const saveBase = async (input: KnowledgeBaseInput) => {
    if (editing) {
      await updateBase(editing.id, input);
      showToast("知识库配置已保存，相关文档需要重新摄取");
    } else {
      await createBase(input);
      showToast("知识库已创建");
    }
    setDialogOpen(false);
  };

  const removeBase = async () => {
    if (!active || !window.confirm(`删除知识库“${active.name}”及其文档？`)) return;
    try {
      await deleteBase(active.id);
      showToast("知识库、文档与向量已删除");
    } catch {
      // The shared error band contains the API detail.
    }
  };

  return (
    <div className="ambient-stage flex h-full w-full flex-col bg-void text-ice">
      <header role="presentation" className="glass relative z-20 flex min-h-14 flex-wrap items-center gap-2 border-b border-line px-3 py-2 sm:px-5">
        <span className="relative flex h-8 w-8 items-center justify-center text-ok">
          <Hexagon size={27} strokeWidth={1.2} />
          <Database size={11} className="absolute" />
        </span>
        <div className="min-w-0">
          <h1 className="font-display text-sm font-semibold text-ice sm:text-base">
            AgentCanvas Knowledge
          </h1>
          <p className="font-mono text-[9px] uppercase text-ghost/50">
            ingest / index / retrieval
          </p>
        </div>

        <div role="toolbar" aria-label="知识库页面操作" className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={() =>
              void loadKnowledge(
                activeId ?? undefined,
                activeId === citationTarget.kbId
                  ? citationTarget.documentId ?? undefined
                  : undefined,
              )
            }
            disabled={loading}
            className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-pulse disabled:opacity-40"
            title="刷新"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : undefined} />
          </button>
          {canEdit && (
            <button
              type="button"
              onClick={openCreate}
              className="flex h-8 items-center gap-1.5 rounded-md bg-ok px-3 text-xs font-semibold text-void transition hover:brightness-110"
            >
              <Plus size={13} />
              <span className="hidden sm:inline">新建知识库</span>
            </button>
          )}
        </div>
      </header>

      {error && (
        <div className="relative z-10 flex min-h-9 items-center gap-2 border-b border-bad/30 bg-bad/10 px-4 text-xs text-bad">
          <TriangleAlert size={13} />
          <span className="min-w-0 flex-1 truncate">{error}</span>
          <button
            type="button"
            onClick={clearError}
            className="h-7 rounded-md px-2 font-mono text-[9px] uppercase hover:bg-bad/10"
          >
            dismiss
          </button>
        </div>
      )}

      <div className="relative z-10 flex min-h-0 flex-1 flex-col md:flex-row">
        <KnowledgeSidebar
          rows={knowledgeBases}
          activeId={activeId}
          loading={loading}
          canEdit={canEdit}
          onSelect={(id) => void selectKnowledgeBase(id).catch(() => undefined)}
          onCreate={openCreate}
        />
        <KnowledgeWorkspace
          knowledgeBase={active}
          focusedDocumentId={
            active?.id === citationTarget.kbId ? citationTarget.documentId : null
          }
          canEdit={canEdit}
          onEdit={() => {
            setEditing(active);
            setDialogOpen(true);
          }}
          onDelete={() => void removeBase()}
          onToast={showToast}
        />
        {loading && knowledgeBases.length > 0 && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-void/35">
            <Loader2 size={18} className="animate-spin text-pulse" />
          </div>
        )}
      </div>

      {toast && (
        <div className="fixed bottom-5 left-1/2 z-40 -translate-x-1/2 animate-fade-up">
          <div className="glass rounded-md border border-line px-4 py-2 text-xs text-ice shadow-card">
            {toast}
          </div>
        </div>
      )}

      <KnowledgeBaseDialog
        open={dialogOpen}
        knowledgeBase={editing}
        working={working === "create" || working === "update"}
        onClose={() => setDialogOpen(false)}
        onSave={saveBase}
      />
    </div>
  );
}
