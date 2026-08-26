import { useCallback, useEffect, useState } from "react";
import { Activity } from "lucide-react";

import {
  getResilience,
  type ResilienceResource,
  type ResilienceSnapshot,
} from "@/api/endpoints/adminConsole";

function errorRate(resource: ResilienceResource): number | null {
  const total = resource.total_failures + resource.total_successes;
  if (total === 0) return null;
  return resource.total_failures / total;
}

const STATE_STYLES: Record<string, string> = {
  closed: "rounded bg-ok/10 px-1.5 py-0.5 text-[10px] text-ok",
  open: "rounded bg-bad/10 px-1.5 py-0.5 text-[10px] text-bad",
  half_open: "rounded bg-warn/10 px-1.5 py-0.5 text-[10px] text-warn",
};

export function AdminProvidersSection() {
  const [snapshot, setSnapshot] = useState<ResilienceSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSnapshot(await getResilience());
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载 Provider 健康失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const resources = snapshot?.resources ?? [];

  return (
    <section
      aria-label="Provider 健康"
      className="glass rounded-lg border border-line p-4"
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Activity size={15} className="text-warn" />
        <h2 className="font-display text-sm font-semibold">Provider / MCP 韧性</h2>
        {snapshot && (
          <span className="ml-auto font-mono text-[9px] uppercase text-ghost">
            {snapshot.summary.total} resources · {snapshot.summary.open} open ·{" "}
            {snapshot.summary.half_open} half-open
          </span>
        )}
      </div>
      {error && (
        <p role="alert" className="mb-2 font-mono text-[10px] text-bad">
          {error}
        </p>
      )}
      {loading ? (
        <p className="font-mono text-[10px] uppercase text-ghost">loading…</p>
      ) : resources.length === 0 ? (
        <p className="font-mono text-[10px] text-ghost">
          暂无熔断资源(未配置 resilience 注册项)
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-xs">
            <thead>
              <tr className="font-mono text-[9px] uppercase text-ghost">
                <th className="py-1 pr-3">资源</th>
                <th className="py-1 pr-3">状态</th>
                <th className="py-1 pr-3">错误率</th>
                <th className="py-1 pr-3">连续失败</th>
                <th className="py-1">重试预算</th>
              </tr>
            </thead>
            <tbody>
              {resources.map((resource) => {
                const rate = errorRate(resource);
                return (
                  <tr key={resource.key} className="border-t border-line/60">
                    <td className="py-1.5 pr-3 font-mono text-[10px] text-ghost">
                      {resource.key}
                    </td>
                    <td className="py-1.5 pr-3">
                      <span className={STATE_STYLES[resource.state] ?? STATE_STYLES.closed}>
                        {resource.state}
                      </span>
                    </td>
                    <td className="py-1.5 pr-3">
                      {rate === null ? "—" : `${(rate * 100).toFixed(1)}%`}
                    </td>
                    <td className="py-1.5 pr-3">{resource.consecutive_failures}</td>
                    <td className="py-1.5 font-mono text-[10px]">
                      {resource.retry_budget_used}/{resource.retry_budget_limit}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
