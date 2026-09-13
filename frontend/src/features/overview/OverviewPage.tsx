import { Activity, CircleDollarSign, Clock3, FolderKanban, Gauge, Loader2, Plus, RefreshCw, TriangleAlert, Workflow } from "lucide-react";
import { Link } from "react-router-dom";

import type { ProjectQuotaDTO } from "@/api/endpoints/projectQuotas";
import { useI18nStore, useT, type Translate } from "@/features/i18n/i18n";
import { CostTrendPanel } from "@/features/overview/OverviewCostChart";
import {
  QuotaWaterlinePanel,
  RecentWorkflowsPanel,
  WorkspaceShortcuts,
  type OverviewQuotaRow,
  quotaUsageRatio,
} from "@/features/overview/OverviewPanels";
import { useOverviewData, type OverviewRequestFailure } from "@/features/overview/useOverviewData";
import { cn } from "@/utils/cn";

function quotaRows(
  quota: ProjectQuotaDTO | null,
  t: Translate,
  money: (value: string | null) => string,
  numberFormat: Intl.NumberFormat,
): OverviewQuotaRow[] {
  if (!quota) return [];
  const bytes = (value: number) => `${numberFormat.format(value)} B`;
  return [
    {
      label: t("overview.modelCost"),
      usage: Number(quota.model_cost_usd),
      limit: quota.monthly_model_cost_usd_limit === null ? null : Number(quota.monthly_model_cost_usd_limit),
      display: money(quota.model_cost_usd),
      limitDisplay: quota.monthly_model_cost_usd_limit === null ? t("overview.unlimited") : money(quota.monthly_model_cost_usd_limit),
      tone: "bg-volt",
    },
    {
      label: t("overview.storage"),
      usage: quota.storage_bytes,
      limit: quota.storage_bytes_limit,
      display: bytes(quota.storage_bytes),
      limitDisplay: quota.storage_bytes_limit === null ? t("overview.unlimited") : bytes(quota.storage_bytes_limit),
      tone: "bg-pulse",
    },
    {
      label: "Embedding",
      usage: quota.embedding_input_bytes,
      limit: quota.monthly_embedding_input_bytes_limit,
      display: bytes(quota.embedding_input_bytes),
      limitDisplay: quota.monthly_embedding_input_bytes_limit === null ? t("overview.unlimited") : bytes(quota.monthly_embedding_input_bytes_limit),
      tone: "bg-ok",
    },
    {
      label: t("overview.concurrent"),
      usage: quota.concurrent_executions,
      limit: quota.concurrent_execution_limit,
      display: numberFormat.format(quota.concurrent_executions),
      limitDisplay: quota.concurrent_execution_limit === null ? t("overview.unlimited") : numberFormat.format(quota.concurrent_execution_limit),
      tone: "bg-pulse",
    },
    {
      label: t("overview.mcpProcesses"),
      usage: quota.stdio_mcp_processes,
      limit: quota.stdio_mcp_process_limit,
      display: numberFormat.format(quota.stdio_mcp_processes),
      limitDisplay: quota.stdio_mcp_process_limit === null ? t("overview.unlimited") : numberFormat.format(quota.stdio_mcp_process_limit),
      tone: "bg-volt",
    },
  ];
}

function failureMessage(failure: OverviewRequestFailure | null, fallback: string): string | null {
  if (!failure) return null;
  return failure.cause instanceof Error ? failure.cause.message : fallback;
}

export function OverviewPage() {
  const t = useT();
  const locale = useI18nStore((state) => state.locale);
  const {
    projects, selectedProjectId, selectProject, overview, quota,
    loadingProjects, busy, projectError, overviewError, quotaError, refresh,
  } = useOverviewData();
  const numberLocale = locale === "zh" ? "zh-CN" : "en-US";
  const numberFormat = new Intl.NumberFormat(numberLocale);
  const moneyFormat = new Intl.NumberFormat(numberLocale, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 6,
  });
  const money = (value: string | null) => {
    if (value === null) return t("overview.unpriced");
    const amount = Number(value);
    return Number.isFinite(amount) ? moneyFormat.format(amount) : t("overview.unavailable");
  };
  const quotas = quotaRows(quota, t, money, numberFormat);
  const finiteRatios = quotas.map((row) => quotaUsageRatio(row.usage, row.limit)).filter((value): value is number => value !== null);
  const quotaWaterline = finiteRatios.length ? Math.max(...finiteRatios) : null;
  const summary = overview?.execution_summary;
  const unavailable = Boolean(projectError || overviewError);
  const unavailableQuota = Boolean(projectError || quotaError);
  const missingDetail = t(unavailable ? "overview.unavailable" : "overview.waiting");
  const errors = [
    failureMessage(projectError, t("overview.projectsFailed")),
    failureMessage(overviewError, t("overview.loadFailed")),
    failureMessage(quotaError, t("overview.loadQuotaFailed")),
  ].filter((message): message is string => message !== null);
  const quotaEmptyLabel = unavailableQuota
    ? t("overview.quotaUnavailable")
    : busy ? t("overview.loading")
      : selectedProjectId ? t("overview.noQuota") : t("overview.noProject");
  const metrics = [
    {
      key: "success",
      label: t("overview.successRate"),
      value: summary?.success_rate == null ? "—" : `${Math.round(summary.success_rate * 100)}%`,
      detail: summary ? t("overview.successDetail", { succeeded: numberFormat.format(summary.succeeded), failed: numberFormat.format(summary.failed) }) : missingDetail,
      icon: Activity,
      tone: "text-ok",
    },
    {
      key: "executions",
      label: t("overview.executions"),
      value: summary ? numberFormat.format(summary.total) : "—",
      detail: summary ? t("overview.activeDetail", { count: numberFormat.format(summary.active) }) : missingDetail,
      icon: Workflow,
      tone: "text-pulse",
    },
    {
      key: "cost",
      label: t("overview.estimatedCost"),
      value: overview ? money(overview.cost_known ? overview.estimated_cost_usd : null) : "—",
      detail: overview ? t(overview.cost_known ? "overview.priced" : "overview.unpricedCalls") : missingDetail,
      icon: CircleDollarSign,
      tone: "text-volt",
    },
    {
      key: "quota",
      label: t("overview.quota"),
      value: unavailableQuota ? t("overview.unavailable") : quota ? quotaWaterline === null ? t("overview.notConfigured") : `${Math.round(quotaWaterline * 100)}%` : "—",
      detail: unavailableQuota ? t("overview.quotaFailed") : quota ? t("overview.highestUsage") : quotaEmptyLabel,
      icon: Gauge,
      tone: quotaWaterline !== null && quotaWaterline >= 0.8 ? "text-warn" : "text-ghost",
    },
  ];

  return (
    <main className="workspace-overview h-full min-w-0 overflow-y-auto text-ice" aria-busy={busy}>
      <div className="mx-auto w-full max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <header className="workspace-reveal flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <span className="inline-flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-pulse" aria-hidden="true" />
                <span className="workspace-eyebrow text-pulse">{t("overview.eyebrow")}</span>
              </span>
              <span className="workspace-badge">
                <Clock3 size={11} aria-hidden="true" />{t("overview.period")}
              </span>
            </div>
            <h1 className="workspace-display-title mt-3">{t("overview.title")}</h1>
            <p className="mt-2.5 max-w-xl text-xs leading-relaxed text-ghost sm:text-[13px]">{t("overview.description")}</p>
          </div>
          <div className="flex min-w-0 items-center gap-2">
            <label className="relative min-w-0 flex-1 sm:w-52 sm:flex-none">
              <FolderKanban size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ghost" aria-hidden="true" />
              <select
                aria-label={t("overview.projectScope")}
                value={selectedProjectId}
                onChange={(event) => selectProject(event.target.value)}
                disabled={loadingProjects || projects.length === 0}
                className="field-input h-10 truncate bg-ink py-0 pl-9 disabled:cursor-not-allowed"
              >
                {projects.length === 0 && <option value="">{projectError ? t("overview.unavailable") : t("overview.unassigned")}</option>}
                {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
              </select>
            </label>
            <button
              type="button"
              onClick={refresh}
              disabled={busy}
              className="workspace-icon-button h-10 w-10 shrink-0 border border-line bg-ink"
              title={t("overview.refresh")}
              aria-label={t("overview.refresh")}
            >
              <RefreshCw size={15} className={busy ? "animate-spin" : undefined} aria-hidden="true" />
            </button>
            <Link to="/workflows/new" aria-label={t("overview.newWorkflow")} className="workspace-primary-button shrink-0">
              <Plus size={15} aria-hidden="true" /><span className="hidden sm:inline">{t("overview.newWorkflow")}</span>
            </Link>
          </div>
        </header>

        <div className="flex h-7 items-center text-[10px] text-ghost" role="status">
          {busy && <span className="flex items-center gap-1.5"><Loader2 size={11} className="animate-spin" aria-hidden="true" />{t(overview ? "overview.refreshing" : "overview.loading")}</span>}
        </div>

        {errors.length > 0 && (
          <div role="alert" className="mb-5 flex min-w-0 items-start gap-3 rounded-lg border border-bad/25 bg-bad/5 p-4 text-xs text-bad">
            <TriangleAlert size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
            <p className="min-w-0 flex-1 break-words leading-relaxed">{errors.join(" · ")}</p>
            <button type="button" onClick={refresh} disabled={busy} className="shrink-0 rounded-sm font-medium underline underline-offset-4 disabled:opacity-50">{t("overview.retry")}</button>
          </div>
        )}

        <section aria-label={t("overview.metrics")} className="overview-metrics workspace-reveal mt-6 grid grid-cols-2 overflow-hidden rounded-xl border border-line bg-ink xl:grid-cols-4">
          {metrics.map((metric) => {
            const Icon = metric.icon;
            return (
              <div key={metric.key} data-metric={metric.key} className="overview-metric">
                <div className="flex items-start justify-between gap-3">
                  <span className="workspace-stat-label">{metric.label}</span>
                  <span className={cn("workspace-stat-icon", metric.tone)}>
                    <Icon size={15} aria-hidden="true" />
                  </span>
                </div>
                <p className="workspace-stat-value">{metric.value}</p>
                <p className="workspace-stat-detail">{metric.detail}</p>
              </div>
            );
          })}
        </section>

        <div className="mt-6 grid min-w-0 items-start gap-6 xl:grid-cols-[minmax(0,1.7fr)_minmax(280px,1fr)]">
          <div className="overview-panel min-w-0 overflow-hidden">
            {busy && !overview ? (
              <div className="p-5 sm:p-6" aria-hidden="true">
                <div className="overview-skeleton h-3 w-28" /><div className="overview-skeleton mt-4 h-7 w-40" />
                <div className="mt-8 grid h-40 grid-cols-7 items-end gap-3" aria-hidden="true">
                  {[35, 60, 45, 80, 55, 95, 70].map((height, index) => <div key={index} className="overview-skeleton" style={{ height: `${height}%` }} />)}
                </div>
                <div className="mt-8 space-y-4 border-t border-line pt-6" aria-hidden="true">
                  <div className="overview-skeleton h-10 w-full" /><div className="overview-skeleton h-10 w-full" />
                </div>
              </div>
            ) : unavailable ? (
              <div className="workspace-empty min-h-80 p-6">
                <TriangleAlert size={26} className="text-bad" aria-hidden="true" />
                <h2 className="text-sm font-semibold text-ice">{t("overview.dataUnavailable")}</h2>
                <p className="max-w-sm text-xs leading-relaxed text-ghost">{t("overview.retryHint")}</p>
              </div>
            ) : overview ? (
              <>
                <CostTrendPanel daily={overview.daily} formatMoney={money} />
                <RecentWorkflowsPanel workflows={overview.recent_workflows} />
              </>
            ) : null}
          </div>
          <div className="overview-panel min-w-0 overflow-hidden">
            <QuotaWaterlinePanel rows={quotas} emptyLabel={quotaEmptyLabel} emptyHint={!busy && !unavailableQuota && !selectedProjectId ? t("overview.quotaHint") : undefined} />
            <WorkspaceShortcuts />
          </div>
        </div>
      </div>
    </main>
  );
}
