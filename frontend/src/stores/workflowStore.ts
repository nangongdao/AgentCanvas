import {
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type OnConnect,
  type OnEdgesChange,
  type OnNodesChange,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
} from "@xyflow/react";
import { create, useStore } from "zustand";
import { temporal, type TemporalState } from "zundo";

import type {
  CanvasGroup,
  CanvasMetadata,
  CanvasNote,
  CanvasNodeData,
  NodeType,
  SubgraphClipboard,
  WorkflowDSL,
  WorkflowSettings,
  WorkflowVariable,
} from "@/types/dsl";
import { canvasNodeType, DEFAULT_SETTINGS, NODE_META } from "@/types/dsl";
import { defaultIterationConfig } from "@/features/canvas/iterationConfig";
import { uid } from "@/utils/id";

export type FlowNode = Node<CanvasNodeData>;
export type FlowEdge = Edge;

export const WORKFLOW_HISTORY_LIMIT = 50;

const CLIPBOARD_STORAGE_KEY = "agentcanvas:clipboard";

/** Persist the canvas clipboard to sessionStorage so a copied subgraph
 * survives full page reloads and route changes that recreate the store. It is
 * intentionally session-scoped: other tabs/tabs-closed do not inherit it, and
 * it never enters the undo/redo history (partialize omits it). */
function writeClipboard(clipboard: SubgraphClipboard | null): void {
  if (typeof window === "undefined") return;
  try {
    if (clipboard === null) {
      window.sessionStorage.removeItem(CLIPBOARD_STORAGE_KEY);
    } else {
      window.sessionStorage.setItem(
        CLIPBOARD_STORAGE_KEY,
        JSON.stringify(clipboard),
      );
    }
  } catch {
    // sessionStorage may be unavailable (private mode) or the payload may exceed
    // the quota; clipboard is best-effort and must never break canvas edits.
  }
}

function readClipboard(): SubgraphClipboard | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(CLIPBOARD_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as SubgraphClipboard;
    if (!Array.isArray(parsed.nodes) || parsed.nodes.length === 0) {
      // A stale empty payload should not masquerade as a usable clipboard;
      // clear it so a later re-init does not re-parse the same dead entry.
      writeClipboard(null);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

/** Deep-clone a node's data so the clipboard snapshot and the pasted copy are
 * fully isolated from the original node. A shallow `{ ...node.data }` clones
 * the top level but shares the nested `config` object (e.g. an agent's
 * `params: { temperature }`, a condition's `branches: [{ group: { rules } }]`),
 * so editing the original node's nested fields between copy and paste — or
 * sharing the clipboard across pastes — would leak those edits back into the
 * snapshot. `structuredClone` covers DSL JSON; `JSON.parse(JSON.stringify())`
 * is the fallback for environments without it. */
function cloneNodeData(data: CanvasNodeData): CanvasNodeData {
  try {
    return structuredClone(data);
  } catch {
    return JSON.parse(JSON.stringify(data)) as CanvasNodeData;
  }
}

type WorkflowHistoryState = Pick<
  WorkflowState,
  "variables" | "nodes" | "edges" | "canvas"
>;

/** C5-11: derive a screen-reader label for a canvas node from its display
 * label and type. Baked onto the node object at creation/mutation time so
 * React Flow renders it as the `aria-label` of the focusable node wrapper
 * WITHOUT forcing a per-frame `.map` in the render path — remapping every
 * node to a fresh object breaks React Flow's internal node memoization and
 * regresses 100-node drag paint from ~15ms to ~130ms (see C5-5 notes). The
 * label only changes when the node's label or type changes, so it is
 * recomputed at those mutation points, not in render. `applyNodeChanges`
 * spreads the node on position/select changes, so `ariaLabel` survives
 * drag and selection intact. */
function ariaLabelFor(label: string, nodeType: NodeType): string {
  const typeLabel = NODE_META[nodeType]?.label ?? nodeType;
  const trimmed = label.trim();
  if (!trimmed || trimmed === typeLabel) return typeLabel;
  return `${trimmed}（${typeLabel}）`;
}

function withAriaLabel(node: FlowNode): FlowNode {
  return { ...node, ariaLabel: ariaLabelFor(node.data.label, node.data.nodeType) };
}

interface WorkflowState {
  name: string;
  variables: WorkflowVariable[];
  settings: WorkflowSettings;
  nodes: FlowNode[];
  edges: FlowEdge[];
  canvas: CanvasMetadata;
  selectedNodeId: string | null;
  selectedNodeIds: string[];
  selectedEdgeId: string | null;
  clipboard: SubgraphClipboard | null;
  dirty: boolean;
  version: number;
  changeId: number;

  setName: (name: string) => void;
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onConnect: OnConnect;
  addNode: (
    type: NodeType,
    position: { x: number; y: number },
    config?: Record<string, unknown>,
    label?: string,
  ) => void;
  setVariables: (variables: WorkflowVariable[]) => void;
  setSelectedNodeId: (id: string | null) => void;
  setSelectedNodeIds: (ids: string[]) => void;
  setSelectedEdgeId: (id: string | null) => void;
  updateNodePositions: (positions: Record<string, { x: number; y: number }>) => void;
  copySelection: () => void;
  pasteSubgraph: (position?: { x: number; y: number }) => void;
  addCanvasGroup: (nodeIds?: string[], name?: string) => void;
  updateCanvasGroup: (id: string, patch: Partial<CanvasGroup>) => void;
  moveCanvasGroup: (id: string, position: { x: number; y: number }) => void;
  toggleCanvasGroup: (id: string) => void;
  removeCanvasGroup: (id: string) => void;
  addCanvasNote: (position?: { x: number; y: number }) => void;
  updateCanvasNote: (id: string, patch: Partial<CanvasNote>) => void;
  removeCanvasNote: (id: string) => void;
  updateEdgeLabel: (id: string, label: string) => void;
  updateNodeConfig: (id: string, config: Record<string, unknown>) => void;
  updateNodeLabel: (id: string, label: string) => void;
  toDSL: () => WorkflowDSL;
  markClean: (version?: number, savedChangeId?: number) => void;
  loadDSL: (dsl: WorkflowDSL, version?: number) => void;
  /** Apply a copilot draft onto the canvas as an undoable change. */
  applyCopilotDraft: (dsl: WorkflowDSL) => void;
  newWorkflow: () => void;
}

function defaultConfig(type: NodeType): Record<string, unknown> {
  switch (type) {
    case "start":
      return { input_schema: [{ name: "user_query", type: "string", required: true }] };
    case "agent":
      return {
        model_config_id: "default",
        agent_mode: "simple",
        system_prompt: "你是一个有帮助的助手。",
        user_prompt: "{{input.user_query}}",
        tools: [],
        context_nodes: [],
        max_tool_rounds: 5,
        params: { temperature: 0.3 },
      };
    case "tool":
      return { server_id: "", tool_name: "", arguments: {}, timeout_seconds: 30 };
    case "condition":
      return {
        branches: [
          {
            id: "yes",
            group: {
              op: "and",
              rules: [
                { left: "{{input.user_query}}", operator: "not_empty", right: null },
              ],
            },
          },
        ],
        default_branch: "else",
      };
    case "rag":
      return {
        kb_id: "",
        query: "{{input.user_query}}",
        top_k: 5,
        score_threshold: 0.2,
      };
    case "human":
      return { title: "人工审批", instruction: "请确认是否继续", form_schema: {} };
    case "iteration":
      return defaultIterationConfig();
    case "end":
      return { output_template: { answer: "{{nodes.prev.output}}" } };
    default:
      return {};
  }
}

const DEFAULT_VARIABLES: WorkflowVariable[] = [
  { name: "user_query", type: "string", required: true, default: null },
];

let historyGroupDepth = 0;
let historyGroupCaptured = false;

function sameVariables(
  left: WorkflowVariable[],
  right: WorkflowVariable[],
): boolean {
  return (
    left.length === right.length &&
    left.every((variable, index) => {
      const candidate = right[index];
      return (
        candidate !== undefined &&
        variable.name === candidate.name &&
        variable.type === candidate.type &&
        variable.required === candidate.required &&
        JSON.stringify(variable.default) === JSON.stringify(candidate.default)
      );
    })
  );
}

function sameNodes(left: FlowNode[], right: FlowNode[]): boolean {
  return (
    left.length === right.length &&
    left.every((node, index) => {
      const candidate = right[index];
      return (
        candidate !== undefined &&
        node.id === candidate.id &&
        node.type === candidate.type &&
        node.position.x === candidate.position.x &&
        node.position.y === candidate.position.y &&
        node.data.label === candidate.data.label &&
        node.data.nodeType === candidate.data.nodeType &&
        node.data.config === candidate.data.config
      );
    })
  );
}

function sameEdges(left: FlowEdge[], right: FlowEdge[]): boolean {
  return (
    left.length === right.length &&
    left.every((edge, index) => {
      const candidate = right[index];
      return (
        candidate !== undefined &&
        edge.id === candidate.id &&
        edge.source === candidate.source &&
        edge.target === candidate.target &&
        edge.sourceHandle === candidate.sourceHandle &&
        edge.targetHandle === candidate.targetHandle &&
        edge.label === candidate.label
      );
    })
  );
}

function sameHistoryState(
  left: WorkflowHistoryState,
  right: WorkflowHistoryState,
): boolean {
  return (
    sameVariables(left.variables, right.variables) &&
    sameNodes(left.nodes, right.nodes) &&
    sameEdges(left.edges, right.edges) &&
    JSON.stringify(left.canvas) === JSON.stringify(right.canvas)
  );
}

const DEFAULT_CANVAS: CanvasMetadata = { groups: [], notes: [] };

function initialNodes(): FlowNode[] {
  return [
    withAriaLabel({
      id: "start_1",
      type: "start",
      position: { x: 80, y: 180 },
      data: {
        label: NODE_META.start.label,
        nodeType: "start",
        config: defaultConfig("start"),
      },
    }),
    withAriaLabel({
      id: "agent_1",
      type: "agent",
      position: { x: 340, y: 180 },
      data: { label: "Agent", nodeType: "agent", config: defaultConfig("agent") },
    }),
    withAriaLabel({
      id: "end_1",
      type: "end",
      position: { x: 620, y: 180 },
      data: {
        label: NODE_META.end.label,
        nodeType: "end",
        config: defaultConfig("end"),
      },
    }),
  ];
}

function initialEdges(): FlowEdge[] {
  return [
    { id: "e_start_agent", source: "start_1", target: "agent_1" },
    { id: "e_agent_end", source: "agent_1", target: "end_1" },
  ];
}

/**
 * Build canvas nodes/edges from a DSL document.
 *
 * Shared by ``loadDSL`` (open a stored workflow) and ``applyCopilotDraft``
 * (apply an AI draft) so the two can never drift on how a document maps onto
 * the canvas.
 */
function dslToGraph(dsl: WorkflowDSL): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const nodes: FlowNode[] = dsl.nodes.map((node) =>
    withAriaLabel({
      id: node.id,
      type: canvasNodeType(node.type),
      position: node.position,
      data: {
        label: node.name ?? NODE_META[node.type]?.label ?? node.type,
        nodeType: node.type,
        config: node.config ?? {},
      },
    }),
  );
  const edges: FlowEdge[] = dsl.edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.source_handle ?? undefined,
    targetHandle: edge.target_handle ?? undefined,
    label: edge.label ?? undefined,
  }));
  return { nodes, edges };
}

export const useWorkflowStore = create<WorkflowState>()(
  temporal(
    (set, get) => ({
  name: "未命名工作流",
  variables: DEFAULT_VARIABLES.map((variable) => ({ ...variable })),
  settings: { ...DEFAULT_SETTINGS },
  nodes: initialNodes(),
  edges: initialEdges(),
  canvas: { ...DEFAULT_CANVAS },
  selectedNodeId: null,
  selectedNodeIds: [],
  selectedEdgeId: null,
  clipboard: readClipboard(),
  dirty: false,
  version: 1,
  changeId: 0,

  setName: (name) =>
    set((state) =>
      state.name === name
        ? state
        : { name, dirty: true, changeId: state.changeId + 1 },
    ),

  onNodesChange: (changes: NodeChange[]) => {
    const changesDsl = changes.some(
      (change) =>
        change.type !== "select" &&
        change.type !== "dimensions" &&
        !(change.type === "position" && change.dragging === true),
    );
    set((state) => ({
      nodes: applyNodeChanges(changes, state.nodes) as unknown as FlowNode[],
      ...(changesDsl
        ? { dirty: true, changeId: state.changeId + 1 }
        : {}),
    }));
    if (changes.some((change) => !["select", "dimensions"].includes(change.type))) {
      captureWorkflowHistoryGroup();
    }
  },

  onEdgesChange: (changes: EdgeChange[]) => {
    const changesDsl = changes.some((change) => change.type !== "select");
    set((state) => ({
      edges: applyEdgeChanges(changes, state.edges),
      ...(changesDsl
        ? { dirty: true, changeId: state.changeId + 1 }
        : {}),
    }));
    if (changesDsl) captureWorkflowHistoryGroup();
  },

  onConnect: (connection: Connection) => {
    set((state) => ({
      edges: addEdge({ ...connection, id: uid("e") }, state.edges),
      dirty: true,
      changeId: state.changeId + 1,
    }));
    captureWorkflowHistoryGroup();
  },

  addNode: (type, position, initialConfig, initialLabel) => {
    const id = uid(type);
    const meta = NODE_META[type] ?? {
      label: type,
      color: "#a78bfa",
      description: "外部插件节点",
    };
    const node: FlowNode = withAriaLabel({
      id,
      type: canvasNodeType(type),
      position,
      data: {
        label: initialLabel ?? meta.label,
        nodeType: type,
        config: { ...defaultConfig(type), ...(initialConfig ?? {}) },
      },
    });
    set((state) => ({
      nodes: [
        ...state.nodes.map((node) =>
          node.selected ? { ...node, selected: false } : node,
        ),
        { ...node, selected: true },
      ],
      dirty: true,
      changeId: state.changeId + 1,
      selectedNodeId: id,
      selectedNodeIds: [id],
    }));
    captureWorkflowHistoryGroup();
  },

  setVariables: (variables) => {
    set((state) =>
      sameVariables(state.variables, variables)
        ? state
        : {
            variables,
            dirty: true,
            changeId: state.changeId + 1,
          },
    );
    captureWorkflowHistoryGroup();
  },

  setSelectedNodeId: (id) => set({ selectedNodeId: id }),

  setSelectedNodeIds: (ids) =>
    set({
      selectedNodeIds: [...new Set(ids)],
      selectedNodeId: ids[0] ?? null,
    }),

  setSelectedEdgeId: (id) => set({ selectedEdgeId: id }),

  updateNodePositions: (positions) => {
    let changed = false;
    set((state) => {
      const nodes = state.nodes.map((node) => {
        const position = positions[node.id];
        if (
          position === undefined ||
          (position.x === node.position.x && position.y === node.position.y)
        ) {
          return node;
        }
        changed = true;
        return { ...node, position };
      });
      return changed
        ? { nodes, dirty: true, changeId: state.changeId + 1 }
        : state;
    });
    if (changed) captureWorkflowHistoryGroup();
  },

  copySelection: () => {
    const state = get();
    if (state.selectedNodeIds.length === 0) return;
    const selectedSet = new Set(state.selectedNodeIds);
    const selectedNodes = state.nodes.filter((node) => selectedSet.has(node.id));
    if (selectedNodes.length === 0) {
      writeClipboard(null);
      set({ clipboard: null });
      return;
    }
    // Keep only edges whose both endpoints are in the selection so the pasted
    // subgraph preserves internal wiring without dangling references.
    const internalEdges = state.edges.filter(
      (edge) => selectedSet.has(edge.source) && selectedSet.has(edge.target),
    );
    const clipboard: SubgraphClipboard = {
      nodes: selectedNodes.map((node) => ({
        id: node.id,
        type: node.data.nodeType,
        position: { ...node.position },
        data: cloneNodeData(node.data),
      })),
      edges: internalEdges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        sourceHandle: edge.sourceHandle ?? null,
        targetHandle: edge.targetHandle ?? null,
        label: typeof edge.label === "string" ? edge.label : undefined,
      })),
    };
    writeClipboard(clipboard);
    set({ clipboard });
  },

  pasteSubgraph: (position) => {
    const state = get();
    const clipboard = state.clipboard;
    if (!clipboard || clipboard.nodes.length === 0) return;
    // Offset so the pasted copy lands next to the original rather than on top
    // of it; a fixed nudge keeps repeat-paste stacks visible and selectable.
    const PASTE_OFFSET = 40;
    // Normalize the subgraph origin to its top-left so an explicit target
    // position drops the whole selection where the caller pointed.
    const minX = Math.min(...clipboard.nodes.map((node) => node.position.x));
    const minY = Math.min(...clipboard.nodes.map((node) => node.position.y));
    const originX = position?.x ?? minX + PASTE_OFFSET;
    const originY = position?.y ?? minY + PASTE_OFFSET;
    const dx = originX - minX;
    const dy = originY - minY;

    const idMap = new Map<string, string>();
    const newNodes: FlowNode[] = clipboard.nodes.map((node) => {
      const newId = uid(node.type);
      idMap.set(node.id, newId);
      return withAriaLabel({
        id: newId,
        // Normalize stored DSL type (e.g. ``plugin.xxx``) to the canvas
        // renderer key (``plugin``) so React Flow finds the registered node
        // component — ``addNode`` and ``loadDSL`` already do this via
        // ``canvasNodeType``; paste must do the same or plugin nodes fall back
        // to the default renderer.
        type: canvasNodeType(node.type),
        position: {
          x: Math.round(node.position.x + dx),
          y: Math.round(node.position.y + dy),
        },
        data: cloneNodeData(node.data),
        selected: true,
      });
    });
    const pastedIds = new Set(newNodes.map((node) => node.id));
    const newEdges: FlowEdge[] = [];
    for (const edge of clipboard.edges) {
      const source = idMap.get(edge.source);
      const target = idMap.get(edge.target);
      if (!source || !target) continue;
      newEdges.push({
        id: uid("e"),
        source,
        target,
        sourceHandle: edge.sourceHandle ?? undefined,
        targetHandle: edge.targetHandle ?? undefined,
        label: edge.label,
      });
    }

    set((current) => ({
      nodes: [
        ...current.nodes.map((node) =>
          node.selected ? { ...node, selected: false } : node,
        ),
        ...newNodes,
      ],
      edges: [...current.edges, ...newEdges],
      dirty: true,
      changeId: current.changeId + 1,
      selectedNodeId: newNodes[0]?.id ?? current.selectedNodeId,
      selectedNodeIds: [...pastedIds],
    }));
    captureWorkflowHistoryGroup();
  },

  addCanvasGroup: (nodeIds = [], initialName) => {
    const state = get();
    const selected = state.nodes.filter((node) => nodeIds.includes(node.id));
    const padding = 28;
    const minX = selected.length ? Math.min(...selected.map((node) => node.position.x)) : 180;
    const minY = selected.length ? Math.min(...selected.map((node) => node.position.y)) : 140;
    const maxX = selected.length
      ? Math.max(...selected.map((node) => node.position.x + 168))
      : minX + 360;
    const maxY = selected.length
      ? Math.max(...selected.map((node) => node.position.y + 72))
      : minY + 220;
    const group: CanvasGroup = {
      id: uid("group"),
      name: initialName?.trim() || `分组 ${state.canvas.groups.length + 1}`,
      position: { x: Math.round(minX - padding), y: Math.round(minY - 52) },
      width: Math.max(280, Math.round(maxX - minX + padding * 2)),
      height: Math.max(180, Math.round(maxY - minY + padding + 52)),
      node_ids: [...new Set(nodeIds)],
      collapsed: false,
      color: "#22d3ee",
    };
    set((current) => ({
      canvas: { ...current.canvas, groups: [...current.canvas.groups, group] },
      dirty: true,
      changeId: current.changeId + 1,
    }));
    captureWorkflowHistoryGroup();
  },

  updateCanvasGroup: (id, patch) => {
    let changed = false;
    set((state) => {
      const groups = state.canvas.groups.map((group) => {
        if (group.id !== id) return group;
        const next = { ...group, ...patch };
        changed = JSON.stringify(next) !== JSON.stringify(group);
        return changed ? next : group;
      });
      return changed
        ? { canvas: { ...state.canvas, groups }, dirty: true, changeId: state.changeId + 1 }
        : state;
    });
    if (!changed) return;
    captureWorkflowHistoryGroup();
  },

  moveCanvasGroup: (id, position) => {
    const current = get();
    const group = current.canvas.groups.find((candidate) => candidate.id === id);
    if (!group) return;
    const dx = position.x - group.position.x;
    const dy = position.y - group.position.y;
    if (dx === 0 && dy === 0) return;
    const memberIds = new Set(group.node_ids);
    set((state) => ({
      canvas: {
        ...state.canvas,
        groups: state.canvas.groups.map((candidate) =>
          candidate.id === id ? { ...candidate, position } : candidate,
        ),
      },
      nodes: state.nodes.map((node) =>
        memberIds.has(node.id)
          ? { ...node, position: { x: node.position.x + dx, y: node.position.y + dy } }
          : node,
      ),
      dirty: true,
      changeId: state.changeId + 1,
    }));
    captureWorkflowHistoryGroup();
  },

  toggleCanvasGroup: (id) => {
    set((state) => ({
      canvas: {
        ...state.canvas,
        groups: state.canvas.groups.map((group) =>
          group.id === id ? { ...group, collapsed: !group.collapsed } : group,
        ),
      },
      dirty: true,
      changeId: state.changeId + 1,
    }));
    captureWorkflowHistoryGroup();
  },

  removeCanvasGroup: (id) => {
    set((state) => ({
      canvas: {
        ...state.canvas,
        groups: state.canvas.groups.filter((group) => group.id !== id),
      },
      dirty: true,
      changeId: state.changeId + 1,
    }));
    captureWorkflowHistoryGroup();
  },

  addCanvasNote: (position = { x: 220, y: 120 }) => {
    const state = get();
    const note: CanvasNote = {
      id: uid("note"),
      text: "在这里记录流程意图…",
      position,
      width: 240,
      height: 140,
      color: "#fbbf24",
    };
    set((current) => ({
      canvas: { ...current.canvas, notes: [...current.canvas.notes, note] },
      dirty: true,
      changeId: state.changeId + 1,
    }));
    captureWorkflowHistoryGroup();
  },

  updateCanvasNote: (id, patch) => {
    let changed = false;
    set((state) => {
      const notes = state.canvas.notes.map((note) => {
        if (note.id !== id) return note;
        const next = { ...note, ...patch };
        changed = JSON.stringify(next) !== JSON.stringify(note);
        return changed ? next : note;
      });
      return changed
        ? { canvas: { ...state.canvas, notes }, dirty: true, changeId: state.changeId + 1 }
        : state;
    });
    if (!changed) return;
    captureWorkflowHistoryGroup();
  },

  removeCanvasNote: (id) => {
    set((state) => ({
      canvas: {
        ...state.canvas,
        notes: state.canvas.notes.filter((note) => note.id !== id),
      },
      dirty: true,
      changeId: state.changeId + 1,
    }));
    captureWorkflowHistoryGroup();
  },

  updateEdgeLabel: (id, label) => {
    const nextLabel = label.trim() || undefined;
    let changed = false;
    set((state) => {
      const edges = state.edges.map((edge) => {
        if (edge.id !== id || edge.label === nextLabel) return edge;
        changed = true;
        return { ...edge, label: nextLabel };
      });
      return changed ? { edges, dirty: true, changeId: state.changeId + 1 } : state;
    });
    if (!changed) return;
    captureWorkflowHistoryGroup();
  },

  updateNodeConfig: (id, config) => {
    set((state) => ({
      nodes: state.nodes.map((node) =>
        node.id === id ? withAriaLabel({ ...node, data: { ...node.data, config } }) : node,
      ),
      dirty: true,
      changeId: state.changeId + 1,
    }));
    captureWorkflowHistoryGroup();
  },

  updateNodeLabel: (id, label) => {
    set((state) => ({
      nodes: state.nodes.map((node) =>
        node.id === id ? withAriaLabel({ ...node, data: { ...node.data, label } }) : node,
      ),
      dirty: true,
      changeId: state.changeId + 1,
    }));
    captureWorkflowHistoryGroup();
  },

  toDSL: () => {
    const { name, variables, settings, nodes, edges, canvas } = get();
    return {
      version: "1.0",
      name,
      variables,
      settings,
      nodes: nodes.map((node) => ({
        id: node.id,
        type: node.data.nodeType,
        name: node.data.label,
        position: node.position,
        config: node.data.config,
      })),
      edges: edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        source_handle: edge.sourceHandle ?? null,
        target_handle: edge.targetHandle ?? null,
        label: typeof edge.label === "string" ? edge.label : null,
      })),
      canvas,
    };
  },

  markClean: (version, savedChangeId) =>
    set((state) => ({
      dirty:
        savedChangeId === undefined || state.changeId === savedChangeId
          ? false
          : state.dirty,
      version: version ?? state.version,
    })),

  loadDSL: (dsl, version = 1) => {
    const { nodes, edges } = dslToGraph(dsl);
    replaceWorkflowHistoryBaseline(() => {
      set({
        name: dsl.name,
        variables: dsl.variables,
        settings: dsl.settings,
        nodes,
        edges,
        canvas: dsl.canvas ?? { ...DEFAULT_CANVAS },
        dirty: false,
        version,
        changeId: 0,
        selectedNodeId: null,
        selectedNodeIds: [],
        selectedEdgeId: null,
      });
    });
  },

  applyCopilotDraft: (dsl) =>
    set((state) => {
      const { nodes, edges } = dslToGraph(dsl);
      return {
        name: dsl.name || state.name,
        variables: dsl.variables,
        settings: dsl.settings,
        nodes,
        edges,
        canvas: dsl.canvas ?? { ...DEFAULT_CANVAS },
        dirty: true,
        changeId: state.changeId + 1,
        selectedNodeId: null,
        selectedNodeIds: [],
        selectedEdgeId: null,
      };
    }),

  newWorkflow: () =>
    replaceWorkflowHistoryBaseline(() => {
      set({
        name: "未命名工作流",
        variables: DEFAULT_VARIABLES.map((variable) => ({ ...variable })),
        settings: { ...DEFAULT_SETTINGS },
        nodes: initialNodes(),
        edges: initialEdges(),
        canvas: { ...DEFAULT_CANVAS },
        dirty: false,
        version: 1,
        changeId: 0,
        selectedNodeId: null,
        selectedNodeIds: [],
        selectedEdgeId: null,
      });
    }),
    }),
    {
      limit: WORKFLOW_HISTORY_LIMIT,
      partialize: ({ variables, nodes, edges, canvas }) => ({
        variables,
        nodes,
        edges,
        canvas,
      }),
      equality: sameHistoryState,
    },
  ),
);

function captureWorkflowHistoryGroup() {
  if (historyGroupDepth === 0 || historyGroupCaptured) return;
  useWorkflowStore.temporal.getState().pause();
  historyGroupCaptured = true;
}

function finishWorkflowHistoryGroups() {
  historyGroupDepth = 0;
  if (historyGroupCaptured) useWorkflowStore.temporal.getState().resume();
  historyGroupCaptured = false;
}

function replaceWorkflowHistoryBaseline(update: () => void) {
  finishWorkflowHistoryGroups();
  const history = useWorkflowStore.temporal.getState();
  history.pause();
  try {
    update();
    history.clear();
  } finally {
    history.resume();
  }
}

export function beginWorkflowHistoryGroup() {
  if (historyGroupDepth === 0) historyGroupCaptured = false;
  historyGroupDepth += 1;
}

export function endWorkflowHistoryGroup() {
  if (historyGroupDepth === 0) return;
  historyGroupDepth -= 1;
  if (historyGroupDepth === 0 && historyGroupCaptured) {
    useWorkflowStore.temporal.getState().resume();
    historyGroupCaptured = false;
  }
}

let keyboardNudgeTimer: number | null = null;

/**
 * C5-11: coalesce a run of keyboard node nudges into one undo step. Each
 * arrow press emits its own position change; without grouping, moving a node
 * across the canvas with the keyboard would flood undo history with one step
 * per 5px nudge. The group opens on the first nudge and closes after a short
 * idle window, mirroring how a mouse drag collapses to a single step.
 */
export function beginKeyboardNudgeGroup(idleMs = 700) {
  if (keyboardNudgeTimer === null && historyGroupDepth === 0) {
    beginWorkflowHistoryGroup();
  }
  if (keyboardNudgeTimer !== null) window.clearTimeout(keyboardNudgeTimer);
  keyboardNudgeTimer = window.setTimeout(() => {
    keyboardNudgeTimer = null;
    endWorkflowHistoryGroup();
  }, idleMs);
}

function markHistoryReplayDirty() {
  useWorkflowStore.setState((state) => ({
    dirty: true,
    changeId: state.changeId + 1,
    selectedNodeId:
      state.selectedNodeId !== null &&
      state.nodes.some((node) => node.id === state.selectedNodeId)
        ? state.selectedNodeId
        : null,
    selectedNodeIds: state.selectedNodeIds.filter((id) =>
      state.nodes.some((node) => node.id === id),
    ),
  }));
}

export function undoWorkflow(): boolean {
  finishWorkflowHistoryGroups();
  const history = useWorkflowStore.temporal.getState();
  if (history.pastStates.length === 0) return false;
  history.undo();
  markHistoryReplayDirty();
  return true;
}

export function redoWorkflow(): boolean {
  finishWorkflowHistoryGroups();
  const history = useWorkflowStore.temporal.getState();
  if (history.futureStates.length === 0) return false;
  history.redo();
  markHistoryReplayDirty();
  return true;
}

export function useWorkflowHistory<T>(
  selector: (state: TemporalState<WorkflowHistoryState>) => T,
): T {
  return useStore(useWorkflowStore.temporal, selector);
}
