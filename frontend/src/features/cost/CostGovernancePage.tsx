import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BadgeCheck,
  CheckCircle2,
  Coins,
  Download,
  FileClock,
  Gauge,
  Hash,
  Layers,
  Loader2,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  type CostAlertDTO,
  type CostGovernanceDTO,
  acknowledgeCostAlert,
  getCostGovernance,
  listCostAlerts,
} from "@/api/endpoints/costAlerts";
import {
  type UsageReconciliation,
  downloadUsageExport,
  getUsageReconciliation,
} from "@/api/endpoints/usage";
import { useAuth } from "@/features/auth/AuthProvider";
import { useI18nStore, useT, type Translate } from "@/features/i18n/i18n";
import { cn } from "@/utils/cn";

/** Mirrors `MAX_USAGE_FACT_WINDOW_DAYS` in `backend/app/schemas/usage.py`;
 * the server rejects wider windows with a 422, so the form refuses first. */
const USAGE_WINDOW_DAYS = 366;

const ALERT_KINDS = ["calls", "tokens", "cost", "concurrency"] as const;
const ALERT_SEVERITIES = ["critical", "warning"] as const;
const ALERT_STATUSES = ["open", "acknowledged"] as const;

function kindLabel(kind: string, t: Translate): string {
  return (ALERT_KINDS as readonly string[]).includes(kind)
    ? t(`cost.kind.${kind}` as Parameters<Translate>[0])
    : kind;
}

function severityLabel(severity: string, t: Translate): string {
  return (ALERT_SEVERITIES as readonly string[]).includes(severity)
    ? t(`cost.severity.${severity}` as Parameters<Translate>[0])
    : severity;
}

function statusLabel(status: string, t: Translate): string {
  return (ALERT_STATUSES as readonly string[]).includes(status)
    ? t(`cost.status.${status}` as Parameters<Translate>[0])
    : status;
}

const SEVERITY_STYLE: Record<string, string> = {
  critical: "border-bad/40 bg-bad/10 text-bad",
  warning: "border-warn/40 bg-warn/10 text-warn",
};

const STATUS_STYLE: Record<string, string> = {
  open: "border-pulse/40 bg-pulse/10 text-pulse",
  acknowledged: "border-ok/40 bg-ok/10 text-ok",
};

/** UTC day string shifted by `offsetDays` (negative = past). */
function isoDay(offsetDays: number): string {
  const day = new Date();
  day.setUTCDate(day.getUTCDate() + offsetDays);
  return day.toISOString().slice(0, 10);
}

function daysBetween(fromDay: string, toDay: string): number {
  const from = Date.parse(`${fromDay}T00:00:00Z`);
  const to = Date.parse(`${toDay}T00:00:00Z`);
  if (Number.isNaN(from) || Number.isNaN(to)) return Number.NaN;
  return Math.round((to - from) / 86_400_000);
}

function CeilingCard({
  icon,
  label,
  value,
  unit,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  unit?: string;
  hint?: string;
}) {
  return (
    <div className="glass rounded-lg border border-line p-3">
      <div className="flex items-center gap-2 text-ghost/70">
        {icon}
        <span className="font-mono text-[9px] uppercase tracking-[0.18em]">{label}</span>
      </div>
      <div className="mt-2 flex items-baseline gap-1">
        <span className="font-display text-lg font-semibold text-ice">{value}</span>
        {unit && <span className="font-mono text-[10px] text-ghost/50">{unit}</span>}
      </div>
      {hint && <p className="mt-1 text-[10px] text-ghost/50">{hint}</p>}
    </div>
  );
}

function MetricChip({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "ice" | "ok" | "warn" | "bad" | "pulse";
}) {
  const tones: Record<string, string> = {
    ice: "border-line bg-ink/60 text-ice",
    ok: "border-ok/30 bg-ok/10 text-ok",
    warn: "border-warn/30 bg-warn/10 text-warn",
    bad: "border-bad/30 bg-bad/10 text-bad",
    pulse: "border-pulse/30 bg-pulse/10 text-pulse",
  };
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border px-3 py-2",
        tones[tone],
      )}
    >
      <span className="font-display text-lg font-bold leading-tight">{value}</span>
      <span className="font-mono text-[9px] uppercase tracking-[0.18em]">{label}</span>
    </div>
  );
}

function AlertRow({
  alert,
  canEdit,
  onAck,
}: {
  alert: CostAlertDTO;
  canEdit: boolean;
  onAck: (id: string) => void;
}) {
  const t = useT();
  return (
    <div className="glass flex flex-col gap-2 rounded-lg border border-line px-4 py-3 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] font-medium text-ice">{alert.message}</span>
          <span
            className={cn(
              "rounded-xs border px-1.5 py-0.5 font-mono text-[8px] uppercase",
              SEVERITY_STYLE[alert.severity] ?? "border-line text-ghost",
            )}
          >
            {severityLabel(alert.severity, t)}
          </span>
          <span
            className={cn(
              "rounded-xs border px-1.5 py-0.5 font-mono text-[8px] uppercase",
              STATUS_STYLE[alert.status] ?? "border-line text-ghost",
            )}
          >
            {statusLabel(alert.status, t)}
          </span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[10px] text-ghost/70">
          <span className="text-volt">{kindLabel(alert.kind, t)}</span>
          <span className="text-ghost/30">/</span>
          <span>
            {t("cost.alertLimit", { limit: alert.limit_value, actual: alert.actual_value })}
          </span>
          <span className="text-ghost/30">/</span>
          <span className="truncate">{alert.id.slice(0, 12)}</span>
          {alert.created_at && (
            <span className="truncate text-ghost/50">
              {new Date(alert.created_at).toLocaleString()}
            </span>
          )}
        </div>
      </div>
      {canEdit && alert.status !== "acknowledged" && (
        <button
          type="button"
          onClick={() => onAck(alert.id)}
          className="flex h-8 shrink-0 items-center gap-1.5 rounded-md border border-ok/40 bg-ok/10 px-2.5 text-xs text-ok transition hover:brightness-110 active:scale-95"
          title={t("cost.ackTitle")}
        >
          <BadgeCheck size={13} />
          {t("cost.ack")}
        </button>
      )}
    </div>
  );
}

/** C7-1 surface: download raw metering facts and read the deterministic
 * month digest a billing system reconciles against. Platform-wide by
 * default — a scoped export is available from the project surfaces, so this
 * panel is admin-gated to avoid offering a button that can only 403. */
function UsageExportPanel({ onToast }: { onToast: (message: string) => void }) {
  const t = useT();
  const locale = useI18nStore((state) => state.locale);
  const numberFormat = useMemo(
    () => new Intl.NumberFormat(locale === "zh" ? "zh-CN" : "en-US"),
    [locale],
  );
  const dateFormat = useMemo(
    () =>
      new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-US", {
        year: "numeric",
        month: "long",
        day: "numeric",
        timeZone: "UTC",
      }),
    [locale],
  );

  const [fromDay, setFromDay] = useState(() => isoDay(-29));
  const [toDay, setToDay] = useState(() => isoDay(0));
  const [format, setFormat] = useState<"csv" | "json">("csv");
  const [month, setMonth] = useState(() => isoDay(0).slice(0, 7));
  const [exporting, setExporting] = useState(false);
  const [reconciling, setReconciling] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [reconcileError, setReconcileError] = useState<string | null>(null);
  const [reconciliation, setReconciliation] = useState<UsageReconciliation | null>(null);

  const failure = (cause: unknown, fallback: string) =>
    cause instanceof ApiError || cause instanceof Error ? cause.message : fallback;

  const exportFacts = async () => {
    setExportError(null);
    const span = daysBetween(fromDay, toDay);
    if (Number.isNaN(span) || span < 0) {
      setExportError(t("cost.usage.rangeInvalid"));
      return;
    }
    if (span + 1 > USAGE_WINDOW_DAYS) {
      setExportError(t("cost.usage.windowHint", { days: USAGE_WINDOW_DAYS }));
      return;
    }
    setExporting(true);
    try {
      const filename = await downloadUsageExport({ from_day: fromDay, to_day: toDay, format });
      onToast(t("cost.usage.exported", { filename }));
    } catch (cause) {
      setExportError(failure(cause, t("cost.usage.exportFailed")));
    } finally {
      setExporting(false);
    }
  };

  const readReconciliation = async () => {
    setReconcileError(null);
    setReconciling(true);
    try {
      setReconciliation(await getUsageReconciliation({ month }));
    } catch (cause) {
      setReconciliation(null);
      setReconcileError(failure(cause, t("cost.usage.reconcileFailed")));
    } finally {
      setReconciling(false);
    }
  };

  const totals = reconciliation?.totals;

  return (
    <section className="glass rounded-lg border border-line p-4">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <h2 className="text-sm font-semibold text-ice">{t("cost.usage.title")}</h2>
        <span className="font-mono text-[9px] uppercase tracking-[0.18em] text-ghost/50">
          {t("cost.usage.caption")}
        </span>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,7rem)_auto] sm:items-end">
        <label className="min-w-0">
          <span className="mb-1 block font-mono text-[9px] uppercase text-ghost/60">
            {t("cost.usage.from")}
          </span>
          <input
            type="date"
            value={fromDay}
            max={toDay}
            onChange={(event) => setFromDay(event.target.value)}
            className="field-input h-9 py-0 font-mono text-xs"
          />
        </label>
        <label className="min-w-0">
          <span className="mb-1 block font-mono text-[9px] uppercase text-ghost/60">
            {t("cost.usage.to")}
          </span>
          <input
            type="date"
            value={toDay}
            min={fromDay}
            onChange={(event) => setToDay(event.target.value)}
            className="field-input h-9 py-0 font-mono text-xs"
          />
        </label>
        <label className="min-w-0">
          <span className="mb-1 block font-mono text-[9px] uppercase text-ghost/60">
            {t("cost.usage.format")}
          </span>
          <select
            aria-label={t("cost.usage.format")}
            value={format}
            onChange={(event) => setFormat(event.target.value as "csv" | "json")}
            className="field-input h-9 py-0"
          >
            <option value="csv">CSV</option>
            <option value="json">JSON</option>
          </select>
        </label>
        <button
          type="button"
          onClick={() => void exportFacts()}
          disabled={exporting}
          className="flex h-9 items-center justify-center gap-1.5 rounded-md border border-line bg-ink px-3 text-xs text-ice transition hover:border-pulse/40 hover:text-pulse disabled:opacity-40"
        >
          {exporting ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
          {exporting ? t("cost.usage.exporting") : t("cost.usage.export")}
        </button>
      </div>

      <p className="mt-1.5 font-mono text-[9px] text-ghost/45">
        {t("cost.usage.windowHint", { days: USAGE_WINDOW_DAYS })}
      </p>
      {exportError && (
        <p role="alert" className="mt-1 font-mono text-[10px] text-bad">
          {exportError}
        </p>
      )}

      <div className="mt-4 border-t border-line/70 pt-3">
        <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
          <label className="min-w-0">
            <span className="mb-1 block font-mono text-[9px] uppercase text-ghost/60">
              {t("cost.usage.month")}
            </span>
            <input
              type="month"
              value={month}
              onChange={(event) => setMonth(event.target.value)}
              className="field-input h-9 py-0 font-mono text-xs"
            />
          </label>
          <button
            type="button"
            onClick={() => void readReconciliation()}
            disabled={reconciling || month.length !== 7}
            className="flex h-9 items-center justify-center gap-1.5 rounded-md border border-line bg-ink px-3 text-xs text-ice transition hover:border-pulse/40 hover:text-pulse disabled:opacity-40"
          >
            {reconciling ? <Loader2 size={13} className="animate-spin" /> : <FileClock size={13} />}
            {reconciling ? t("cost.usage.reconciling") : t("cost.usage.reconcile")}
          </button>
        </div>
        {reconcileError && (
          <p role="alert" className="mt-1 font-mono text-[10px] text-bad">
            {reconcileError}
          </p>
        )}
        {reconciliation && totals && (
          <div className="mt-3 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[9px] uppercase text-ghost/50">
                {t("cost.usage.digest")}
              </span>
              <span
                className="truncate rounded-xs border border-volt/30 bg-volt/10 px-1.5 py-0.5 font-mono text-[10px] text-volt"
                title={reconciliation.digest}
                data-testid="usage-digest"
              >
                {reconciliation.digest.slice(0, 24)}…
              </span>
              <span className="rounded-xs border border-line bg-ink/60 px-1.5 py-0.5 font-mono text-[9px] text-ghost">
                {reconciliation.scope.organization_id ??
                  reconciliation.scope.project_id ??
                  t("cost.usage.scopeAll")}
              </span>
            </div>
            <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {[
                { key: "days", label: t("cost.usage.days"), value: numberFormat.format(reconciliation.days.length) },
                { key: "executions", label: t("cost.usage.executions"), value: numberFormat.format(totals.executions) },
                { key: "tokens", label: t("cost.usage.tokens"), value: numberFormat.format(totals.total_tokens) },
                {
                  key: "cost",
                  label: t("cost.usage.cost"),
                  value: totals.estimated_cost_usd ?? "—",
                },
              ].map((entry) => (
                <div key={entry.key} className="rounded-md border border-line bg-ink/40 px-2.5 py-2">
                  <dt className="font-mono text-[9px] uppercase text-ghost/50">{entry.label}</dt>
                  <dd className="mt-0.5 truncate font-display text-sm font-semibold text-ice" title={entry.value}>
                    {entry.value}
                  </dd>
                </div>
              ))}
            </dl>
            <p className="font-mono text-[9px] text-ghost/50">
              {dateFormat.format(new Date(`${reconciliation.month}-01T00:00:00Z`))}
              {totals.estimated_cost_usd === null || totals.estimated_cost_usd === undefined
                ? ` · ${t("cost.usage.costUnknown")}`
                : ""}
              {totals.cost_unknown_executions > 0
                ? ` · ${t("cost.usage.unpriced")}: ${numberFormat.format(totals.cost_unknown_executions)}`
                : ""}
            </p>
          </div>
        )}
      </div>
    </section>
  );
}

export function CostGovernancePage() {
  const t = useT();
  const { can } = useAuth();
  const canEdit = can("editor");
  const canAdmin = can("admin");
  const [governance, setGovernance] = useState<CostGovernanceDTO | null>(null);
  const [alerts, setAlerts] = useState<CostAlertDTO[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [actingId, setActingId] = useState<string | null>(null);

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 3500);
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [gov, page] = await Promise.all([
        getCostGovernance(),
        listCostAlerts({ limit: 50, sort: "created_at", order: "desc" }),
      ]);
      setGovernance(gov);
      setAlerts(page.items);
      setNextCursor(page.next_cursor);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("cost.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const loadMore = async () => {
    if (!nextCursor) return;
    const cursor = nextCursor;
    setNextCursor(null);
    try {
      const page = await listCostAlerts({
        limit: 50,
        cursor,
        sort: "created_at",
        order: "desc",
      });
      setAlerts((current) => [...current, ...page.items]);
      setNextCursor(page.next_cursor);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("cost.loadMoreFailed"));
    }
  };

  const handleAck = async (id: string) => {
    setActingId(id);
    try {
      await acknowledgeCostAlert(id);
      setAlerts((current) =>
        current.map((a) => (a.id === id ? { ...a, status: "acknowledged" } : a)),
      );
      setGovernance((gov) =>
        gov ? { ...gov, summary: { ...gov.summary, open: Math.max(0, gov.summary.open - 1) } } : gov,
      );
      showToast(t("cost.acked"));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("cost.ackFailed"));
    } finally {
      setActingId(null);
    }
  };

  const ceilings = governance
    ? [
        {
          icon: <Hash size={13} />,
          label: t("cost.ceiling.tokens.label"),
          value: governance.max_tokens_per_execution > 0 ? String(governance.max_tokens_per_execution) : t("cost.notSet"),
          hint: t("cost.ceiling.tokens.hint"),
        },
        {
          icon: <Coins size={13} />,
          label: t("cost.ceiling.cost.label"),
          value: governance.max_cost_usd_per_execution ?? t("cost.notSet"),
          unit: "USD",
          hint: t("cost.ceiling.cost.hint"),
        },
        {
          icon: <Gauge size={13} />,
          label: t("cost.ceiling.concurrency.label"),
          value: governance.max_concurrent_per_execution > 0 ? String(governance.max_concurrent_per_execution) : t("cost.notSet"),
          hint: t("cost.ceiling.concurrency.hint"),
        },
        {
          icon: <Layers size={13} />,
          label: t("cost.ceiling.calls.label"),
          value: String(governance.max_calls_per_execution),
          hint: t("cost.ceiling.calls.hint"),
        },
      ]
    : [];

  return (
    <div className="ambient-stage flex h-full w-full flex-col text-ice">
      <header role="presentation" className="glass relative z-20 flex min-h-14 flex-wrap items-center gap-2 border-b border-line px-3 py-2 sm:px-5">
        <span className="flex h-8 w-8 items-center justify-center text-volt">
          <ShieldCheck size={18} />
        </span>
        <div className="min-w-0">
          <h1 className="workspace-page-title">
            {t("cost.title")}
          </h1>
          <p className="font-mono text-[9px] uppercase text-ghost/50">
            {t("cost.eyebrow")}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => void reload()}
            disabled={loading}
            className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-pulse disabled:opacity-40"
            title={t("cost.refresh")}
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : undefined} />
          </button>
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
            {t("cost.dismiss")}
          </button>
        </div>
      )}

      {toast && (
        <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-md border border-ok/40 bg-ok/15 px-4 py-2 text-xs text-ok shadow-card animate-fade-up">
          {toast}
        </div>
      )}

      <main className="flex-1 overflow-auto p-4 sm:p-6">
        <div className="mx-auto max-w-6xl space-y-5">
          {loading && !governance ? (
            <div className="flex items-center gap-2 text-ghost/60">
              <Loader2 size={14} className="animate-spin" />
              <span className="text-xs">{t("cost.loading")}</span>
            </div>
          ) : (
            <>
              <section>
                <h2 className="mb-3 font-mono text-[10px] uppercase tracking-[0.25em] text-ghost/60">
                  {t("cost.ceilingsTitle")}
                </h2>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {ceilings.map((ceiling) => (
                    <CeilingCard key={ceiling.label} {...ceiling} />
                  ))}
                </div>
              </section>

              {governance && (
                <section>
                  <h2 className="mb-3 font-mono text-[10px] uppercase tracking-[0.25em] text-ghost/60">
                    {t("cost.alertSummary")}
                  </h2>
                  <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
                    <MetricChip label={t("cost.summary.total")} value={governance.summary.total} tone="ice" />
                    <MetricChip label={t("cost.status.open")} value={governance.summary.open} tone="pulse" />
                    <MetricChip label={t("cost.severity.critical")} value={governance.summary.critical} tone="bad" />
                    <MetricChip label={t("cost.severity.warning")} value={governance.summary.warning} tone="warn" />
                    <MetricChip
                      label={t("cost.status.acknowledged")}
                      value={governance.summary.acknowledged}
                      tone="ok"
                    />
                  </div>
                  {Object.keys(governance.summary.open_by_kind).length > 0 && (
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      {Object.entries(governance.summary.open_by_kind).map(([kind, count]) => (
                        <span
                          key={kind}
                          className="rounded-xs border border-line bg-ink/60 px-2 py-0.5 font-mono text-[9px] uppercase text-ghost"
                        >
                          {kindLabel(kind, t)} × {count}
                        </span>
                      ))}
                    </div>
                  )}
                </section>
              )}

              {canAdmin && <UsageExportPanel onToast={showToast} />}

              <section>
                <h2 className="mb-3 flex items-center justify-between font-mono text-[10px] uppercase tracking-[0.25em] text-ghost/60">
                  <span>{t("cost.alerts", { count: alerts.length })}</span>
                  {alerts.length > 0 && (
                    <span className="flex items-center gap-1 text-ok/80">
                      <CheckCircle2 size={11} />
                      {t("cost.persisted")}
                    </span>
                  )}
                </h2>
                <div className="space-y-2">
                  {alerts.map((alert) => (
                    <AlertRow
                      key={alert.id}
                      alert={alert}
                      canEdit={canEdit}
                      onAck={(id) => void handleAck(id)}
                    />
                  ))}
                  {alerts.length === 0 && (
                    <p className="rounded-lg border border-line bg-ink/40 px-4 py-6 text-center text-xs text-ghost/50">
                      {t("cost.empty")}
                    </p>
                  )}
                </div>
                {nextCursor && (
                  <div className="mt-3 text-center">
                    <button
                      type="button"
                      onClick={() => void loadMore()}
                      disabled={actingId !== null}
                      className="flex h-8 items-center gap-1.5 rounded-md border border-line bg-ink/80 px-3 text-xs text-ice transition hover:border-ghost/50 hover:bg-line/60"
                    >
                      {nextCursor && actingId === null ? t("cost.loadMore") : t("cost.loading")}
                    </button>
                  </div>
                )}
              </section>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
