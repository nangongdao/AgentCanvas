import { useEffect, useMemo, useState } from "react";
import { Bug, CheckCircle2, Loader2, SkipForward } from "lucide-react";

import type { DebugRunOptions } from "@/api/endpoints/workflows";
import { cn } from "@/utils/cn";

interface Props {
  title: string;
  instruction: string;
  nodeId: string | null;
  nodeOutputs: Record<string, unknown> | null;
  debugOptions: DebugRunOptions | null;
  resuming: boolean;
  onContinue: (
    decision: Record<string, unknown>,
    debug: DebugRunOptions | null,
  ) => void;
}

/**
 * Debug breakpoint resume panel (C2-7).
 *
 * Surfaces the interrupted node's intermediate state (node_outputs snapshot)
 * as an editable JSON patch. The editor can rewrite any node's output before
 * continuing — the patch is sent as ``state_patch`` in the resume decision.
 *
 * LangGraph requires a non-empty resume value to clear an interrupt; an empty
 * object does not resume, so the decision always carries ``resume: true``.
 */
export function DebugResumePanel({
  title,
  instruction,
  nodeId,
  nodeOutputs,
  debugOptions,
  resuming,
  onContinue,
}: Props) {
  const snapshot = useMemo(
    () => (nodeOutputs && Object.keys(nodeOutputs).length > 0
      ? nodeOutputs
      : {}),
    [nodeOutputs],
  );
  const [draft, setDraft] = useState("{}");
  const [error, setError] = useState<string | null>(null);
  const [usePatch, setUsePatch] = useState(false);

  useEffect(() => {
    setDraft(snapshot && Object.keys(snapshot).length > 0
      ? JSON.stringify(snapshot, null, 2)
      : "{}");
    setError(null);
    setUsePatch(false);
  }, [snapshot, nodeId]);

  const validate = (): Record<string, unknown> | null => {
    try {
      const parsed = JSON.parse(draft) as unknown;
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error("state_patch 必须是 JSON 对象");
      }
      return parsed as Record<string, unknown>;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return null;
    }
  };

  const continueRun = () => {
    const patch = usePatch ? validate() : null;
    if (usePatch && patch === null) return;
    setError(null);
    const decision: Record<string, unknown> = { resume: true };
    if (usePatch && patch && Object.keys(patch).length > 0) {
      decision.state_patch = patch;
    }
    onContinue(decision, debugOptions);
  };

  return (
    <div className="animate-fade-up rounded-lg border border-volt/40 bg-volt/5 px-3 py-3">
      <div className="mb-2 flex items-center gap-2">
        <Bug size={13} className="text-volt" />
        <span className="font-mono text-[10px] uppercase tracking-widest text-volt">
          断点暂停 · {nodeId ?? "node"}
        </span>
      </div>
      <p className="mb-1 text-[12px] font-medium text-ice">{title}</p>
      <p className="mb-3 whitespace-pre-wrap text-[11.5px] leading-5 text-ghost/80">
        {instruction}
      </p>

      <div className="mb-2 flex items-center gap-2">
        <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-ice">
          <input
            type="checkbox"
            className="h-3.5 w-3.5 accent-volt"
            checked={usePatch}
            onChange={() => setUsePatch((v) => !v)}
          />
          编辑中间状态后继续
        </label>
        {usePatch && (
          <button
            type="button"
            onClick={() =>
              setDraft(
                snapshot && Object.keys(snapshot).length > 0
                  ? JSON.stringify(snapshot, null, 2)
                  : "{}",
              )
            }
            className="font-mono text-[9px] uppercase text-ghost/50 hover:text-ice"
          >
            重置为快照
          </button>
        )}
      </div>

      {usePatch && (
        <textarea
          className={cn(
            "mb-3 min-h-36 w-full resize-y rounded-md border bg-void/70 px-3 py-2 font-mono text-[10px] leading-5 text-ice outline-hidden",
            error ? "border-bad/60" : "border-line focus:border-volt/60",
          )}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          spellCheck={false}
        />
      )}
      {usePatch && error && (
        <p className="mb-2 text-[10px] text-bad">{error}</p>
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={resuming}
          onClick={continueRun}
          className="flex items-center gap-1.5 rounded-md border border-volt/50 bg-volt/15 px-3 py-1.5 text-[11px] font-medium text-volt transition hover:bg-volt/25 disabled:opacity-50"
        >
          {resuming ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <SkipForward size={13} />
          )}
          {usePatch ? "应用补丁并继续" : "继续运行"}
        </button>
        {!usePatch && (
          <span className="font-mono text-[10px] text-ghost/50">
            <CheckCircle2 size={11} className="mr-1 inline" />
            使用原始中间状态继续
          </span>
        )}
        {resuming && (
          <span className="ml-1 self-center font-mono text-[10px] text-ghost/60">
            resuming…
          </span>
        )}
      </div>
    </div>
  );
}
