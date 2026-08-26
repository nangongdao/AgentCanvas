import { memo, useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  ViewportPortal,
  useReactFlow,
  type Edge,
  type IsValidConnection,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Loader2, RefreshCw, Terminal } from "lucide-react";

import { ApiError } from "@/api/client";
import { runWorkflow, type DebugRunOptions } from "@/api/endpoints/workflows";
import { CanvasCommandBar } from "@/features/canvas/CanvasCommandBar";
import { CanvasGroupFrame } from "@/features/canvas/CanvasGroupFrame";
import { CanvasNote } from "@/features/canvas/CanvasNote";
import {
  alignNodes,
  distributeNodes,
  layoutWorkflow,
  snapNodeChanges,
  type AlignmentGuide,
  type AlignmentMode,
  type DistributionMode,
} from "@/features/canvas/canvasLayout";
import { canConnect } from "@/features/canvas/connectionRules";
import { DropNodeSearch } from "@/features/canvas/DropNodeSearch";
import { dataFlowEdgeTypes } from "@/features/canvas/edges/DataFlowEdge";
import { nodeTypes } from "@/features/canvas/nodes/registry";
import { ConfigPanel } from "@/features/canvas/panels/ConfigPanel";
import { NodePalette } from "@/features/canvas/panels/NodePalette";
import { CanvasTour, isCanvasTourDue } from "@/features/canvas/CanvasTour";
import { RunDialog } from "@/features/canvas/panels/RunDialog";
import { ExecutionDrawer } from "@/features/execution/ExecutionDrawer";
import { useExecutionSSE } from "@/features/execution/useExecutionSSE";
import { useAuth } from "@/features/auth/AuthProvider";
import { PersistenceDialogs } from "@/features/workflows/PersistenceDialogs";
import { useWorkflowCollaboration } from "@/features/workflows/useWorkflowCollaboration";
import { useWorkflowPersistence } from "@/features/workflows/useWorkflowPersistence";
import { useExecutionStore } from "@/stores/executionStore";
import {
  beginKeyboardNudgeGroup,
  beginWorkflowHistoryGroup,
  endWorkflowHistoryGroup,
  redoWorkflow,
  undoWorkflow,
  useWorkflowHistory,
  useWorkflowStore,
} from "@/stores/workflowStore";
import { type NodeType } from "@/types/dsl";

const EDIT_DELETE_KEYS = ["Backspace", "Delete"];
const FIT_VIEW_OPTIONS = { padding: 0.18, minZoom: 0.55, maxZoom: 1 } as const;
const PRO_OPTIONS = { hideAttribution: true } as const;
// Enable React Flow viewport virtualization only once a canvas grows past this
// many nodes. Smaller canvases render every node so fitView's asynchronous
// viewport sizing cannot unmount nodes it has not yet framed; larger canvases
// trade that safety for steady-state paint/commit throughput.
const VIRTUALIZATION_NODE_THRESHOLD = 50;

function messageFrom(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string"
      ? error.detail
      : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

function CanvasInner() {
  const { can } = useAuth();
  const { fitView } = useReactFlow();
  const roleCanEdit = can("editor");
  const canAdmin = can("admin");
  const addNode = useWorkflowStore((state) => state.addNode);
  const name = useWorkflowStore((state) => state.name);
  const dirty = useWorkflowStore((state) => state.dirty);
  const version = useWorkflowStore((state) => state.version);
  // C6-2: nodes/edges are read via getState() inside the callbacks below
  // instead of hook subscriptions. A drag emits position changes every frame,
  // and each one replaces the store's nodes array reference — a hook
  // subscription here would re-render the entire command bar / palette /
  // panel subtree on every frame of a large-canvas drag. CanvasSurface (the
  // React Flow data source) keeps its own subscription.
  const selectedNodeIds = useWorkflowStore((state) => state.selectedNodeIds);
  const addCanvasGroup = useWorkflowStore((state) => state.addCanvasGroup);
  const addCanvasNote = useWorkflowStore((state) => state.addCanvasNote);
  const updateNodePositions = useWorkflowStore((state) => state.updateNodePositions);
  const copySelection = useWorkflowStore((state) => state.copySelection);
  const pasteSubgraph = useWorkflowStore((state) => state.pasteSubgraph);
  const hasClipboard = useWorkflowStore((state) => state.clipboard !== null);
  const setName = useWorkflowStore((state) => state.setName);
  const canUndo = useWorkflowHistory((state) => state.pastStates.length > 0);
  const canRedo = useWorkflowHistory((state) => state.futureStates.length > 0);

  const executionId = useExecutionStore((state) => state.executionId);
  const execStatus = useExecutionStore((state) => state.status);
  const begin = useExecutionStore((state) => state.begin);
  const resetExec = useExecutionStore((state) => state.reset);

  const [startingRun, setStartingRun] = useState(false);
  const [runDialogOpen, setRunDialogOpen] = useState(false);
  const [tourOpen, setTourOpen] = useState(isCanvasTourDue);
  const [toast, setToast] = useState<string | null>(null);

  useExecutionSSE(executionId);

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 3500);
  }, []);

  const collaboration = useWorkflowCollaboration(roleCanEdit, showToast);
  const editingAllowed = collaboration.editingAllowed;
  const persistence = useWorkflowPersistence(showToast, editingAllowed);
  // C6-2: stable callback identity so the memoized ConfigPanel (and any other
  // memoized consumer) does not re-render on every CanvasInner render — the
  // inline arrow previously defeated ConfigPanel's React.memo.
  const ensureSaved = useCallback(
    () => persistence.persist({ silent: true }),
    [persistence.persist],
  );

  const undo = useCallback(() => {
    if (editingAllowed) undoWorkflow();
  }, [editingAllowed]);
  const redo = useCallback(() => {
    if (editingAllowed) redoWorkflow();
  }, [editingAllowed]);

  const autoLayout = useCallback(() => {
    if (!editingAllowed) return;
    const { nodes, edges } = useWorkflowStore.getState();
    const positions = layoutWorkflow(nodes, edges);
    updateNodePositions(positions);
    window.requestAnimationFrame(() => {
      void fitView(FIT_VIEW_OPTIONS);
    });
    showToast("已完成自动布局");
  }, [editingAllowed, fitView, showToast, updateNodePositions]);

  const align = useCallback(
    (mode: AlignmentMode) => {
      if (!editingAllowed) return;
      const { nodes } = useWorkflowStore.getState();
      const positions = alignNodes(nodes, selectedNodeIds, mode);
      if (Object.keys(positions).length === 0) return;
      updateNodePositions(positions);
      showToast("已对齐所选节点");
    },
    [editingAllowed, selectedNodeIds, showToast, updateNodePositions],
  );

  const distribute = useCallback(
    (mode: DistributionMode) => {
      if (!editingAllowed) return;
      const { nodes } = useWorkflowStore.getState();
      const positions = distributeNodes(nodes, selectedNodeIds, mode);
      if (Object.keys(positions).length === 0) return;
      updateNodePositions(positions);
      showToast("已均匀分布所选节点");
    },
    [editingAllowed, selectedNodeIds, showToast, updateNodePositions],
  );

  const copy = useCallback(() => {
    if (!editingAllowed || selectedNodeIds.length === 0) return;
    copySelection();
    showToast(`已复制 ${selectedNodeIds.length} 个节点`);
  }, [copySelection, editingAllowed, selectedNodeIds.length, showToast]);

  const paste = useCallback(
    (position?: { x: number; y: number }) => {
      if (!editingAllowed) return;
      const count = useWorkflowStore.getState().clipboard?.nodes.length ?? 0;
      if (count === 0) return;
      pasteSubgraph(position);
      showToast(`已粘贴 ${count} 个节点`);
    },
    [editingAllowed, pasteSubgraph, showToast],
  );

  useEffect(() => {
    const handleClipboardShortcut = (event: KeyboardEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
      ) {
        return;
      }
      const key = event.key.toLowerCase();
      if (key === "c" && selectedNodeIds.length > 0) {
        event.preventDefault();
        copy();
      } else if (key === "v") {
        if (!hasClipboard) return;
        event.preventDefault();
        paste();
      }
    };
    window.addEventListener("keydown", handleClipboardShortcut);
    return () => window.removeEventListener("keydown", handleClipboardShortcut);
  }, [copy, hasClipboard, paste, selectedNodeIds.length]);

  useEffect(() => {
    const handleHistoryShortcut = (event: KeyboardEvent) => {
      if (!editingAllowed || (!event.ctrlKey && !event.metaKey)) return;
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
      ) {
        return;
      }

      const key = event.key.toLowerCase();
      const wantsUndo = key === "z" && !event.shiftKey;
      const wantsRedo = (key === "z" && event.shiftKey) || key === "y";
      if ((!wantsUndo || !canUndo) && (!wantsRedo || !canRedo)) return;
      event.preventDefault();
      if (wantsUndo) undo();
      else redo();
    };
    window.addEventListener("keydown", handleHistoryShortcut);
    return () => window.removeEventListener("keydown", handleHistoryShortcut);
  }, [canRedo, canUndo, editingAllowed, redo, undo]);

  const handleAdd = useCallback(
    (type: NodeType, config?: Record<string, unknown>, label?: string) => {
      if (!editingAllowed) return;
      addNode(
        type,
        { x: 280 + Math.random() * 120, y: 160 + Math.random() * 120 },
        config,
        label,
      );
    },
    [addNode, editingAllowed],
  );

  const run = useCallback(
    async (
      inputs: Record<string, unknown>,
      debug?: DebugRunOptions | null,
    ): Promise<boolean> => {
      if (!editingAllowed) {
        showToast("当前未持有工作流编辑权");
        return false;
      }
      setStartingRun(true);
      try {
        const workflowId = await persistence.persist({ silent: true });
        if (!workflowId) return false;
        resetExec();
        const execution = await runWorkflow(workflowId, inputs, undefined, debug);
        begin(execution.id, workflowId, debug, inputs);
        showToast(`执行已启动 ${execution.id.slice(0, 8)}`);
        return true;
      } catch (error) {
        showToast(`运行失败：${messageFrom(error)}`);
        return false;
      } finally {
        setStartingRun(false);
      }
    },
    [begin, editingAllowed, persistence, resetExec, showToast],
  );

  const running = execStatus === "running";
  const busy =
    startingRun ||
    collaboration.takeoverBusy ||
    persistence.loading ||
    persistence.saveState === "saving";

  return (
    <div className="ambient-stage flex h-full w-full flex-col">
      <CanvasCommandBar
        name={name}
        workflowId={persistence.workflowId}
        version={version}
        dirty={dirty}
        saveState={persistence.saveState}
        busy={busy}
        running={running}
        canEdit={roleCanEdit}
        editingAllowed={editingAllowed}
        canAdmin={canAdmin}
        collaboration={collaboration}
        canUndo={canUndo}
        canRedo={canRedo}
        selectionCount={selectedNodeIds.length}
        canPaste={hasClipboard}
        onUndo={undo}
        onRedo={redo}
        onAutoLayout={autoLayout}
        onAlign={align}
        onDistribute={distribute}
        onAddGroup={() => addCanvasGroup(selectedNodeIds)}
        onAddNote={() => addCanvasNote()}
        onCopy={copy}
        onPaste={() => paste()}
        onNameChange={setName}
        onSave={() => void persistence.persist()}
        onRun={() => setRunDialogOpen(true)}
        onOpenWorkflow={persistence.openWorkflow}
        onEnsureSaved={ensureSaved}
        onReloadWorkflow={persistence.reloadCurrent}
        onNotify={showToast}
        onActiveArchived={() => {
          persistence.openWithoutSaving(null);
          showToast("工作流已归档");
        }}
      />

      <div className="relative flex min-h-0 flex-1">
        {editingAllowed && <NodePalette onAdd={handleAdd} />}
        <div className="relative h-full min-h-0 min-w-0 flex-1">
          <div className="pointer-events-none absolute bottom-6 left-8 z-0 select-none">
            <p className="type-outline font-display text-[9vw] font-bold leading-none">
              FLOW
            </p>
          </div>

          <CanvasSurface
            editingAllowed={editingAllowed}
            workflowKey={persistence.workflowId ?? "new"}
          />

          {persistence.loading && (
            <div className="absolute inset-0 z-20 flex items-center justify-center bg-void/70 backdrop-blur-xs">
              <Loader2 size={22} className="animate-spin text-pulse" />
            </div>
          )}
          {persistence.routeError && !persistence.loading && (
            <div className="absolute inset-0 z-20 flex items-center justify-center bg-void/85 p-5">
              <div className="max-w-md text-center">
                <p className="text-sm font-semibold text-ice">无法打开工作流</p>
                <p className="mt-2 wrap-break-word text-xs text-bad">
                  {persistence.routeError}
                </p>
                <div className="mt-4 flex justify-center gap-2">
                  <button
                    type="button"
                    onClick={persistence.retryLoad}
                    className="flex h-9 items-center gap-2 rounded-md border border-line px-4 text-xs text-ice hover:bg-line"
                  >
                    <RefreshCw size={13} /> 重试
                  </button>
                  <button
                    type="button"
                    onClick={() => persistence.openWithoutSaving(null)}
                    className="h-9 rounded-md bg-pulse px-4 text-xs font-semibold text-void"
                  >
                    新建工作流
                  </button>
                </div>
              </div>
            </div>
          )}

          {toast && (
            <div className="absolute bottom-5 left-1/2 z-30 -translate-x-1/2 animate-fade-up">
              <div className="glass flex items-center gap-2 rounded-lg border border-line px-4 py-2 shadow-card">
                <Terminal size={13} className="text-pulse" />
                <span className="text-xs text-ice">{toast}</span>
              </div>
            </div>
          )}
        </div>
        {editingAllowed && (
          <ConfigPanel ensureWorkflowId={() => persistence.persist({ silent: true })} />
        )}
      </div>

      <ExecutionDrawer />
      {roleCanEdit && (
        <>
          <CanvasTour
            open={tourOpen && !persistence.loading && !persistence.routeError}
            onFinished={() => setTourOpen(false)}
          />
          <RunDialog
            open={runDialogOpen}
            working={startingRun}
            onClose={() => setRunDialogOpen(false)}
            onRun={run}
          />
          <PersistenceDialogs
            conflict={persistence.conflict}
            navigationBlocked={persistence.navigationBlocked}
            working={persistence.saveState === "saving"}
            onReloadConflict={persistence.reloadConflict}
            onMergeConflict={() => void persistence.mergeConflict()}
            onOverwriteConflict={() => void persistence.overwriteConflict()}
            onStay={persistence.cancelBlockedNavigation}
            onDiscardAndContinue={persistence.discardBlockedNavigation}
            onSaveAndContinue={() => void persistence.saveAndContinue()}
          />
        </>
      )}
    </div>
  );
}

const CanvasSurface = memo(function CanvasSurface({
  editingAllowed,
  workflowKey,
}: {
  editingAllowed: boolean;
  workflowKey: string;
}) {
  const nodes = useWorkflowStore((state) => state.nodes);
  const edges = useWorkflowStore((state) => state.edges);
  const onNodesChange = useWorkflowStore((state) => state.onNodesChange);
  const onEdgesChange = useWorkflowStore((state) => state.onEdgesChange);
  const onConnect = useWorkflowStore((state) => state.onConnect);
  const setSelectedNodeIds = useWorkflowStore((state) => state.setSelectedNodeIds);
  const setSelectedEdgeId = useWorkflowStore((state) => state.setSelectedEdgeId);
  const canvas = useWorkflowStore((state) => state.canvas);
  const moveCanvasGroup = useWorkflowStore((state) => state.moveCanvasGroup);
  const updateCanvasGroup = useWorkflowStore((state) => state.updateCanvasGroup);
  const toggleCanvasGroup = useWorkflowStore((state) => state.toggleCanvasGroup);
  const removeCanvasGroup = useWorkflowStore((state) => state.removeCanvasGroup);
  const moveCanvasNote = useWorkflowStore((state) => state.updateCanvasNote);
  const removeCanvasNote = useWorkflowStore((state) => state.removeCanvasNote);
  const updateCanvasNote = useWorkflowStore((state) => state.updateCanvasNote);
  const nodeStatus = useExecutionStore((state) => state.nodeStatus);
  const takenEdges = useExecutionStore((state) => state.takenEdges);
  const nodeOutputs = useExecutionStore((state) => state.nodeOutputs);
  const execStatus = useExecutionStore((state) => state.status);
  const setRunQueue = useExecutionStore((state) => state.setRunQueue);
  const [isDragging, setIsDragging] = useState(false);
  const [alignmentGuides, setAlignmentGuides] = useState<AlignmentGuide[]>([]);
  const [dropSearch, setDropSearch] = useState<{ x: number; y: number } | null>(null);
  const { screenToFlowPosition } = useReactFlow();
  const startDragging = useCallback(() => {
    setIsDragging(true);
    beginWorkflowHistoryGroup();
  }, []);
  const stopDragging = useCallback(() => {
    setIsDragging(false);
    setAlignmentGuides([]);
    endWorkflowHistoryGroup();
  }, []);
  const beforeDelete = useCallback(async () => {
    beginWorkflowHistoryGroup();
    return true;
  }, []);
  const afterDelete = useCallback(() => {
    endWorkflowHistoryGroup();
  }, []);

  // C5-7: gate every live connection against the same structural rules the
  // backend enforces on save/compile (start has no inbound, end has no
  // outbound, no self-loop, no duplicate edge, valid condition handle). This
  // stops an invalid edge at drag time instead of surfacing it on run.
  const isValidConnection: IsValidConnection<Edge> = useCallback(
    (connection) => canConnect(connection, nodes, edges),
    [nodes, edges],
  );

  // C5-7: when a connection drag ends on empty canvas (no target handle),
  // open the node-search palette at the drop point so the editor can insert
  // a node and wire it in one motion. We capture the start node id on
  // connect start so DropNodeSearch knows the pending source to connect.
  const [pendingConnectionSource, setPendingConnectionSource] = useState<string | null>(null);
  const onConnectStart = useCallback(
    (_event: MouseEvent | TouchEvent, params: { nodeId: string | null }) => {
      setPendingConnectionSource(params.nodeId);
    },
    [],
  );
  const onConnectEnd = useCallback(
    (event: MouseEvent | TouchEvent) => {
      const target = event.target as HTMLElement | null;
      const droppedOnHandle =
        target?.closest(".react-flow__handle") ?? null;
      if (droppedOnHandle) {
        setPendingConnectionSource(null);
        return;
      }
      const clientX = "clientX" in event ? event.clientX : 0;
      const clientY = "clientY" in event ? event.clientY : 0;
      setDropSearch({ x: clientX, y: clientY });
    },
    [],
  );
  const closeDropSearch = useCallback(() => {
    setDropSearch(null);
    setPendingConnectionSource(null);
  }, []);
  const defaultEdgeOptions = useMemo(
    () => ({ type: isDragging ? "straight" : "smoothstep" }) as const,
    [isDragging],
  );

  const onSelectionChange = useCallback(
    ({ nodes: selected }: { nodes: Node[] }) => {
      setSelectedNodeIds(selected.map((node) => node.id));
    },
    [setSelectedNodeIds],
  );

  const handleNodesChange = useCallback(
    (changes: Parameters<typeof onNodesChange>[0]) => {
      const hasPosition = changes.some(
        (change) => change.type === "position" && change.position !== undefined,
      );
      const hasDraggingPosition = changes.some(
        (change) => change.type === "position" && change.dragging === true,
      );
      if (!editingAllowed || !hasPosition) {
        if (!isDragging) setAlignmentGuides([]);
        onNodesChange(changes);
        return;
      }
      // C5-11: position-only changes outside a mouse drag come from keyboard
      // nudges (React Flow's built-in arrow-key movement). The 5px step is
      // smaller than the drag snap threshold, so applying drag snapping would
      // pin the node back to the alignment guide and the arrows would feel
      // dead — nudges bypass snapping, and coalesce into one undo step
      // instead of one per press.
      const keyboardNudge =
        !isDragging &&
        !hasDraggingPosition &&
        changes.length > 0 &&
        changes.every(
          (change) =>
            change.type === "position" &&
            (change as { dragging?: boolean }).dragging !== true,
        );
      if (keyboardNudge) {
        beginKeyboardNudgeGroup();
        setAlignmentGuides([]);
        onNodesChange(changes);
        return;
      }
      const snapped = snapNodeChanges(nodes, changes);
      setAlignmentGuides(isDragging || hasDraggingPosition ? snapped.guides : []);
      onNodesChange(snapped.changes);
    },
    [editingAllowed, isDragging, nodes, onNodesChange],
  );

  const styledEdges = useMemo(
    () =>
      edges.map((edge) => {
        const taken = takenEdges[`${edge.source}->${edge.target}`] === true;
        const sourceDone = ["succeeded", "streaming", "running"].includes(
          nodeStatus[edge.source] ?? "",
        );
        const targetActive = ["running", "streaming"].includes(
          nodeStatus[edge.target] ?? "",
        );
        const targetDone = nodeStatus[edge.target] === "succeeded";
        const className = taken
          ? targetActive
            ? "edge-active edge-taken"
            : "edge-done edge-taken"
          : targetActive && sourceDone
            ? "edge-active"
            : sourceDone && targetDone
              ? "edge-done"
              : undefined;
        // C2-8: when the source node has produced a bounded output snapshot,
        // render the edge with the dataflow edge type so hovering shows the
        // flowing data summary. During node drag we keep the plain straight
        // edge to stay cheap and avoid path flicker.
        const hasFlow =
          !isDragging &&
          (nodeOutputs[edge.source] !== undefined ||
            (typeof edge.label === "string" && edge.label.length > 0));
        return {
          ...edge,
          type: hasFlow ? "dataflow" : edge.type,
          className: className,
          label: edge.label,
        };
      }),
    [edges, nodeStatus, takenEdges, isDragging, nodeOutputs],
  );

  const hiddenNodeIds = useMemo(
    () => new Set(canvas.groups.filter((group) => group.collapsed).flatMap((group) => group.node_ids)),
    [canvas.groups],
  );
  // Return the store nodes directly when no group is collapsed so React Flow's
  // node memoization is preserved during drag (mapping 100+ nodes to fresh
  // objects every frame forces every node component to re-render).
  const visibleNodes = useMemo(
    () =>
      hiddenNodeIds.size === 0
        ? nodes
        : nodes.map((node) => ({ ...node, hidden: hiddenNodeIds.has(node.id) })),
    [hiddenNodeIds, nodes],
  );
  // Virtualize off-screen nodes only past a scale where DOM count starts to
  // drag on steady-state paint/commit. Below the threshold we keep every node
  // mounted so fitView's async viewport sizing can never unmount nodes that
  // have not yet been framed (see C5-5 onlyRenderVisibleElements regression).
  const onlyRenderVisibleElements = nodes.length > VIRTUALIZATION_NODE_THRESHOLD;

  // C5-7: derive the queued-node set from the canvas edge graph + run status.
  // A node is queued when the workflow is active, the node has not started,
  // and at least one predecessor has started or finished. Computed while a run
  // is live or paused on approval so the downstream nodes waiting behind a
  // human/breakpoint still show the pending state. Stored as a per-node record
  // so BaseNode can select its own membership and preserve memoization.
  useEffect(() => {
    if (execStatus !== "running" && execStatus !== "waiting_approval") return;
    const startedOrFinished = new Set(
      Object.entries(nodeStatus)
        .filter(([, status]) => status === "running" || status === "streaming" || status === "succeeded")
        .map(([id]) => id),
    );
    if (startedOrFinished.size === 0) {
      setRunQueue({});
      return;
    }
    const inbound = new Map<string, string[]>();
    for (const edge of edges) {
      const list = inbound.get(edge.target) ?? [];
      list.push(edge.source);
      inbound.set(edge.target, list);
    }
    const queued: Record<string, true> = {};
    for (const node of nodes) {
      const status = nodeStatus[node.id];
      if (status && status !== "idle") continue;
      const preds = inbound.get(node.id) ?? [];
      if (preds.some((pred) => startedOrFinished.has(pred))) {
        queued[node.id] = true;
      }
    }
    setRunQueue(queued);
  }, [execStatus, nodeStatus, nodes, edges, setRunQueue]);

  const nodeColor = useCallback((node: Node) => {
    const colors: Record<string, string> = {
      start: "#34d399",
      agent: "#22d3ee",
      tool: "#8b5cf6",
      condition: "#fbbf24",
      rag: "#2dd4bf",
      human: "#fb7185",
      iteration: "#f97316",
      end: "#8b93a7",
    };
    return colors[node.type ?? ""] ?? "#64748b";
  }, []);

  return (
    <ReactFlow
      className="h-full w-full"
      key={workflowKey}
      nodes={visibleNodes}
      edges={styledEdges}
      onNodesChange={handleNodesChange}
      onEdgesChange={onEdgesChange}
      onConnect={onConnect}
      onConnectStart={onConnectStart}
      onConnectEnd={onConnectEnd}
      isValidConnection={isValidConnection}
      onSelectionChange={({ nodes: selected, edges: selectedEdges }: { nodes: Node[]; edges: Edge[] }) => {
        onSelectionChange({ nodes: selected });
        setSelectedEdgeId(selectedEdges[0]?.id ?? null);
      }}
      onNodeDragStart={startDragging}
      onNodeDragStop={stopDragging}
      onBeforeDelete={beforeDelete}
      onDelete={afterDelete}
      nodesDraggable={editingAllowed}
      nodesConnectable={editingAllowed}
      deleteKeyCode={editingAllowed ? EDIT_DELETE_KEYS : null}
      nodeTypes={nodeTypes}
      edgeTypes={dataFlowEdgeTypes}
      fitView
      fitViewOptions={FIT_VIEW_OPTIONS}
      proOptions={PRO_OPTIONS}
      defaultEdgeOptions={defaultEdgeOptions}
      onlyRenderVisibleElements={onlyRenderVisibleElements}
    >
      <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="var(--rf-bg-dots)" />
      <ViewportPortal>
        {canvas.groups.map((group) => (
          <CanvasGroupFrame
            key={group.id}
            group={group}
            onMove={(position) => moveCanvasGroup(group.id, position)}
            onDragStart={beginWorkflowHistoryGroup}
            onDragStop={endWorkflowHistoryGroup}
            onToggle={() => toggleCanvasGroup(group.id)}
            onRename={(name) => updateCanvasGroup(group.id, { name })}
            onRemove={() => removeCanvasGroup(group.id)}
          />
        ))}
        {canvas.notes.map((note) => (
          <CanvasNote
            key={note.id}
            note={note}
            onMove={(position) => moveCanvasNote(note.id, { position })}
            onDragStart={beginWorkflowHistoryGroup}
            onDragStop={endWorkflowHistoryGroup}
            onTextChange={(text) => updateCanvasNote(note.id, { text })}
            onRemove={() => removeCanvasNote(note.id)}
          />
        ))}
        {alignmentGuides.map((guide) => (
          <div
            key={guide.id}
            data-testid="alignment-guide"
            aria-hidden="true"
            className="pointer-events-none absolute z-30 bg-pulse/80"
            style={
              guide.orientation === "vertical"
                ? {
                    left: guide.position,
                    top: guide.start,
                    width: 1,
                    height: Math.max(1, guide.end - guide.start),
                  }
                : {
                    left: guide.start,
                    top: guide.position,
                    width: Math.max(1, guide.end - guide.start),
                    height: 1,
                  }
            }
          />
        ))}
      </ViewportPortal>
      <Controls position="bottom-left" showInteractive={false} />
      {!isDragging && (
        <MiniMap
          nodeColor={nodeColor}
          maskColor="rgba(4,6,12,0.82)"
          position="bottom-right"
          pannable
        />
      )}
      {dropSearch && (
        <DropNodeSearch
          clientPosition={dropSearch}
          sourceNodeId={pendingConnectionSource}
          screenToFlowPosition={screenToFlowPosition}
          onClose={closeDropSearch}
        />
      )}
    </ReactFlow>
  );
});

export function FlowCanvas() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  );
}
