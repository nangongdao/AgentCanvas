import { ArrowRight, BrainCircuit, Database, Gauge, Plus, Workflow } from "lucide-react";
import { Link } from "react-router-dom";

import type { OverviewWorkflowDTO } from "@/api/endpoints/overview";
import { useI18nStore, useT } from "@/features/i18n/i18n";
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
  // A zero limit disables the resource, even when current usage is zero.
  if (limit === 0) return 1;
  return usage / limit;
}

export function RecentWorkflowsPanel({ workflows }: { workflows: readonly OverviewWorkflowDTO[] }) {
  const t = useT();
  const locale = useI18nStore((state) => state.locale);
  return (
    <section aria-labelledby="recent-workflows-title" className="border-t border-line p-5 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="workspace-eyebrow">{t("overview.recentCaption")}</p>
          <h2 id="recent-workflows-title" className="workspace-section-title mt-2 flex items-center gap-2">
            {t("overview.recentWorkflows")}
            <span className="rounded-md border border-line bg-raise px-1.5 py-0.5 font-mono text-[10px] font-normal text-ghost">{workflows.length}</span>
          </h2>
        </div>
        <Link to="/workflows/new" className="workspace-text-link">
          {t("overview.allWorkflows")} <ArrowRight size={14} aria-hidden="true" />
        </Link>
      </div>
      {workflows.length ? (
        <div className="mt-5 divide-y divide-line">
          {workflows.map((workflow) => (
            <Link key={workflow.id} to={`/workflows/${workflow.id}`} className="workspace-workflow-row group">
              <span className="workspace-row-glyph">
                <Workflow size={16} aria-hidden="true" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-medium text-ice" title={workflow.name}>{workflow.name}</span>
                <span className="mt-1.5 block truncate text-[11px] text-ghost">{workflow.description || t("overview.noDescription")}</span>
              </span>
              <time dateTime={workflow.updated_at} className="hidden shrink-0 font-mono text-[10px] text-ghost sm:block">
                {new Date(workflow.updated_at).toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US")}
              </time>
              <ArrowRight size={14} className="workspace-row-arrow shrink-0 text-ghost" aria-hidden="true" />
            </Link>
          ))}
        </div>
      ) : (
        <div className="workspace-empty min-h-52">
          <Workflow size={25} className="text-pulse" aria-hidden="true" />
          <p className="text-sm font-medium text-ice">{t("overview.noWorkflows")}</p>
          <p className="max-w-xs text-xs leading-relaxed text-ghost">{t("overview.noWorkflowsHint")}</p>
          <Link to="/workflows/new" className="workspace-text-link mt-2 text-pulse">
            <Plus size={14} aria-hidden="true" />{t("overview.newWorkflow")}
          </Link>
        </div>
      )}
    </section>
  );
}

export function QuotaWaterlinePanel({
  rows,
  emptyLabel,
  emptyHint,
}: {
  rows: readonly OverviewQuotaRow[];
  emptyLabel: string;
  emptyHint?: string;
}) {
  const t = useT();
  return (
    <section className="min-w-0 p-5 sm:p-6" aria-labelledby="quota-title">
      <p className="workspace-eyebrow">{t("overview.quotaCaption")}</p>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
        <h2 id="quota-title" className="workspace-section-title">{t("overview.quota")}</h2>
        <Link to="/settings/quotas" className="workspace-text-link">
          {t("overview.quotaSettings")} <ArrowRight size={14} aria-hidden="true" />
        </Link>
      </div>
      {rows.length ? (
        <div className="mt-3 divide-y divide-line">
          {rows.map((row) => {
            const ratio = quotaUsageRatio(row.usage, row.limit);
            const percentage = ratio === null ? null : Math.round(ratio * 100);
            const pressured = ratio !== null && ratio >= 0.8;
            return (
              <div key={row.label} className="py-4">
                <div className="flex items-start justify-between gap-3 text-xs">
                  <span className="text-ice">{row.label}</span>
                  <span className={cn("shrink-0 font-mono text-[10px]", pressured ? "text-warn" : "text-ghost")}>
                    {percentage === null ? t("overview.unlimited") : `${percentage}%`}
                  </span>
                </div>
                <p className="mt-2 break-words font-mono text-[10px] leading-relaxed text-ghost">{row.display} / {row.limitDisplay}</p>
                <div
                  className="mt-3 h-1.5 overflow-hidden rounded-full bg-line"
                  role={ratio === null ? undefined : "progressbar"}
                  aria-label={ratio === null ? undefined : t("overview.quotaAria", { label: row.label })}
                  aria-valuemin={ratio === null ? undefined : 0}
                  aria-valuemax={ratio === null ? undefined : 100}
                  aria-valuenow={percentage === null ? undefined : Math.min(100, Math.max(0, percentage))}
                  aria-valuetext={percentage === null ? undefined : `${row.display} / ${row.limitDisplay} (${percentage}%)`}
                >
                  {ratio !== null && (
                    <div className={cn("h-full rounded-full", pressured ? "bg-warn" : row.tone)} style={{ width: `${Math.min(100, Math.max(0, ratio * 100))}%` }} />
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="workspace-empty min-h-52">
          <Gauge size={24} className="text-ghost" aria-hidden="true" />
          <p className="text-xs text-ice">{emptyLabel}</p>
          {emptyHint && <p className="max-w-60 text-xs leading-relaxed text-ghost">{emptyHint}</p>}
        </div>
      )}
    </section>
  );
}

export function WorkspaceShortcuts() {
  const t = useT();
  const actions = [
    { to: "/workflows/new", icon: Workflow, label: "overview.workflowAction", hint: "overview.workflowActionHint" },
    { to: "/knowledge", icon: Database, label: "overview.knowledgeAction", hint: "overview.knowledgeActionHint" },
    { to: "/settings/models", icon: BrainCircuit, label: "overview.modelsAction", hint: "overview.modelsActionHint" },
  ] as const;
  return (
    <section className="border-t border-line p-5 sm:p-6" aria-labelledby="workspace-shortcuts-title">
      <h2 id="workspace-shortcuts-title" className="workspace-eyebrow">{t("overview.quickActions")}</h2>
      <div className="mt-3 space-y-1">
        {actions.map(({ to, icon: Icon, label, hint }) => (
          <Link key={to} to={to} className="workspace-shortcut group">
            <span className="workspace-row-glyph">
              <Icon size={15} aria-hidden="true" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-xs font-medium text-ice">{t(label)}</span>
              <span className="mt-1 block text-[11px] leading-relaxed text-ghost">{t(hint)}</span>
            </span>
            <ArrowRight size={14} className="workspace-row-arrow shrink-0 text-ghost" aria-hidden="true" />
          </Link>
        ))}
      </div>
    </section>
  );
}
