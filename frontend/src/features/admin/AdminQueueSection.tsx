import { useCallback, useEffect, useState } from "react";
import { ListRestart } from "lucide-react";

import {
  getAdminQueue,
  replayDeadLetter,
  type AdminQueue,
} from "@/api/endpoints/adminConsole";

const DEPTH_ORDER = [
  "queued",
  "leased",
  "retry_wait",
  "dead_letter",
  "done",
] as const;

const DEPTH_LABELS: Record<string, string> = {
  queued: "排队",
  leased: "租约中",
  retry_wait: "重试等待",
  dead_letter: "死信",
  done: "完成",
};

export function AdminQueueSection() {
  const [queue, setQueue] = useState<AdminQueue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setQueue(await getAdminQueue());
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载队列失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const replay = async (itemId: string) => {
    setBusy(itemId);
    setError(null);
    setNotice(null);
    try {
      const item = await replayDeadLetter(itemId);
      setNotice(`已重放 ${item.execution_id}(attempt 重置为 0)`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "重放死信失败");
    } finally {
      setBusy(null);
    }
  };

  const depthEntries = queue
    ? [
        ...DEPTH_ORDER.filter((status) => (queue.depth[status] ?? 0) > 0).map(
          (status) => [status, queue.depth[status]] as const,
        ),
        ...Object.entries(queue.depth).filter(
          ([status]) => !DEPTH_ORDER.includes(status as (typeof DEPTH_ORDER)[number]),
        ),
      ]
    : [];

  return (
    <section
      aria-label="执行队列"
      className="glass rounded-lg border border-line p-4"
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <ListRestart size={15} className="text-ok" />
        <h2 className="font-display text-sm font-semibold">执行队列与死信</h2>
        <button
          type="button"
          onClick={() => void load()}
          className="ml-auto rounded border border-line px-2 py-0.5 text-[10px] text-ghost transition hover:bg-line hover:text-ice"
        >
          刷新
        </button>
      </div>
      {error && (
        <p role="alert" className="mb-2 font-mono text-[10px] text-bad">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="mb-2 font-mono text-[10px] text-ok">
          {notice}
        </p>
      )}
      {loading ? (
        <p className="font-mono text-[10px] uppercase text-ghost">loading…</p>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap gap-2">
            {depthEntries.length === 0 ? (
              <span className="font-mono text-[10px] text-ghost">队列为空</span>
            ) : (
              depthEntries.map(([status, count]) => (
                <span
                  key={status}
                  className={
                    status === "dead_letter"
                      ? "rounded bg-bad/10 px-2 py-1 font-mono text-[10px] text-bad"
                      : "rounded bg-line/60 px-2 py-1 font-mono text-[10px] text-ghost"
                  }
                >
                  {DEPTH_LABELS[status] ?? status}: {count}
                </span>
              ))
            )}
          </div>
          {queue && queue.dead_letters.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-xs">
                <thead>
                  <tr className="font-mono text-[9px] uppercase text-ghost">
                    <th className="py-1 pr-3">执行</th>
                    <th className="py-1 pr-3">类型</th>
                    <th className="py-1 pr-3">尝试</th>
                    <th className="py-1 pr-3">最后错误</th>
                    <th className="py-1">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {queue.dead_letters.map((item) => (
                    <tr key={item.id} className="border-t border-line/60">
                      <td className="py-1.5 pr-3 font-mono text-[10px] text-ghost">
                        {item.execution_id}
                      </td>
                      <td className="py-1.5 pr-3">{item.kind}</td>
                      <td className="py-1.5 pr-3">{item.attempt}</td>
                      <td
                        className="max-w-[260px] truncate py-1.5 pr-3 font-mono text-[10px] text-bad"
                        title={item.last_error ?? ""}
                      >
                        {item.last_error ?? "—"}
                      </td>
                      <td className="py-1.5">
                        <button
                          type="button"
                          disabled={busy === item.id}
                          onClick={() => void replay(item.id)}
                          className="rounded border border-line px-2 py-0.5 text-[10px] text-ghost transition hover:bg-line hover:text-ice disabled:opacity-50"
                        >
                          重放
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}
