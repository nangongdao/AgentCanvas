import { ArrowRight, Workflow } from "lucide-react";
import { Link } from "react-router-dom";

import type {
  OverviewDayDTO,
  OverviewWorkflowDTO,
} from "@/api/endpoints/overview";
import { cn } from "@/utils/cn";

export interface OverviewQuotaRow {
  label: string;
  usage: number;
  limit: number | null;
  display: string;
  limitDisplay: string;
  tone: string;
}

export function quotaUsageRatio(usage: number, limit: number | null) {
  if (limit === null) return null;
  if (limit === 0) return 1;
  return usage / limit;
}

export function CostTrendPanel({
  daily,
  formatMoney,
}: {
  daily: readonly OverviewDayDTO[];
  formatMoney: (value: string | null) => string;
}) {
  const maxDailyCost = Math.max(
    0,
    ...daily.map((day) => Number(day.estimated_cost_usd ?? 0)),
  );
  return (
    <section
      aria-labelledby="cost-trend-title"
      className="border-b border-line px-4 py-5 sm:px-6"
    >
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="font-mono text-[9px] uppercase text-ghost">Spend signal</p>
          <h2 id="cost-trend-title" className="mt-1 text-sm font-semibold text-ice">
            费用趋势
          </h2>
        </div>
        <Link
          to="/cost"
          className="flex items-center gap-1 text-xs text-ghost transition hover:text-pulse"
        >
          成本治理 <ArrowRight size={13} />
        </Link>
      </div>
      <div className="mt-5 grid h-44 grid-cols-7 items-end gap-2 border-b border-line px-1 sm:gap-4">
        {daily.map((day) => {
          const value = Number(day.estimated_cost_usd ?? 0);
          const height = maxDailyCost > 0 ? Math.max(3, (value / maxDailyCost) * 100) : 3;
          const costLabel = day.cost_known
            ? formatMoney(day.estimated_cost_usd)
            : "存在未定价调用";
          return (
            <div
              key={day.date}
              className="flex h-full min-w-0 flex-col items-center justify-end gap-2"
            >
              <span className="hidden font-mono text-[9px] text-ghost sm:block">
                {day.cost_known ? costLabel : "?"}
              </span>
              <div className="flex h-28 w-full items-end justify-center">
                <div
                  className={cn(
                    "w-full max-w-9 rounded-t-xs border border-b-0",
                    day.cost_known
                      ? "border-volt/50 bg-volt/35"
                      : "border-warn/50 bg-warn/20",
                  )}
                  style={{ height: `${height}%` }}
                  title={`${day.date}: ${costLabel}`}
                />
              </div>
              <span className="font-mono text-[9px] text-ghost">{day.date.slice(5)}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function RecentWorkflowsPanel({
  workflows,
}: {
  workflows: readonly OverviewWorkflowDTO[];
}) {
  return (
    <section aria-labelledby="recent-workflows-title" className="px-4 py-5 sm:px-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-mono text-[9px] uppercase text-ghost">Recently updated</p>
          <h2 id="recent-workflows-title" className="mt-1 text-sm font-semibold text-ice">
            最近工作流
          </h2>
        </div>
        <Link
          to="/workflows/new"
          className="flex items-center gap-1 text-xs text-ghost transition hover:text-pulse"
        >
          全部工作流 <ArrowRight size={13} />
        </Link>
      </div>
      <div className="mt-4 divide-y divide-line border-y border-line">
        {workflows.length ? (
          workflows.map((workflow) => (
            <Link
              key={workflow.id}
              to={`/workflows/${workflow.id}`}
              className="group flex min-h-16 min-w-0 items-center gap-3 px-1 py-3 transition hover:bg-line/30 sm:px-2"
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line bg-ink text-pulse">
                <Workflow size={14} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-medium text-ice">
                  {workflow.name}
                </span>
                <span className="mt-1 block truncate text-[11px] text-ghost">
                  {workflow.description || "暂无描述"}
                </span>
              </span>
              <span className="hidden shrink-0 font-mono text-[9px] text-ghost sm:block">
                {new Date(workflow.updated_at).toLocaleDateString("zh-CN")}
              </span>
              <ArrowRight
                size={13}
                className="shrink-0 text-ghost transition group-hover:text-pulse"
              />
            </Link>
          ))
        ) : (
          <div className="flex h-28 items-center justify-center text-xs text-ghost">
            暂无工作流
          </div>
        )}
      </div>
    </section>
  );
}

export function QuotaWaterlinePanel({
  rows,
  emptyLabel = "未选择项目",
}: {
  rows: readonly OverviewQuotaRow[];
  emptyLabel?: string;
}) {
  return (
    <aside className="min-w-0 px-4 py-5 sm:px-6" aria-labelledby="quota-title">
      <p className="font-mono text-[9px] uppercase text-ghost">Current headroom</p>
      <div className="mt-1 flex items-center justify-between gap-3">
        <h2 id="quota-title" className="text-sm font-semibold text-ice">
          配额水位
        </h2>
        <Link
          to="/settings/quotas"
          className="flex items-center gap-1 text-xs text-ghost transition hover:text-pulse"
        >
          配额设置 <ArrowRight size={13} />
        </Link>
      </div>
      <div className="mt-5 divide-y divide-line border-y border-line">
        {rows.length ? (
          rows.map((row) => {
            const ratio = quotaUsageRatio(row.usage, row.limit);
            const percentage = ratio === null ? null : Math.round(ratio * 100);
            return (
              <div key={row.label} className="py-4">
                <div className="flex items-center justify-between gap-3 text-[11px]">
                  <span className="text-ghost">{row.label}</span>
                  <span className="font-mono text-ice">
                    {row.display} / {row.limitDisplay}
                  </span>
                </div>
                <div
                  className="mt-2 h-1.5 overflow-hidden rounded-full bg-line"
                  role={ratio === null ? undefined : "progressbar"}
                  aria-label={ratio === null ? undefined : `${row.label}配额占用`}
                  aria-valuemin={ratio === null ? undefined : 0}
                  aria-valuemax={ratio === null ? undefined : 100}
                  aria-valuenow={
                    percentage === null ? undefined : Math.min(100, Math.max(0, percentage))
                  }
                  aria-valuetext={percentage === null ? undefined : `${percentage}%`}
                >
                  {ratio !== null && (
                    <div
                      className={cn("h-full rounded-full", row.tone)}
                      style={{ width: `${Math.min(100, ratio * 100)}%` }}
                    />
                  )}
                </div>
              </div>
            );
          })
        ) : (
          <div className="flex h-32 items-center justify-center text-xs text-ghost">
            {emptyLabel}
          </div>
        )}
      </div>
    </aside>
  );
}
