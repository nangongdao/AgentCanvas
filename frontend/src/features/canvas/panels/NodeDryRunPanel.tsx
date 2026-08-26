import { useEffect, useMemo, useState } from "react";
import { FlaskConical, Loader2, Play, TriangleAlert } from "lucide-react";

import { ApiError } from "@/api/client";
import { dryRunNode, type NodeDryRunResponse } from "@/api/endpoints/workflows";
import { useWorkflowStore } from "@/stores/workflowStore";
import type { NodeType } from "@/types/dsl";
import { cn } from "@/utils/cn";

// Node types that the backend rejects for isolated single-node dry-run.
const UNSUPPORTED_TYPES = new Set<NodeType>(["start", "end", "subworkflow", "iteration"]);

type MockField = {
  name: string;
  type: "string" | "number" | "boolean" | "object";
  required: boolean;
};

interface Props {
  /** Persist the current canvas (silent) and resolve to a workflow id, if any. */
  ensureWorkflowId: () => Promise<string | null>;
}

function messageFrom(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string"
      ? error.detail
      : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

function inferFields(nodeType: NodeType, config: Record<string, unknown>): MockField[] {
  // The start node's input_schema lists declared workflow inputs; for a
  // single-node dry-run we let the editor mock the same shape so templates
  // like {{input.x}} resolve. For nodes without an obvious schema we expose a
  // single free-form JSON object so any value can be supplied.
  if (nodeType === "start") return [];
  const declared = config["inputs"];
  if (Array.isArray(declared)) {
    const fields: MockField[] = [];
    for (const entry of declared) {
      if (typeof entry !== "object" || entry === null) continue;
      const row = entry as Record<string, unknown>;
      const name = typeof row.name === "string" ? row.name : "";
      const type = row.type;
      if (!name || typeof type !== "string") continue;
      if (!["string", "number", "boolean", "object"].includes(type)) continue;
      fields.push({
        name,
        type: type as MockField["type"],
        required: row.required !== false,
      });
    }
    if (fields.length > 0) return fields;
  }
  return [{ name: "payload", type: "object", required: false }];
}

function emptyValue(field: MockField): unknown {
  if (field.type === "boolean") return false;
  if (field.type === "object") return {};
  if (field.type === "number") return 0;
  return "";
}

function NodeDryRunPanel({ ensureWorkflowId }: Props) {
  const selectedNodeId = useWorkflowStore((state) => state.selectedNodeId);
  const nodeData = useWorkflowStore(
    (state) => state.nodes.find((candidate) => candidate.id === selectedNodeId)?.data,
  );

  const supported =
    !!nodeData && !UNSUPPORTED_TYPES.has(nodeData.nodeType);
  const fields = useMemo<MockField[]>(
    () => (nodeData ? inferFields(nodeData.nodeType, nodeData.config) : []),
    [nodeData?.nodeType, nodeData?.config],
  );

  const [values, setValues] = useState<Record<string, unknown>>({});
  const [objectDrafts, setObjectDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<NodeDryRunResponse | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!supported) {
      setValues({});
      setObjectDrafts({});
      setError(null);
      setResult(null);
      return;
    }
    const next = Object.fromEntries(fields.map((field) => [field.name, emptyValue(field)]));
    setValues(next);
    setObjectDrafts(
      Object.fromEntries(
        fields
          .filter((field) => field.type === "object")
          .map((field) => [field.name, JSON.stringify(next[field.name] ?? {}, null, 2)]),
      ),
    );
    setError(null);
    setResult(null);
  }, [fields, supported, selectedNodeId]);

  if (!nodeData || !selectedNodeId || !supported) return null;

  const run = async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const workflowId = await ensureWorkflowId();
      if (!workflowId) {
        setError("无法获取工作流,请先保存");
        return;
      }
      const inputs: Record<string, unknown> = {};
      for (const field of fields) {
        if (field.type === "object") {
          inputs[field.name] = JSON.parse(objectDrafts[field.name] ?? "{}");
        } else {
          inputs[field.name] = values[field.name];
        }
      }
      const response = await dryRunNode(workflowId, {
        node_id: selectedNodeId,
        node_type: nodeData.nodeType,
        node_config: nodeData.config,
        inputs,
      });
      setResult(response);
    } catch (runError) {
      setError(messageFrom(runError));
    } finally {
      setRunning(false);
    }
  };

  return (
    <section className="border-t border-line pt-3">
      <div className="mb-2 flex items-center gap-2">
        <FlaskConical size={12} className="text-pulse" />
        <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-ghost">
          试运行
        </span>
        <span className="ml-auto rounded-xs bg-pulse/10 px-1.5 py-0.5 font-mono text-[8px] text-pulse/80">
          不入历史
        </span>
      </div>

      <div className="space-y-2.5">
        {fields.map((field) => (
          <MockInput
            key={field.name}
            field={field}
            value={values[field.name]}
            objectDraft={objectDrafts[field.name]}
            onChange={(value) => setValues((current) => ({ ...current, [field.name]: value }))}
            onObjectDraftChange={(draft) =>
              setObjectDrafts((current) => ({ ...current, [field.name]: draft }))
            }
          />
        ))}
      </div>

      <button
        type="button"
        onClick={() => void run()}
        disabled={running}
        className="mt-3 flex h-8 w-full items-center justify-center gap-1.5 rounded-md border border-pulse/40 bg-pulse/10 px-3 text-[11px] font-medium text-pulse transition hover:bg-pulse/20 disabled:opacity-50"
      >
        {running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
        {running ? "执行中" : "试运行此节点"}
      </button>

      {error && (
        <p className="mt-2 flex items-start gap-1.5 border-l-2 border-bad px-2 text-[10px] text-bad">
          <TriangleAlert size={11} className="mt-0.5 shrink-0" />
          <span className="wrap-break-word">{error}</span>
        </p>
      )}

      {result && (
        <div className="mt-3 space-y-2 border-t border-line/70 pt-2">
          <SnapshotRow label="输出" value={result.output} />
          <SnapshotRow label="最终输出" value={result.final_output} />
          {(result.events?.length ?? 0) > 0 && (
            <SnapshotRow
              label={`事件 (${result.events?.length ?? 0})`}
              value={(result.events ?? []).map((event) => ({
                type: event.event_type,
                node: event.node_id,
                payload: event.payload,
              }))}
            />
          )}
        </div>
      )}
    </section>
  );
}

function MockInput(props: {
  field: MockField;
  value: unknown;
  objectDraft?: string;
  onChange: (value: unknown) => void;
  onObjectDraftChange: (value: string) => void;
}) {
  const { field } = props;
  return (
    <label className="flex flex-col gap-1">
      <span className="flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-wider text-ghost/70">
        {field.name}
        <span className="text-ghost/40">{field.type}</span>
      </span>
      {field.type === "string" && (
        <input
          className="field-input h-8 text-[11px]"
          value={typeof props.value === "string" ? props.value : ""}
          onChange={(event) => props.onChange(event.target.value)}
        />
      )}
      {field.type === "number" && (
        <input
          type="number"
          className="field-input h-8 text-[11px] font-mono"
          value={typeof props.value === "number" ? props.value : ""}
          onChange={(event) => {
            const parsed = Number(event.target.value);
            props.onChange(
              event.target.value && Number.isFinite(parsed) ? parsed : undefined,
            );
          }}
        />
      )}
      {field.type === "boolean" && (
        <input
          type="checkbox"
          className="h-4 w-4 accent-cyan-400"
          checked={Boolean(props.value)}
          onChange={(event) => props.onChange(event.target.checked)}
        />
      )}
      {field.type === "object" && (
        <textarea
          className={cn(
            "min-h-20 resize-y rounded-md border bg-void/70 px-2.5 py-1.5 font-mono text-[10px] leading-4 text-ice outline-hidden",
            "border-line focus:border-pulse/60",
          )}
          value={props.objectDraft ?? "{}"}
          onChange={(event) => props.onObjectDraftChange(event.target.value)}
          spellCheck={false}
        />
      )}
    </label>
  );
}

function SnapshotRow({ label, value }: { label: string; value: unknown }) {
  const text =
    value === undefined || value === null
      ? "--"
      : JSON.stringify(value, null, 2);
  return (
    <div>
      <p className="mb-1 font-mono text-[9px] uppercase text-ghost/60">{label}</p>
      <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-4 text-ice/80">
        {text}
      </pre>
    </div>
  );
}

export { NodeDryRunPanel };
