import { useCallback, useEffect, useState } from "react";
import {
  Loader2,
  Plus,
  RefreshCw,
  Settings2,
  TriangleAlert,
} from "lucide-react";

import {
  type ModelConfigCreate,
  type ModelConfigDTO,
  type ModelConfigUpdate,
  type ProviderDescriptorDTO,
  type ResilienceSnapshotDTO,
  createModel,
  deleteModel,
  getResilienceStatus,
  listModels,
  listProviderCapabilities,
  updateModel,
} from "@/api/endpoints/meta";
import { useAuth } from "@/features/auth/AuthProvider";
import { ModelDialog } from "@/features/models/ModelDialog";
import { ModelListItem } from "@/features/models/ModelListItem";
import { ProviderCapabilityMatrix } from "@/features/models/ProviderCapabilityMatrix";
import { ProviderRuntimeHealth } from "@/features/models/ProviderRuntimeHealth";

export function ModelsPage() {
  const { can } = useAuth();
  const canEdit = can("admin");
  const [models, setModels] = useState<ModelConfigDTO[]>([]);
  const [providerDescriptors, setProviderDescriptors] = useState<ProviderDescriptorDTO[]>([]);
  const [runtimeResources, setRuntimeResources] = useState<ResilienceSnapshotDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [editing, setEditing] = useState<ModelConfigDTO | null>(null);
  const [creating, setCreating] = useState(false);

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 3500);
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [rows, descriptors, resilience] = await Promise.all([
        listModels(),
        listProviderCapabilities(),
        getResilienceStatus(),
      ]);
      setModels(rows);
      setProviderDescriptors(descriptors);
      setRuntimeResources(resilience.resources);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载模型列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const handleSave = async (input: ModelConfigCreate | ModelConfigUpdate, id?: string) => {
    try {
      if (id) {
        await updateModel(id, input as ModelConfigUpdate);
        showToast("模型已更新");
      } else {
        await createModel(input as ModelConfigCreate);
        showToast("模型已创建");
      }
      setEditing(null);
      setCreating(false);
      void reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存模型失败");
    }
  };

  const handleDelete = async (model: ModelConfigDTO) => {
    if (!window.confirm(`删除模型「${model.name}」？`)) return;
    try {
      await deleteModel(model.id);
      showToast("模型已删除");
      void reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    }
  };

  return (
    <div className="ambient-stage flex h-full w-full min-w-0 flex-col overflow-x-hidden text-ice">
      <header role="presentation" className="glass relative z-20 flex min-h-14 min-w-0 flex-wrap items-center gap-2 border-b border-line px-3 py-2 sm:px-5">
        <span className="flex h-8 w-8 items-center justify-center text-volt">
          <Settings2 size={18} />
        </span>
        <div className="min-w-0">
          <h1 className="workspace-page-title">
            AgentCanvas Models
          </h1>
          <p className="font-mono text-[9px] uppercase text-ghost/50">
            providers / api keys / defaults
          </p>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => void reload()}
            disabled={loading}
            className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-pulse disabled:opacity-40"
            title="刷新"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : undefined} />
          </button>
          {canEdit && (
            <button
              type="button"
              onClick={() => setCreating(true)}
              className="flex h-8 items-center gap-1.5 rounded-md bg-volt px-3 text-xs font-semibold text-void transition hover:brightness-110"
            >
              <Plus size={13} />
              <span className="hidden sm:inline">新建模型</span>
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
            onClick={() => setError(null)}
            className="h-7 rounded-md px-2 font-mono text-[9px] uppercase hover:bg-bad/10"
          >
            dismiss
          </button>
        </div>
      )}

      {toast && (
        <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-md border border-ok/40 bg-ok/15 px-4 py-2 text-xs text-ok shadow-card animate-fade-up">
          {toast}
        </div>
      )}

      <main className="min-w-0 flex-1 overflow-auto p-4 sm:p-6">
        <div className="mx-auto min-w-0 max-w-5xl">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-mono text-[10px] uppercase tracking-[0.25em] text-ghost/60">
              Configured Models / {models.length}
            </h2>
            {providerDescriptors.length > 0 && (
              <span className="font-mono text-[10px] text-ghost/50">
                providers: {providerDescriptors.map((item) => item.id).join(" · ")}
              </span>
            )}
          </div>

          <ProviderCapabilityMatrix providers={providerDescriptors} />
          <ProviderRuntimeHealth resources={runtimeResources} />

          {loading && models.length === 0 ? (
            <div className="flex items-center gap-2 text-ghost/60">
              <Loader2 size={14} className="animate-spin" />
              <span className="text-xs">加载中…</span>
            </div>
          ) : (
            <div className="grid gap-2">
              {models.map((model) => (
                <ModelListItem
                  key={model.id}
                  model={model}
                  canEdit={canEdit}
                  onEdit={() => setEditing(model)}
                  onDelete={() => void handleDelete(model)}
                />
              ))}
              {models.length === 0 && !loading && (
                <p className="text-xs text-ghost/50">尚未配置任何模型，点击右上角「新建模型」开始。</p>
              )}
            </div>
          )}
        </div>
      </main>

      {(creating || editing) && (
        <ModelDialog
          model={editing}
          providerDescriptors={providerDescriptors}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSave={handleSave}
        />
      )}
    </div>
  );
}
