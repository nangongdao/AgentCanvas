import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  BrainCircuit,
  Cable,
  CircleDollarSign,
  FolderKanban,
  Gauge,
  HardDrive,
  Loader2,
  Pencil,
  RefreshCw,
  Save,
  TriangleAlert,
  X,
} from "lucide-react";
import { useSearchParams } from "react-router-dom";

import {
  type ProjectDTO,
  type ProjectQuotaDTO,
  type ProjectQuotaUpdate,
  getProjectQuotas,
  listProjects,
  updateProjectQuotas,
} from "@/api/endpoints/projectQuotas";
import { useAuth } from "@/features/auth/AuthProvider";
import {
  QuotaMetricRow,
  type QuotaMetricView,
} from "@/features/quotas/QuotaMetricRow";

interface QuotaDraft {
  concurrent: string | null;
  storage: string | null;
  embedding: string | null;
  modelCost: string | null;
  mcp: string | null;
}

const numberFormat = new Intl.NumberFormat("zh-CN");

function integerDraft(value: number | null): string | null {
  return value === null ? null : String(value);
}

function draftFrom(quota: ProjectQuotaDTO): QuotaDraft {
  return {
    concurrent: integerDraft(quota.concurrent_execution_limit),
    storage: integerDraft(quota.storage_bytes_limit),
    embedding: integerDraft(quota.monthly_embedding_input_bytes_limit),
    modelCost: quota.monthly_model_cost_usd_limit,
    mcp: integerDraft(quota.stdio_mcp_process_limit),
  };
}

function parseInteger(value: string | null, label: string): number | null {
  if (value === null) return null;
  if (!/^\d+$/.test(value)) throw new Error(`${label}必须是非负整数`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) throw new Error(`${label}超出安全范围`);
  return parsed;
}

function updateFrom(draft: QuotaDraft): ProjectQuotaUpdate {
  if (
    draft.modelCost !== null &&
    !/^\d+(?:\.\d{1,12})?$/.test(draft.modelCost)
  ) {
    throw new Error("模型费用必须是最多 12 位小数的非负金额");
  }
  return {
    concurrent_execution_limit: parseInteger(draft.concurrent, "并发执行上限"),
    storage_bytes_limit: parseInteger(draft.storage, "文档存储上限"),
    monthly_embedding_input_bytes_limit: parseInteger(
      draft.embedding,
      "Embedding 输入上限",
    ),
    monthly_model_cost_usd_limit: draft.modelCost,
    stdio_mcp_process_limit: parseInteger(draft.mcp, "MCP 进程上限"),
  };
}

function formatBytes(value: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let amount = value;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
}

function formatUsd(value: string): string {
  const [whole, fraction = ""] = value.split(".");
  const compactFraction = fraction.replace(/0+$/, "");
  return `$${whole}${compactFraction ? `.${compactFraction}` : ".00"}`;
}

function ratio(usage: number, limit: number | null): number | null {
  if (limit === null) return null;
  if (limit === 0) return usage === 0 ? 0 : 100;
  return (usage / limit) * 100;
}

function metricViews(quota: ProjectQuotaDTO, draft: QuotaDraft): QuotaMetricView[] {
  const modelUsage = Number(quota.model_cost_usd);
  const modelLimit = quota.monthly_model_cost_usd_limit;
  return [
    {
      key: "concurrent",
      icon: <Activity size={16} />,
      label: "并发执行",
      description: "当前运行中的项目工作流执行槽位。",
      usage: numberFormat.format(quota.concurrent_executions),
      limit: quota.concurrent_execution_limit === null ? "无限" : numberFormat.format(quota.concurrent_execution_limit),
      remaining: quota.concurrent_executions_remaining === null ? "无限" : numberFormat.format(quota.concurrent_executions_remaining),
      ratio: ratio(quota.concurrent_executions, quota.concurrent_execution_limit),
      tone: "pulse",
      draft: draft.concurrent,
      inputLabel: "并发执行上限",
    },
    {
      key: "storage",
      icon: <HardDrive size={16} />,
      label: "文档存储",
      description: "项目知识库中文档原文件的持久化字节数。",
      usage: formatBytes(quota.storage_bytes),
      limit: quota.storage_bytes_limit === null ? "无限" : formatBytes(quota.storage_bytes_limit),
      remaining: quota.storage_bytes_remaining === null ? "无限" : formatBytes(quota.storage_bytes_remaining),
      ratio: ratio(quota.storage_bytes, quota.storage_bytes_limit),
      tone: "ok",
      draft: draft.storage,
      inputLabel: "文档存储字节上限",
    },
    {
      key: "embedding",
      icon: <BrainCircuit size={16} />,
      label: "Embedding 输入",
      description: "UTC 月内发送给嵌入服务的未缓存 UTF-8 输入。",
      usage: formatBytes(quota.embedding_input_bytes),
      limit: quota.monthly_embedding_input_bytes_limit === null ? "无限" : formatBytes(quota.monthly_embedding_input_bytes_limit),
      remaining: quota.embedding_input_bytes_remaining === null ? "无限" : formatBytes(quota.embedding_input_bytes_remaining),
      ratio: ratio(quota.embedding_input_bytes, quota.monthly_embedding_input_bytes_limit),
      tone: "volt",
      draft: draft.embedding,
      inputLabel: "每月 Embedding 输入字节上限",
    },
    {
      key: "modelCost",
      icon: <CircleDollarSign size={16} />,
      label: "模型费用",
      description: "UTC 月内按模型定价与实际 Token 用量累计的费用。",
      usage: formatUsd(quota.model_cost_usd),
      limit: modelLimit === null ? "无限" : formatUsd(modelLimit),
      remaining: quota.model_cost_usd_remaining === null ? "无限" : formatUsd(quota.model_cost_usd_remaining),
      ratio: ratio(modelUsage, modelLimit === null ? null : Number(modelLimit)),
      tone: "warn",
      draft: draft.modelCost,
      inputLabel: "每月模型费用美元上限",
      inputStep: "0.000000000001",
    },
    {
      key: "mcp",
      icon: <Cable size={16} />,
      label: "MCP 进程",
      description: "项目专属 stdio MCP 子进程的实时占用。",
      usage: numberFormat.format(quota.stdio_mcp_processes),
      limit: quota.stdio_mcp_process_limit === null ? "无限" : numberFormat.format(quota.stdio_mcp_process_limit),
      remaining: quota.stdio_mcp_processes_remaining === null ? "无限" : numberFormat.format(quota.stdio_mcp_processes_remaining),
      ratio: ratio(quota.stdio_mcp_processes, quota.stdio_mcp_process_limit),
      tone: "bad",
      draft: draft.mcp,
      inputLabel: "MCP 进程上限",
    },
  ];
}

export function ProjectQuotasPage() {
  const { ready } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [projects, setProjects] = useState<ProjectDTO[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [quota, setQuota] = useState<ProjectQuotaDTO | null>(null);
  const [draft, setDraft] = useState<QuotaDraft | null>(null);
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (!ready) return;
    let active = true;
    setLoadingProjects(true);
    void listProjects()
      .then((rows) => {
        if (!active) return;
        setProjects(rows);
        const requested = searchParams.get("project_id");
        const next = rows.some((row) => row.id === requested) ? requested! : (rows[0]?.id ?? "");
        setSelectedId(next);
        if (next !== requested) {
          setSearchParams(next ? { project_id: next } : {}, { replace: true });
        }
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "加载项目失败");
      })
      .finally(() => {
        if (active) setLoadingProjects(false);
      });
    return () => {
      active = false;
    };
  }, [ready, searchParams, setSearchParams]);

  const loadQuota = useCallback(async (projectId: string) => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const next = await getProjectQuotas(projectId);
      setQuota(next);
      setDraft(draftFrom(next));
    } catch (cause) {
      setQuota(null);
      setDraft(null);
      setError(cause instanceof Error ? cause.message : "加载项目配额失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setEditing(false);
    void loadQuota(selectedId);
  }, [loadQuota, selectedId]);

  const metrics = useMemo(
    () => (quota && draft ? metricViews(quota, draft) : []),
    [draft, quota],
  );

  const selectProject = (projectId: string) => {
    setSelectedId(projectId);
    setSearchParams({ project_id: projectId }, { replace: true });
  };

  const cancelEdit = () => {
    if (quota) setDraft(draftFrom(quota));
    setEditing(false);
    setError(null);
  };

  const save = async () => {
    if (!quota || !draft) return;
    setSaving(true);
    setError(null);
    try {
      const next = await updateProjectQuotas(quota.project_id, updateFrom(draft));
      setQuota(next);
      setDraft(draftFrom(next));
      setEditing(false);
      setToast("项目配额已保存");
      window.setTimeout(() => setToast(null), 3000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存项目配额失败");
    } finally {
      setSaving(false);
    }
  };

  const changeDraft = (key: string, value: string | null) => {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  };

  const selected = projects.find((project) => project.id === selectedId) ?? null;

  return (
    <div className="ambient-stage flex h-full w-full flex-col bg-void text-ice">
      <header role="presentation" className="glass relative z-20 flex min-h-14 flex-wrap items-center gap-2 border-b border-line px-3 py-2 sm:px-5">
        <span className="flex h-8 w-8 items-center justify-center text-pulse"><Gauge size={19} /></span>
        <div className="min-w-0">
          <h1 className="font-display text-sm font-semibold text-ice sm:text-base">AgentCanvas Quotas</h1>
          <p className="font-mono text-[9px] uppercase text-ghost/50">project / usage / headroom</p>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <button type="button" onClick={() => void loadQuota(selectedId)} disabled={loading || !selectedId} className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-pulse disabled:opacity-40" title="刷新配额">
            <RefreshCw size={14} className={loading ? "animate-spin" : undefined} />
          </button>
        </div>
      </header>

      {error && (
        <div className="relative z-10 flex min-h-9 items-center gap-2 border-b border-bad/30 bg-bad/10 px-4 text-xs text-bad">
          <TriangleAlert size={13} />
          <span className="min-w-0 flex-1">{error}</span>
          <button type="button" onClick={() => setError(null)} className="flex h-7 w-7 items-center justify-center rounded-md hover:bg-bad/10" title="关闭"><X size={13} /></button>
        </div>
      )}

      <main className="relative z-10 min-h-0 flex-1 overflow-auto">
        <div className="mx-auto w-full max-w-6xl">
          <section className="flex flex-col gap-3 border-b border-line bg-ink/45 px-4 py-4 sm:px-6 lg:flex-row lg:items-end">
            <label className="min-w-0 flex-1">
              <span className="mb-1.5 block font-mono text-[9px] uppercase text-ghost/50">项目</span>
              <div className="relative">
                <FolderKanban size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-pulse" />
                <select aria-label="选择项目" value={selectedId} onChange={(event) => selectProject(event.target.value)} disabled={loadingProjects || projects.length === 0} className="field-input h-10 py-0 pl-9">
                  {projects.length === 0 && <option value="">没有可访问的项目</option>}
                  {projects.map((project) => <option key={project.id} value={project.id}>{project.name} / {project.slug}</option>)}
                </select>
              </div>
            </label>
            <div className="grid shrink-0 grid-cols-2 gap-3 lg:w-[22rem]">
              <div>
                <span className="font-mono text-[9px] uppercase text-ghost/50">周期</span>
                <p className="mt-1 text-xs text-ice">{quota ? new Date(`${quota.period_start}T00:00:00Z`).toLocaleDateString("zh-CN", { year: "numeric", month: "long", timeZone: "UTC" }) : "—"}</p>
              </div>
              <div>
                <span className="font-mono text-[9px] uppercase text-ghost/50">权限</span>
                <p className={quota?.can_update ? "mt-1 text-xs text-ok" : "mt-1 text-xs text-ghost"}>{quota?.can_update ? "可配置" : "只读"}</p>
              </div>
            </div>
            <div className="flex shrink-0 justify-end gap-1.5">
              {quota?.can_update && !editing && (
                <button type="button" onClick={() => setEditing(true)} className="flex h-9 items-center gap-1.5 rounded-md border border-line bg-ink px-3 text-xs text-ice transition hover:border-pulse/40 hover:text-pulse"><Pencil size={13} />编辑</button>
              )}
              {quota?.can_update && editing && (
                <>
                  <button type="button" onClick={cancelEdit} disabled={saving} className="flex h-9 w-9 items-center justify-center rounded-md border border-line text-ghost transition hover:text-ice disabled:opacity-40" title="取消编辑"><X size={14} /></button>
                  <button type="button" onClick={() => void save()} disabled={saving} className="flex h-9 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void transition hover:brightness-110 disabled:opacity-40">{saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}保存</button>
                </>
              )}
            </div>
          </section>

          {loadingProjects || (loading && !quota) ? (
            <div className="flex h-56 items-center justify-center gap-2 text-ghost"><Loader2 size={15} className="animate-spin" /><span className="text-xs">加载项目配额…</span></div>
          ) : !selected ? (
            <div className="flex h-64 flex-col items-center justify-center gap-2 text-center text-ghost/55"><FolderKanban size={24} /><p className="text-xs">当前没有可访问的项目</p></div>
          ) : quota && draft ? (
            <section aria-label="项目配额指标" className="border-b border-line bg-void/50">
              {metrics.map((metric) => (
                <QuotaMetricRow key={metric.key} metric={metric} editing={editing} disabled={saving} onDraftChange={(value) => changeDraft(metric.key, value)} />
              ))}
            </section>
          ) : null}
        </div>
      </main>

      {toast && <div className="fixed bottom-5 left-1/2 z-40 -translate-x-1/2 animate-fade-up"><div className="glass rounded-md border border-ok/30 px-4 py-2 text-xs text-ok shadow-card">{toast}</div></div>}
    </div>
  );
}
