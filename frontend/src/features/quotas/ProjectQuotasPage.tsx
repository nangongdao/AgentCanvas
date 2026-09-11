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
import { useI18nStore, useT, type Translate } from "@/features/i18n/i18n";
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

/** Locale-bound formatters; the units themselves (bytes, USD) stay neutral. */
interface QuotaFormat {
  number: Intl.NumberFormat;
  tag: string;
}

function makeFormat(locale: "zh" | "en"): QuotaFormat {
  const tag = locale === "zh" ? "zh-CN" : "en-US";
  return { number: new Intl.NumberFormat(tag), tag };
}

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

function parseInteger(value: string | null, label: string, t: Translate): number | null {
  if (value === null) return null;
  if (!/^\d+$/.test(value)) throw new Error(t("quotas.error.integer", { label }));
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) throw new Error(t("quotas.error.overflow", { label }));
  return parsed;
}

function updateFrom(draft: QuotaDraft, t: Translate): ProjectQuotaUpdate {
  if (
    draft.modelCost !== null &&
    !/^\d+(?:\.\d{1,12})?$/.test(draft.modelCost)
  ) {
    throw new Error(
      t("quotas.error.decimal", { label: t("quotas.metric.modelCost.label") }),
    );
  }
  return {
    concurrent_execution_limit: parseInteger(draft.concurrent, t("quotas.field.concurrent"), t),
    storage_bytes_limit: parseInteger(draft.storage, t("quotas.field.storage"), t),
    monthly_embedding_input_bytes_limit: parseInteger(
      draft.embedding,
      t("quotas.field.embedding"),
      t,
    ),
    monthly_model_cost_usd_limit: draft.modelCost,
    stdio_mcp_process_limit: parseInteger(draft.mcp, t("quotas.field.mcp"), t),
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

function metricViews(
  quota: ProjectQuotaDTO,
  draft: QuotaDraft,
  t: Translate,
  format: QuotaFormat,
): QuotaMetricView[] {
  const modelUsage = Number(quota.model_cost_usd);
  const modelLimit = quota.monthly_model_cost_usd_limit;
  const unlimited = t("quotas.unlimited");
  return [
    {
      key: "concurrent",
      icon: <Activity size={16} />,
      label: t("quotas.metric.concurrent.label"),
      description: t("quotas.metric.concurrent.description"),
      usage: format.number.format(quota.concurrent_executions),
      limit: quota.concurrent_execution_limit === null ? unlimited : format.number.format(quota.concurrent_execution_limit),
      remaining: quota.concurrent_executions_remaining === null ? unlimited : format.number.format(quota.concurrent_executions_remaining),
      ratio: ratio(quota.concurrent_executions, quota.concurrent_execution_limit),
      tone: "pulse",
      draft: draft.concurrent,
      inputLabel: t("quotas.metric.concurrent.input"),
    },
    {
      key: "storage",
      icon: <HardDrive size={16} />,
      label: t("quotas.metric.storage.label"),
      description: t("quotas.metric.storage.description"),
      usage: formatBytes(quota.storage_bytes),
      limit: quota.storage_bytes_limit === null ? unlimited : formatBytes(quota.storage_bytes_limit),
      remaining: quota.storage_bytes_remaining === null ? unlimited : formatBytes(quota.storage_bytes_remaining),
      ratio: ratio(quota.storage_bytes, quota.storage_bytes_limit),
      tone: "ok",
      draft: draft.storage,
      inputLabel: t("quotas.metric.storage.input"),
    },
    {
      key: "embedding",
      icon: <BrainCircuit size={16} />,
      label: t("quotas.metric.embedding.label"),
      description: t("quotas.metric.embedding.description"),
      usage: formatBytes(quota.embedding_input_bytes),
      limit: quota.monthly_embedding_input_bytes_limit === null ? unlimited : formatBytes(quota.monthly_embedding_input_bytes_limit),
      remaining: quota.embedding_input_bytes_remaining === null ? unlimited : formatBytes(quota.embedding_input_bytes_remaining),
      ratio: ratio(quota.embedding_input_bytes, quota.monthly_embedding_input_bytes_limit),
      tone: "volt",
      draft: draft.embedding,
      inputLabel: t("quotas.metric.embedding.input"),
    },
    {
      key: "modelCost",
      icon: <CircleDollarSign size={16} />,
      label: t("quotas.metric.modelCost.label"),
      description: t("quotas.metric.modelCost.description"),
      usage: formatUsd(quota.model_cost_usd),
      limit: modelLimit === null ? unlimited : formatUsd(modelLimit),
      remaining: quota.model_cost_usd_remaining === null ? unlimited : formatUsd(quota.model_cost_usd_remaining),
      ratio: ratio(modelUsage, modelLimit === null ? null : Number(modelLimit)),
      tone: "warn",
      draft: draft.modelCost,
      inputLabel: t("quotas.metric.modelCost.input"),
      inputStep: "0.000000000001",
    },
    {
      key: "mcp",
      icon: <Cable size={16} />,
      label: t("quotas.metric.mcp.label"),
      description: t("quotas.metric.mcp.description"),
      usage: format.number.format(quota.stdio_mcp_processes),
      limit: quota.stdio_mcp_process_limit === null ? unlimited : format.number.format(quota.stdio_mcp_process_limit),
      remaining: quota.stdio_mcp_processes_remaining === null ? unlimited : format.number.format(quota.stdio_mcp_processes_remaining),
      ratio: ratio(quota.stdio_mcp_processes, quota.stdio_mcp_process_limit),
      tone: "bad",
      draft: draft.mcp,
      inputLabel: t("quotas.metric.mcp.input"),
    },
  ];
}

export function ProjectQuotasPage() {
  const t = useT();
  const locale = useI18nStore((state) => state.locale);
  const format = useMemo(() => makeFormat(locale), [locale]);
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
        if (active) setError(cause instanceof Error ? cause.message : t("quotas.loadProjectsFailed"));
      })
      .finally(() => {
        if (active) setLoadingProjects(false);
      });
    return () => {
      active = false;
    };
  }, [ready, searchParams, setSearchParams, t]);

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
      setError(cause instanceof Error ? cause.message : t("quotas.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    setEditing(false);
    void loadQuota(selectedId);
  }, [loadQuota, selectedId]);

  const metrics = useMemo(
    () => (quota && draft ? metricViews(quota, draft, t, format) : []),
    [draft, format, quota, t],
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
      const next = await updateProjectQuotas(quota.project_id, updateFrom(draft, t));
      setQuota(next);
      setDraft(draftFrom(next));
      setEditing(false);
      setToast(t("quotas.saved"));
      window.setTimeout(() => setToast(null), 3000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("quotas.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const changeDraft = (key: string, value: string | null) => {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  };

  const selected = projects.find((project) => project.id === selectedId) ?? null;

  return (
    <div className="ambient-stage flex h-full w-full flex-col text-ice">
      <header role="presentation" className="glass relative z-20 flex min-h-14 flex-wrap items-center gap-2 border-b border-line px-3 py-2 sm:px-5">
        <span className="flex h-8 w-8 items-center justify-center text-pulse"><Gauge size={19} /></span>
        <div className="min-w-0">
          <h1 className="workspace-page-title">{t("quotas.title")}</h1>
          <p className="font-mono text-[9px] uppercase text-ghost/50">{t("quotas.eyebrow")}</p>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <button type="button" onClick={() => void loadQuota(selectedId)} disabled={loading || !selectedId} className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-pulse disabled:opacity-40" title={t("quotas.refresh")}>
            <RefreshCw size={14} className={loading ? "animate-spin" : undefined} />
          </button>
        </div>
      </header>

      {error && (
        <div className="relative z-10 flex min-h-9 items-center gap-2 border-b border-bad/30 bg-bad/10 px-4 text-xs text-bad">
          <TriangleAlert size={13} />
          <span className="min-w-0 flex-1">{error}</span>
          <button type="button" onClick={() => setError(null)} className="flex h-7 w-7 items-center justify-center rounded-md hover:bg-bad/10" title={t("quotas.close")}><X size={13} /></button>
        </div>
      )}

      <main className="relative z-10 min-h-0 flex-1 overflow-auto">
        <div className="mx-auto w-full max-w-6xl">
          <section className="flex flex-col gap-3 border-b border-line bg-ink/45 px-4 py-4 sm:px-6 lg:flex-row lg:items-end">
            <label className="min-w-0 flex-1">
              <span className="mb-1.5 block font-mono text-[9px] uppercase text-ghost/50">{t("quotas.project")}</span>
              <div className="relative">
                <FolderKanban size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-pulse" />
                <select aria-label={t("quotas.chooseProject")} value={selectedId} onChange={(event) => selectProject(event.target.value)} disabled={loadingProjects || projects.length === 0} className="field-input h-10 py-0 pl-9">
                  {projects.length === 0 && <option value="">{t("quotas.noProjects")}</option>}
                  {projects.map((project) => <option key={project.id} value={project.id}>{project.name} / {project.slug}</option>)}
                </select>
              </div>
            </label>
            <div className="grid shrink-0 grid-cols-2 gap-3 lg:w-[22rem]">
              <div>
                <span className="font-mono text-[9px] uppercase text-ghost/50">{t("quotas.period")}</span>
                <p className="mt-1 text-xs text-ice">{quota ? new Date(`${quota.period_start}T00:00:00Z`).toLocaleDateString(format.tag, { year: "numeric", month: "long", timeZone: "UTC" }) : "—"}</p>
              </div>
              <div>
                <span className="font-mono text-[9px] uppercase text-ghost/50">{t("quotas.permission")}</span>
                <p className={quota?.can_update ? "mt-1 text-xs text-ok" : "mt-1 text-xs text-ghost"}>{quota?.can_update ? t("quotas.writable") : t("quotas.readonly")}</p>
              </div>
            </div>
            <div className="flex shrink-0 justify-end gap-1.5">
              {quota?.can_update && !editing && (
                <button type="button" onClick={() => setEditing(true)} className="flex h-9 items-center gap-1.5 rounded-md border border-line bg-ink px-3 text-xs text-ice transition hover:border-pulse/40 hover:text-pulse"><Pencil size={13} />{t("quotas.edit")}</button>
              )}
              {quota?.can_update && editing && (
                <>
                  <button type="button" onClick={cancelEdit} disabled={saving} className="flex h-9 w-9 items-center justify-center rounded-md border border-line text-ghost transition hover:text-ice disabled:opacity-40" title={t("quotas.cancelEdit")}><X size={14} /></button>
                  <button type="button" onClick={() => void save()} disabled={saving} className="flex h-9 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void transition hover:brightness-110 disabled:opacity-40">{saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}{t("quotas.save")}</button>
                </>
              )}
            </div>
          </section>

          {loadingProjects || (loading && !quota) ? (
            <div className="flex h-56 items-center justify-center gap-2 text-ghost"><Loader2 size={15} className="animate-spin" /><span className="text-xs">{t("quotas.loading")}</span></div>
          ) : !selected ? (
            <div className="flex h-64 flex-col items-center justify-center gap-2 text-center text-ghost/55"><FolderKanban size={24} /><p className="text-xs">{t("quotas.noAccessibleProject")}</p></div>
          ) : quota && draft ? (
            <section aria-label={t("quotas.metricsAria")} className="border-b border-line bg-void/50">
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
