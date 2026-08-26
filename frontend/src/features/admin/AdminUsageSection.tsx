import { useCallback, useEffect, useMemo, useState } from "react";
import { ChartNoAxesCombined } from "lucide-react";

import { getUsageDaily } from "@/api/endpoints/adminConsole";

interface OrgUsage {
  organizationId: string;
  executions: number;
  totalTokens: number;
  costKnown: boolean;
  costMicroUsd: number;
}

function toIsoDay(offsetDays: number): string {
  const day = new Date();
  day.setUTCDate(day.getUTCDate() - offsetDays);
  return day.toISOString().slice(0, 10);
}

export function AdminUsageSection() {
  const [rows, setRows] = useState<OrgUsage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await getUsageDaily(toIsoDay(6), toIsoDay(0));
      const byOrg = new Map<string, OrgUsage>();
      for (const fact of payload.facts) {
        const entry = byOrg.get(fact.organization_id) ?? {
          organizationId: fact.organization_id,
          executions: 0,
          totalTokens: 0,
          costKnown: true,
          costMicroUsd: 0,
        };
        entry.executions += fact.executions;
        entry.totalTokens += fact.total_tokens;
        if (fact.estimated_cost_usd === null) {
          if (fact.executions > 0 || fact.cost_unknown_executions > 0) {
            entry.costKnown = false;
          }
        } else {
          entry.costMicroUsd += Math.round(
            parseFloat(fact.estimated_cost_usd) * 1_000_000,
          );
        }
        byOrg.set(fact.organization_id, entry);
      }
      setRows(
        [...byOrg.values()].sort((a, b) => b.executions - a.executions),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载跨租户用量失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const totals = useMemo(
    () => ({
      executions: rows.reduce((sum, row) => sum + row.executions, 0),
      tokens: rows.reduce((sum, row) => sum + row.totalTokens, 0),
    }),
    [rows],
  );

  return (
    <section
      aria-label="跨租户用量"
      className="glass rounded-lg border border-line p-4"
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <ChartNoAxesCombined size={15} className="text-ok" />
        <h2 className="font-display text-sm font-semibold">跨租户用量(近 7 天)</h2>
        <span className="ml-auto font-mono text-[9px] uppercase text-ghost">
          {totals.executions} executions · {totals.tokens.toLocaleString()} tokens
        </span>
      </div>
      {error && (
        <p role="alert" className="mb-2 font-mono text-[10px] text-bad">
          {error}
        </p>
      )}
      {loading ? (
        <p className="font-mono text-[10px] uppercase text-ghost">loading…</p>
      ) : rows.length === 0 ? (
        <p className="font-mono text-[10px] text-ghost">
          尚无用量事实(聚合 job 按天生成,或暂无终态执行)
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-xs">
            <thead>
              <tr className="font-mono text-[9px] uppercase text-ghost">
                <th className="py-1 pr-3">组织</th>
                <th className="py-1 pr-3">执行数</th>
                <th className="py-1 pr-3">Tokens</th>
                <th className="py-1">预估费用(USD)</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.organizationId} className="border-t border-line/60">
                  <td className="py-1.5 pr-3 font-mono text-[10px] text-ghost">
                    {row.organizationId}
                  </td>
                  <td className="py-1.5 pr-3">{row.executions}</td>
                  <td className="py-1.5 pr-3">{row.totalTokens.toLocaleString()}</td>
                  <td className="py-1.5">
                    {row.costKnown
                      ? `$${(row.costMicroUsd / 1_000_000).toFixed(6)}`
                      : "未知(含缺失用量/费率)"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
