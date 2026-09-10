import { useCallback, useEffect, useRef, useState } from "react";
import {
  BadgeCheck,
  CheckCircle2,
  Coins,
  Gauge,
  Hash,
  Layers,
  Loader2,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";

import {
  type CostAlertDTO,
  type CostGovernanceDTO,
  acknowledgeCostAlert,
  getCostGovernance,
  listCostAlerts,
} from "@/api/endpoints/costAlerts";
import { useAuth } from "@/features/auth/AuthProvider";
import { cn } from "@/utils/cn";

const KIND_LABEL: Record<string, string> = {
  calls: "调用次数",
  tokens: "Token",
  cost: "费用",
  concurrency: "并发",
};

const SEVERITY_STYLE: Record<string, string> = {
  critical: "border-bad/40 bg-bad/10 text-bad",
  warning: "border-warn/40 bg-warn/10 text-warn",
};

const STATUS_STYLE: Record<string, string> = {
  open: "border-pulse/40 bg-pulse/10 text-pulse",
  acknowledged: "border-ok/40 bg-ok/10 text-ok",
};

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
            {alert.severity}
          </span>
          <span
            className={cn(
              "rounded-xs border px-1.5 py-0.5 font-mono text-[8px] uppercase",
              STATUS_STYLE[alert.status] ?? "border-line text-ghost",
            )}
          >
            {alert.status}
          </span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[10px] text-ghost/70">
          <span className="text-volt">{KIND_LABEL[alert.kind] ?? alert.kind}</span>
          <span className="text-ghost/30">/</span>
          <span>
            {alert.limit_value} 上限 / 实际 {alert.actual_value}
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
          title="标记为已确认"
        >
          <BadgeCheck size={13} />
          确认
        </button>
      )}
    </div>
  );
}

export function CostGovernancePage() {
  const { can } = useAuth();
  const canEdit = can("editor");
  const [governance, setGovernance] = useState<CostGovernanceDTO | null>(null);
  const [alerts, setAlerts] = useState<CostAlertDTO[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [actingId, setActingId] = useState<string | null>(null);
  const cursorRef = useRef<string | null>(null);

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
      cursorRef.current = null;
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载成本治理数据失败");
    } finally {
      setLoading(false);
    }
  }, []);

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
      cursorRef.current = cursor;
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载更多失败");
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
      showToast("已确认告警");
    } catch (err) {
      setError(err instanceof Error ? err.message : "确认失败");
    } finally {
      setActingId(null);
    }
  };

  const ceilings = governance
    ? [
        {
          icon: <Hash size={13} />,
          label: "token / 执行",
          value: governance.max_tokens_per_execution > 0 ? String(governance.max_tokens_per_execution) : "未设置",
          hint: "0 表示不限量",
        },
        {
          icon: <Coins size={13} />,
          label: "费用上限 / 执行",
          value: governance.max_cost_usd_per_execution ?? "未设置",
          unit: "USD",
          hint: "达到后立即终止执行",
        },
        {
          icon: <Gauge size={13} />,
          label: "并发 / 执行",
          value: governance.max_concurrent_per_execution > 0 ? String(governance.max_concurrent_per_execution) : "未设置",
          hint: "单次执行内同时的模型调用数",
        },
        {
          icon: <Layers size={13} />,
          label: "调用次数 / 执行",
          value: String(governance.max_calls_per_execution),
          hint: "模型调用预算",
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
            AgentCanvas Cost Governance
          </h1>
          <p className="font-mono text-[9px] uppercase text-ghost/50">
            budgets / ceilings / alerts
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

      <main className="flex-1 overflow-auto p-4 sm:p-6">
        <div className="mx-auto max-w-6xl space-y-5">
          {loading && !governance ? (
            <div className="flex items-center gap-2 text-ghost/60">
              <Loader2 size={14} className="animate-spin" />
              <span className="text-xs">加载中…</span>
            </div>
          ) : (
            <>
              <section>
                <h2 className="mb-3 font-mono text-[10px] uppercase tracking-[0.25em] text-ghost/60">
                  Per-Execution Ceilings
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
                    Alert Summary
                  </h2>
                  <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
                    <MetricChip label="total" value={governance.summary.total} tone="ice" />
                    <MetricChip label="open" value={governance.summary.open} tone="pulse" />
                    <MetricChip label="critical" value={governance.summary.critical} tone="bad" />
                    <MetricChip label="warning" value={governance.summary.warning} tone="warn" />
                    <MetricChip
                      label="acknowledged"
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
                          {KIND_LABEL[kind] ?? kind} × {count}
                        </span>
                      ))}
                    </div>
                  )}
                </section>
              )}

              <section>
                <h2 className="mb-3 flex items-center justify-between font-mono text-[10px] uppercase tracking-[0.25em] text-ghost/60">
                  <span>Alerts / {alerts.length}</span>
                  {alerts.length > 0 && (
                    <span className="flex items-center gap-1 text-ok/80">
                      <CheckCircle2 size={11} />
                      已持久化到数据库
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
                      暂无成本告警。当执行超出配置的 token / 费用 / 并发 / 调用上限时，这里会生成持久化告警。
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
                      {nextCursor && actingId === null ? "加载更多" : "加载中…"}
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
