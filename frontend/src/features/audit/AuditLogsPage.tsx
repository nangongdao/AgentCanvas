import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  Filter,
  Loader2,
  RefreshCw,
  RotateCcw,
  ScrollText,
  Search,
  TriangleAlert,
} from "lucide-react";
import { Navigate } from "react-router-dom";

import {
  type AuditLogDTO,
  type AuditLogQuery,
  listAuditLogs,
} from "@/api/endpoints/auditLogs";
import { AuditLogRow, ACTION_LABEL } from "@/features/audit/AuditLogRow";
import { useAuth } from "@/features/auth/AuthProvider";

interface Filters {
  search: string;
  action: string;
  resourceType: string;
  actorKey: string;
  organizationId: string;
  projectId: string;
}

const EMPTY_FILTERS: Filters = {
  search: "",
  action: "",
  resourceType: "",
  actorKey: "",
  organizationId: "",
  projectId: "",
};

const RESOURCE_TYPES = [
  "organization",
  "project",
  "project_quota",
  "membership",
  "model",
  "mcp_server",
  "mcp_catalog",
  "mcp_catalog_version",
  "service_account",
  "api_token",
  "workflow",
  "workflow_version",
  "workflow_review",
  "execution",
];

function queryFor(filters: Filters, cursor?: string): AuditLogQuery {
  return {
    limit: 50,
    sort: "created_at",
    order: "desc",
    cursor,
    search: filters.search.trim() || undefined,
    action: filters.action || undefined,
    resource_type: filters.resourceType || undefined,
    actor_key: filters.actorKey.trim() || undefined,
    organization_id: filters.organizationId.trim() || undefined,
    project_id: filters.projectId.trim() || undefined,
  };
}

export function AuditLogsPage() {
  const { ready, can } = useAuth();
  const canAdmin = can("admin");
  const [draft, setDraft] = useState<Filters>(EMPTY_FILTERS);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [entries, setEntries] = useState<AuditLogDTO[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!canAdmin) return;
    setLoading(true);
    setError(null);
    try {
      const page = await listAuditLogs(queryFor(filters));
      setEntries(page.items);
      setNextCursor(page.next_cursor);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载审计日志失败");
    } finally {
      setLoading(false);
    }
  }, [canAdmin, filters]);

  useEffect(() => {
    if (ready) void reload();
  }, [ready, reload]);

  if (ready && !canAdmin) return <Navigate to="/" replace />;

  const applyFilters = (event: FormEvent) => {
    event.preventDefault();
    setFilters(draft);
  };

  const resetFilters = () => {
    setDraft(EMPTY_FILTERS);
    setFilters(EMPTY_FILTERS);
  };

  const loadMore = async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await listAuditLogs(queryFor(filters, nextCursor));
      setEntries((current) => [...current, ...page.items]);
      setNextCursor(page.next_cursor);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载更多审计日志失败");
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <div className="ambient-stage flex h-full w-full flex-col text-ice">
      <header role="presentation" className="glass relative z-20 flex min-h-14 flex-wrap items-center gap-2 border-b border-line px-3 py-2 sm:px-5">
        <span className="flex h-8 w-8 items-center justify-center text-warn">
          <ScrollText size={18} />
        </span>
        <div className="min-w-0">
          <h1 className="workspace-page-title">
            AgentCanvas Audit Log
          </h1>
          <p className="font-mono text-[9px] uppercase text-ghost/50">
            actor / action / resource / scope
          </p>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => void reload()}
            disabled={loading || !canAdmin}
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

      <main className="relative z-1 flex min-h-0 flex-1 flex-col overflow-hidden">
        <form
          onSubmit={applyFilters}
          className="grid shrink-0 gap-2 border-b border-line bg-ink/45 p-3 sm:grid-cols-2 lg:grid-cols-[minmax(12rem,1.5fr)_repeat(5,minmax(8rem,1fr))_auto] lg:px-5"
        >
          <label className="relative min-w-0">
            <span className="sr-only">搜索审计日志</span>
            <Search
              size={13}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ghost/55"
            />
            <input
              value={draft.search}
              onChange={(event) => setDraft({ ...draft, search: event.target.value })}
              placeholder="主体、动作、资源"
              className="field-input h-8 py-0 pl-8"
            />
          </label>
          <select
            aria-label="动作筛选"
            value={draft.action}
            onChange={(event) => setDraft({ ...draft, action: event.target.value })}
            className="field-input h-8 py-0"
          >
            <option value="">全部动作</option>
            {Object.entries(ACTION_LABEL).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          <select
            aria-label="资源类型筛选"
            value={draft.resourceType}
            onChange={(event) => setDraft({ ...draft, resourceType: event.target.value })}
            className="field-input h-8 py-0"
          >
            <option value="">全部资源</option>
            {RESOURCE_TYPES.map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </select>
          <input
            aria-label="主体标识筛选"
            value={draft.actorKey}
            onChange={(event) => setDraft({ ...draft, actorKey: event.target.value })}
            placeholder="actor key"
            className="field-input h-8 py-0"
          />
          <input
            aria-label="组织标识筛选"
            value={draft.organizationId}
            onChange={(event) => setDraft({ ...draft, organizationId: event.target.value })}
            placeholder="organization id"
            className="field-input h-8 py-0"
          />
          <input
            aria-label="项目标识筛选"
            value={draft.projectId}
            onChange={(event) => setDraft({ ...draft, projectId: event.target.value })}
            placeholder="project id"
            className="field-input h-8 py-0"
          />
          <div className="flex justify-end gap-1">
            <button
              type="button"
              onClick={resetFilters}
              className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice"
              title="清除筛选"
            >
              <RotateCcw size={13} />
            </button>
            <button
              type="submit"
              className="flex h-8 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void transition hover:brightness-110"
            >
              <Filter size={13} />
              筛选
            </button>
          </div>
        </form>

        <div className="hidden shrink-0 grid-cols-[10.5rem_minmax(10rem,1fr)_minmax(12rem,1.35fr)_minmax(14rem,1.7fr)] border-b border-line bg-void/70 px-4 py-2 font-mono text-[9px] uppercase text-ghost/50 lg:grid">
          <span>time / event</span>
          <span>actor</span>
          <span>action / resource</span>
          <span>scope / metadata</span>
        </div>

        <section aria-label="审计事件" tabIndex={0} className="min-h-0 flex-1 overflow-auto">
          {loading && entries.length === 0 ? (
            <div className="flex h-32 items-center justify-center gap-2 text-ghost/60">
              <Loader2 size={14} className="animate-spin" />
              <span className="text-xs">加载审计日志…</span>
            </div>
          ) : entries.length === 0 ? (
            <div className="flex h-40 flex-col items-center justify-center gap-2 text-center text-ghost/50">
              <ScrollText size={22} />
              <p className="text-xs">当前筛选条件下没有审计事件</p>
            </div>
          ) : (
            <div className="mx-auto max-w-[110rem]">
              {entries.map((entry) => <AuditLogRow key={entry.id} entry={entry} />)}
              {nextCursor && (
                <div className="flex justify-center p-4">
                  <button
                    type="button"
                    onClick={() => void loadMore()}
                    disabled={loadingMore}
                    className="flex h-8 items-center gap-1.5 rounded-md border border-line bg-ink/80 px-3 text-xs text-ice transition hover:border-ghost/50 hover:bg-line/60 disabled:opacity-40"
                  >
                    {loadingMore && <Loader2 size={13} className="animate-spin" />}
                    {loadingMore ? "加载中…" : "加载更多"}
                  </button>
                </div>
              )}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
