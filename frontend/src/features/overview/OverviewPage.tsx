import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  CircleDollarSign,
  FolderKanban,
  Gauge,
  Loader2,
  Plus,
  RefreshCw,
  TriangleAlert,
  Workflow,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";

import { getOverview, type OverviewDTO } from "@/api/endpoints/overview";
import {
  getProjectQuotas,
  listProjects,
  type ProjectDTO,
  type ProjectQuotaDTO,
} from "@/api/endpoints/projectQuotas";
import { useAuth } from "@/features/auth/AuthProvider";
import {
  CostTrendPanel,
  QuotaWaterlinePanel,
  RecentWorkflowsPanel,
  type OverviewQuotaRow,
  quotaUsageRatio,
} from "@/features/overview/OverviewPanels";
import { cn } from "@/utils/cn";

const moneyFormat = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 6,
});
const numberFormat = new Intl.NumberFormat("zh-CN");

function money(value: string | null) {
  if (value === null) return "待定价";
  return moneyFormat.format(Number(value));
}

function quotaRows(quota: ProjectQuotaDTO | null): OverviewQuotaRow[] {
  if (!quota) return [];
  return [
    {
      label: "模型费用",
      usage: Number(quota.model_cost_usd),
      limit:
        quota.monthly_model_cost_usd_limit === null
          ? null
          : Number(quota.monthly_model_cost_usd_limit),
      display: money(quota.model_cost_usd),
      limitDisplay:
        quota.monthly_model_cost_usd_limit === null
          ? "无限"
          : money(quota.monthly_model_cost_usd_limit),
      tone: "bg-volt",
    },
    {
      label: "存储",
      usage: quota.storage_bytes,
      limit: quota.storage_bytes_limit,
      display: `${numberFormat.format(quota.storage_bytes)} B`,
      limitDisplay:
        quota.storage_bytes_limit === null
          ? "无限"
          : `${numberFormat.format(quota.storage_bytes_limit)} B`,
      tone: "bg-pulse",
    },
    {
      label: "Embedding",
      usage: quota.embedding_input_bytes,
      limit: quota.monthly_embedding_input_bytes_limit,
      display: `${numberFormat.format(quota.embedding_input_bytes)} B`,
      limitDisplay:
        quota.monthly_embedding_input_bytes_limit === null
          ? "无限"
          : `${numberFormat.format(quota.monthly_embedding_input_bytes_limit)} B`,
      tone: "bg-ok",
    },
    {
      label: "并发执行",
      usage: quota.concurrent_executions,
      limit: quota.concurrent_execution_limit,
      display: numberFormat.format(quota.concurrent_executions),
      limitDisplay:
        quota.concurrent_execution_limit === null
          ? "无限"
          : numberFormat.format(quota.concurrent_execution_limit),
      tone: "bg-warn",
    },
    {
      label: "MCP 进程",
      usage: quota.stdio_mcp_processes,
      limit: quota.stdio_mcp_process_limit,
      display: numberFormat.format(quota.stdio_mcp_processes),
      limitDisplay:
        quota.stdio_mcp_process_limit === null
          ? "无限"
          : numberFormat.format(quota.stdio_mcp_process_limit),
      tone: "bg-bad",
    },
  ];
}

export function OverviewPage() {
  const { ready } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [projects, setProjects] = useState<ProjectDTO[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [overview, setOverview] = useState<OverviewDTO | null>(null);
  const [quota, setQuota] = useState<ProjectQuotaDTO | null>(null);
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [loading, setLoading] = useState(true);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [quotaError, setQuotaError] = useState<string | null>(null);
  const requestGeneration = useRef(0);
  const requestedProjectId = searchParams.get("project_id") ?? "";
  const resolvedProjectId = projects.some((project) => project.id === requestedProjectId)
    ? requestedProjectId
    : (projects[0]?.id ?? "");
  const selectionReady =
    !loadingProjects && selectedProjectId === resolvedProjectId;

  useEffect(() => {
    if (!ready) return;
    let active = true;
    requestGeneration.current += 1;
    setLoadingProjects(true);
    setProjectError(null);
    void listProjects()
      .then((rows) => {
        if (!active) return;
        setProjects(rows);
      })
      .catch((cause: unknown) => {
        if (active) {
          setProjectError(cause instanceof Error ? cause.message : "加载项目失败");
        }
      })
      .finally(() => {
        if (active) setLoadingProjects(false);
      });
    return () => {
      active = false;
    };
  }, [ready]);

  useEffect(() => {
    if (loadingProjects) return;
    if (resolvedProjectId !== requestedProjectId) {
      const updated = new URLSearchParams(searchParams);
      if (resolvedProjectId) updated.set("project_id", resolvedProjectId);
      else updated.delete("project_id");
      setSearchParams(updated, { replace: true });
    }
    if (selectedProjectId === resolvedProjectId) return;
    requestGeneration.current += 1;
    setSelectedProjectId(resolvedProjectId);
    setOverview(null);
    setQuota(null);
    setLoading(true);
    setOverviewError(null);
    setQuotaError(null);
  }, [
    loadingProjects,
    requestedProjectId,
    resolvedProjectId,
    searchParams,
    selectedProjectId,
    setSearchParams,
  ]);

  const reload = useCallback(async (projectId: string) => {
    const generation = ++requestGeneration.current;
    setLoading(true);
    setOverviewError(null);
    setQuotaError(null);
    const [overviewResult, quotaResult] = await Promise.allSettled([
      getOverview(projectId || undefined),
      projectId ? getProjectQuotas(projectId) : Promise.resolve(null),
    ]);
    if (generation !== requestGeneration.current) return;
    if (overviewResult.status === "fulfilled") {
      setOverview(overviewResult.value);
    } else {
      setOverview(null);
      setOverviewError(
        overviewResult.reason instanceof Error
          ? overviewResult.reason.message
          : "加载运营概览失败",
      );
    }
    if (quotaResult.status === "fulfilled") {
      setQuota(quotaResult.value);
    } else {
      setQuota(null);
      setQuotaError(
        quotaResult.reason instanceof Error
          ? quotaResult.reason.message
          : "加载项目配额失败",
      );
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    if (selectionReady) void reload(selectedProjectId);
  }, [reload, selectedProjectId, selectionReady]);

  const selectProject = (projectId: string) => {
    requestGeneration.current += 1;
    setSelectedProjectId(projectId);
    setOverview(null);
    setQuota(null);
    setLoading(true);
    setOverviewError(null);
    setQuotaError(null);
    const updated = new URLSearchParams(searchParams);
    if (projectId) updated.set("project_id", projectId);
    else updated.delete("project_id");
    setSearchParams(updated, { replace: true });
  };

  const quotas = useMemo(() => quotaRows(quota), [quota]);
  const quotaWaterline = useMemo(() => {
    const finite = quotas
      .map((row) => quotaUsageRatio(row.usage, row.limit))
      .filter((value): value is number => value !== null);
    return finite.length ? Math.max(...finite) : null;
  }, [quotas]);
  const summary = overview?.execution_summary;
  const errors = [projectError, overviewError, quotaError].filter(
    (value): value is string => value !== null,
  );
  const quotaEmptyLabel = quotaError
    ? "配额数据不可用"
    : selectedProjectId
      ? "暂无配额数据"
      : "未选择项目";

  return (
    <main className="ambient-stage h-full min-w-0 overflow-y-auto bg-void text-ice">
      <div className="relative z-10 mx-auto w-full max-w-[1440px]">
        <header role="presentation" className="flex min-h-20 flex-col gap-3 border-b border-line px-4 py-4 sm:flex-row sm:items-center sm:px-6">
          <div className="min-w-0">
            <p className="font-mono text-[9px] uppercase text-pulse">Operations / 7 days</p>
            <h1 className="mt-1 font-display text-xl font-semibold text-ice">工作台概览</h1>
          </div>
          <div className="flex min-w-0 items-center gap-2 sm:ml-auto">
            <label className="relative min-w-0 flex-1 sm:w-56 sm:flex-none">
              <FolderKanban
                size={13}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-pulse"
              />
              <select
                aria-label="概览项目范围"
                value={selectedProjectId}
                onChange={(event) => selectProject(event.target.value)}
                disabled={loadingProjects}
                className="field-input h-9 truncate py-0 pl-9"
              >
                {projects.length === 0 && <option value="">未归属项目</option>}
                {projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => void reload(selectedProjectId)}
              disabled={loading}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-line text-ghost transition hover:border-pulse/40 hover:text-pulse disabled:opacity-40"
              title="刷新概览"
            >
              <RefreshCw size={14} className={loading ? "animate-spin" : undefined} />
            </button>
            <Link
              to="/workflows/new"
              aria-label="新建工作流"
              className="flex h-9 shrink-0 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void transition hover:brightness-110"
            >
              <Plus size={14} />
              <span className="hidden sm:inline">新建工作流</span>
            </Link>
          </div>
        </header>

        {errors.length > 0 && (
          <div className="flex min-h-10 items-center gap-2 border-b border-bad/30 bg-bad/10 px-4 text-xs text-bad sm:px-6">
            <TriangleAlert size={14} /> {errors.join("；")}
          </div>
        )}

        <section aria-label="核心指标" className="grid border-b border-line sm:grid-cols-2 xl:grid-cols-4">
          {[
            {
              label: "执行成功率",
              value:
                summary?.success_rate === null || summary?.success_rate === undefined
                  ? "—"
                  : `${Math.round(summary.success_rate * 100)}%`,
              detail: `${numberFormat.format(summary?.succeeded ?? 0)} 成功 / ${numberFormat.format(summary?.failed ?? 0)} 失败`,
              icon: Activity,
              tone: "text-ok",
            },
            {
              label: "近 7 日执行",
              value: numberFormat.format(summary?.total ?? 0),
              detail: `${numberFormat.format(summary?.active ?? 0)} 进行中`,
              icon: Workflow,
              tone: "text-pulse",
            },
            {
              label: "估算费用",
              value: overview ? money(overview.estimated_cost_usd) : "—",
              detail: overview?.cost_known ? "费率覆盖完整" : "存在未定价调用",
              icon: CircleDollarSign,
              tone: "text-volt",
            },
            {
              label: "配额水位",
              value:
                quotaError
                  ? "不可用"
                  : quotaWaterline === null
                    ? "未配置"
                    : `${Math.round(quotaWaterline * 100)}%`,
              detail: quotaError
                ? "配额服务请求失败"
                : quota
                  ? "当前项目最高占用"
                  : selectedProjectId
                    ? "暂无配额数据"
                    : "未选择项目",
              icon: Gauge,
              tone: quotaWaterline !== null && quotaWaterline >= 0.8 ? "text-warn" : "text-ghost",
            },
          ].map((metric) => {
            const Icon = metric.icon;
            return (
              <div key={metric.label} className="min-h-32 border-b border-line p-4 sm:p-5 xl:border-b-0 xl:border-r last:border-r-0">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[9px] uppercase text-ghost">{metric.label}</span>
                  <Icon size={15} className={metric.tone} />
                </div>
                <p className={cn("mt-4 truncate font-mono text-2xl", metric.tone)}>{metric.value}</p>
                <p className="mt-2 text-[11px] text-ghost">{metric.detail}</p>
              </div>
            );
          })}
        </section>

        {loading && !overview ? (
          <div className="flex h-72 items-center justify-center gap-2 text-ghost">
            <Loader2 size={16} className="animate-spin" />
            <span className="text-xs">加载运营数据…</span>
          </div>
        ) : (
          <div className="grid min-w-0 xl:grid-cols-[minmax(0,1.45fr)_minmax(20rem,0.8fr)]">
            <div className="min-w-0 border-b border-line xl:border-b-0 xl:border-r">
              <CostTrendPanel daily={overview?.daily ?? []} formatMoney={money} />
              <RecentWorkflowsPanel workflows={overview?.recent_workflows ?? []} />
            </div>
            <QuotaWaterlinePanel rows={quotas} emptyLabel={quotaEmptyLabel} />
          </div>
        )}
      </div>
    </main>
  );
}
