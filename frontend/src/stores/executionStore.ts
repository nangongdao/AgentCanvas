import { create } from "zustand";

import type {
  ExecutionEventContract,
  ExecutionEventType,
} from "@/api/contracts";
import type { DebugRunOptions } from "@/api/endpoints/workflows";
import { streamBuffer } from "@/features/execution/streamBuffer";

export type NodeRunStatus =
  | "idle"
  | "queued"
  | "running"
  | "streaming"
  | "succeeded"
  | "failed"
  | "skipped";

export interface TimelineItem {
  seq: number;
  type: ExecutionEventType;
  nodeId?: string | null;
  ts?: string;
  payload?: Record<string, unknown>;
}

export interface ExecutionCitation {
  id: string;
  label: string;
  documentId: string;
  filename: string;
  page?: number | null;
  chunkIndex: number;
  score: number;
  text: string;
  nodeId?: string;
}

interface ExecutionState {
  executionId: string | null;
  workflowId: string | null;
  status: "idle" | "running" | "succeeded" | "failed" | "cancelled" | "waiting_approval";
  nodeStatus: Record<string, NodeRunStatus>;
  /** Set of "source->target" pairs chosen by condition/supervisor routing. */
  takenEdges: Record<string, true>;
  timeline: TimelineItem[];
  error: string | null;
  output: Record<string, unknown> | null;
  citations: ExecutionCitation[];
  /** Per-node bounded output snapshot from `node_finished` events. Reused by
   * the C2-8 data-flow pane (inline run values) and edge hover summaries.
   * Values come from the backend `NODE_FINISHED` payload, which is already
   * redacted/bounded by `bounded_json_snapshot`, so no client-side secret
   * scrubbing is required here. */
  nodeOutputs: Record<string, unknown>;
  /** Inputs supplied to the current run, captured at `begin()`. The C2-8
   * data-flow pane resolves `{{input.x}}` references against this so the
   * "引用解析" section can show live input values, not just vars/node
   * outputs. Cleared on reset/begin. */
  runInputs: Record<string, unknown> | null;
  /** Pending human-approval request surfaced by a `workflow_interrupted` event.
   * For a debug breakpoint the request also carries `breakpoint` and a
   * `nodeOutputs` snapshot the editor can edit before continuing. */
  pendingApproval: {
    nodeId: string | null;
    title: string;
    instruction: string;
    formSchema: Record<string, unknown>;
    breakpoint: string | null;
    nodeOutputs: Record<string, unknown> | null;
  } | null;
  /** C5-7: node ids currently queued (waiting to run). Derived by FlowCanvas
   * from nodeStatus + the canvas edge graph on each event — a node is queued
   * when the workflow is running, it has not started yet, and one of its
   * predecessors has started or finished. Stored as a per-node record so
   * BaseNode can select `queuedNodes[id]` and only re-render when its own
   * queued membership flips (mirrors the takenEdges pattern), preserving
   * React Flow node memoization on large canvases. */
  queuedNodes: Record<string, true>;
  /** C5-7: node ids that did not execute by the time the run reached a
   * terminal state (a condition/switch branch not taken, or a path the run
   * never reached). Set when the workflow finishes/fails/is cancelled: every
   * node that never started is marked skipped so the canvas dims it. */
  skippedNodes: Record<string, true>;
  setRunQueue: (queued: Record<string, true>) => void;
  /** Debug-run options active for the current execution, so the pause points
   * persist across resumes. Null for a normal (non-debug) run. */
  debugOptions: DebugRunOptions | null;
  resuming: boolean;

  reset: () => void;
  begin: (
    executionId: string,
    workflowId: string,
    debug?: DebugRunOptions | null,
    inputs?: Record<string, unknown> | null,
  ) => void;
  handleEvent: (type: ExecutionEventType, data: ExecutionEventContract) => void;
  setResuming: (resuming: boolean) => void;
}

function asRecord(data: unknown): Record<string, unknown> {
  return data && typeof data === "object" ? (data as Record<string, unknown>) : {};
}

function extractCitations(value: unknown, nodeId?: string): ExecutionCitation[] {
  const record = asRecord(value);
  const raw = Array.isArray(record.citations) ? record.citations : [];
  return raw.flatMap((item) => {
    const citation = asRecord(item);
    if (!citation.id || !citation.filename || !citation.text) return [];
    return [
      {
        id: String(citation.id),
        label: String(citation.label ?? "[?]"),
        documentId: String(citation.document_id ?? ""),
        filename: String(citation.filename),
        page: typeof citation.page === "number" ? citation.page : null,
        chunkIndex: Number(citation.chunk_index ?? 0),
        score: Number(citation.score ?? 0),
        text: String(citation.text),
        nodeId: String(citation.node_id ?? nodeId ?? "") || undefined,
      },
    ];
  });
}

function mergeCitations(
  current: ExecutionCitation[],
  incoming: ExecutionCitation[],
): ExecutionCitation[] {
  const merged = new Map(current.map((citation) => [citation.id, citation]));
  for (const citation of incoming) merged.set(citation.id, citation);
  return [...merged.values()];
}

/** C5-7: at a terminal state, every node that never started this run is
 * considered skipped (a branch not taken, or a path the run never reached).
 * The canvas dims these uniformly to signal "did not execute". */
function computeSkippedNodes(state: ExecutionState): Record<string, true> {
  const skipped: Record<string, true> = {};
  for (const [id, status] of Object.entries(state.nodeStatus)) {
    if (status === "idle") skipped[id] = true;
  }
  return skipped;
}

export const useExecutionStore = create<ExecutionState>((set, get) => ({
  executionId: null,
  workflowId: null,
  status: "idle",
  nodeStatus: {},
  takenEdges: {},
  timeline: [],
  error: null,
  output: null,
  citations: [],
  nodeOutputs: {},
  runInputs: null,
  pendingApproval: null,
  debugOptions: null,
  resuming: false,
  queuedNodes: {},
  skippedNodes: {},

  reset: () => {
    streamBuffer.clear();
    set({
      executionId: null,
      workflowId: null,
      status: "idle",
      nodeStatus: {},
      takenEdges: {},
      timeline: [],
      error: null,
      output: null,
      citations: [],
      nodeOutputs: {},
      runInputs: null,
      pendingApproval: null,
      debugOptions: null,
      resuming: false,
      queuedNodes: {},
      skippedNodes: {},
    });
  },

  setRunQueue: (queued) => {
    const current = get().queuedNodes;
    let changed = false;
    for (const id of Object.keys(queued)) if (!current[id]) { changed = true; break; }
    if (!changed) {
      for (const id of Object.keys(current)) if (!queued[id]) { changed = true; break; }
    }
    if (!changed) return;
    set({ queuedNodes: queued });
  },

  begin: (executionId, workflowId, debug = null, inputs = null) => {
    streamBuffer.clear();
    set({
      executionId,
      workflowId,
      status: "running",
      nodeStatus: {},
      takenEdges: {},
      timeline: [],
      error: null,
      output: null,
      citations: [],
      nodeOutputs: {},
      runInputs: inputs,
      pendingApproval: null,
      debugOptions: debug,
      resuming: false,
      queuedNodes: {},
      skippedNodes: {},
    });
  },

  setResuming: (resuming) => set({ resuming }),

  handleEvent: (type, data) => {
    const rec = asRecord(data);
    const nodeId = (rec.node_id as string | undefined) ?? undefined;
    const seq = Number(rec.seq ?? 0);
    const payload = (rec.payload as Record<string, unknown>) ?? {};
    const ts = rec.ts as string | undefined;

    // Dedupe by seq
    if (get().timeline.some((t) => t.seq === seq && seq > 0)) return;

    const timeline = [
      ...get().timeline,
      { seq, type, nodeId, ts, payload },
    ].sort((a, b) => a.seq - b.seq);

    const nodeStatus = { ...get().nodeStatus };

    switch (type) {
      case "workflow_started":
        set({ status: "running", timeline });
        return;
      case "node_started":
        if (nodeId) nodeStatus[nodeId] = "running";
        set({ nodeStatus, timeline });
        return;
      case "edge_taken": {
        const src = payload.source as string | undefined;
        const tgt = payload.target as string | undefined;
        if (src && tgt) {
          set({
            takenEdges: { ...get().takenEdges, [`${src}->${tgt}`]: true },
            timeline,
          });
        } else {
          set({ timeline });
        }
        return;
      }
      case "node_streaming": {
        if (nodeId && payload.kind === "text" && typeof payload.delta === "string") {
          streamBuffer.append(nodeId, payload.delta);
          nodeStatus[nodeId] = "streaming";
        }
        const citations =
          payload.kind === "retrieval"
            ? mergeCitations(get().citations, extractCitations(payload, nodeId))
            : get().citations;
        set({ nodeStatus, timeline, citations });
        return;
      }
      case "node_finished": {
        if (nodeId) {
          nodeStatus[nodeId] = "succeeded";
          // If finished carries full output text and buffer empty, seed it
          const out = payload.output;
          if (typeof out === "string" && !streamBuffer.get(nodeId)) {
            streamBuffer.set(nodeId, out);
          } else if (
            out &&
            typeof out === "object" &&
            typeof (out as { text?: string }).text === "string" &&
            !streamBuffer.get(nodeId)
          ) {
            streamBuffer.set(nodeId, (out as { text: string }).text);
          }
        }
        set({
          nodeStatus,
          timeline,
          // C2-8: persist the bounded output snapshot per node so the
          // data-flow pane and edge hover can show inline run values.
          // We store the output even when it is null/empty so the UI can
          // distinguish "not run yet" (undefined) from "ran but empty"
          // (null). Values are already redacted by bounded_json_snapshot.
          nodeOutputs:
            nodeId && "output" in payload
              ? { ...get().nodeOutputs, [nodeId]: payload.output ?? null }
              : get().nodeOutputs,
          citations: mergeCitations(
            get().citations,
            extractCitations(payload.output, nodeId),
          ),
        });
        return;
      }
      case "node_failed":
        if (nodeId) nodeStatus[nodeId] = "failed";
        set({
          nodeStatus,
          timeline,
          error: String(payload.error ?? "node failed"),
        });
        return;
      case "workflow_finished":
        set({
          status: "succeeded",
          timeline,
          output: (payload.output as Record<string, unknown>) ?? null,
          skippedNodes: computeSkippedNodes(get()),
          queuedNodes: {},
        });
        return;
      case "workflow_failed":
        set({
          status: "failed",
          timeline,
          error: String(payload.error ?? "workflow failed"),
          skippedNodes: computeSkippedNodes(get()),
          queuedNodes: {},
        });
        return;
      case "workflow_cancelled":
        set({
          status: "cancelled",
          timeline,
          skippedNodes: computeSkippedNodes(get()),
          queuedNodes: {},
        });
        return;
      case "workflow_interrupted": {
        const reason = payload.reason;
        if (reason === "human_approval") {
          const request = (payload.request as Record<string, unknown>) ?? {};
          const breakpoint =
            typeof request.breakpoint === "string" ? request.breakpoint : null;
          const nodeOutputs = breakpoint
            ? (request.node_outputs as Record<string, unknown>) ?? null
            : null;
          set({
            status: "waiting_approval",
            timeline,
            pendingApproval: {
              nodeId: (payload.node_id as string) ?? (rec.node_id as string) ?? null,
              title: String(request.title ?? "人工审批"),
              instruction: String(request.instruction ?? "请确认是否继续"),
              formSchema: (request.form_schema as Record<string, unknown>) ?? {},
              breakpoint,
              nodeOutputs,
            },
          });
        } else {
          set({ timeline });
        }
        return;
      }
      default:
        set({ timeline });
    }
  },
}));
