import { Activity, CircleAlert, CircleCheck, LoaderCircle } from "lucide-react";

import type { ResilienceSnapshotDTO } from "@/api/endpoints/meta";
import { cn } from "@/utils/cn";

const STATE_LABELS: Record<ResilienceSnapshotDTO["state"], string> = {
  closed: "healthy",
  open: "circuit open",
  half_open: "probing",
};

export function ProviderRuntimeHealth({ resources }: { resources: ResilienceSnapshotDTO[] }) {
  const providers = resources.filter((resource) => resource.key.startsWith("provider:"));
  return (
    <section className="mb-5 border-y border-line/70 py-3" aria-label="Provider 运行健康">
      <div className="mb-2 flex items-center gap-2">
        <Activity size={13} className="text-pulse" />
        <h2 className="font-mono text-[10px] uppercase tracking-[0.25em] text-ghost/70">
          Provider Runtime Health
        </h2>
        <span className="ml-auto font-mono text-[9px] text-ghost/45">
          {providers.length} observed
        </span>
      </div>
      {providers.length === 0 ? (
        <p className="font-mono text-[10px] text-ghost/50">尚无已观测的 Provider 调用</p>
      ) : (
        <div className="grid gap-1.5">
          {providers.map((resource) => {
            const [, provider, ...modelParts] = resource.key.split(":");
            const state = resource.state;
            return (
              <div
                key={resource.key}
                className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-line/40 py-1.5 last:border-0"
              >
                {state === "closed" ? (
                  <CircleCheck size={12} className="shrink-0 text-ok" />
                ) : state === "open" ? (
                  <CircleAlert size={12} className="shrink-0 text-bad" />
                ) : (
                  <LoaderCircle size={12} className="shrink-0 animate-spin text-warn" />
                )}
                <span className="font-mono text-[10px] text-ice">{provider}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-ghost/65">
                  {modelParts.join(":")}
                </span>
                <span
                  className={cn(
                    "font-mono text-[9px] uppercase",
                    state === "closed"
                      ? "text-ok"
                      : state === "open"
                        ? "text-bad"
                        : "text-warn",
                  )}
                >
                  {STATE_LABELS[state]}
                </span>
                <span className="font-mono text-[9px] text-ghost/45">
                  {resource.total_failures} failed · {resource.retry_budget_used}/
                  {resource.retry_budget_limit} retries
                </span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
