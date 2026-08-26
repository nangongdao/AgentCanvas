import type { NodeChange, XYPosition } from "@xyflow/react";
import dagre from "@dagrejs/dagre";

export const DEFAULT_NODE_WIDTH = 168;
export const DEFAULT_NODE_HEIGHT = 72;
export const SNAP_THRESHOLD = 10;
export const MIN_DISTRIBUTION_GAP = 24;
// Snap guides are a precise-alignment aid for small canvases. Computing them
// is O(stationary) per pointermove, so past this node count we skip the
// calculation to keep large-canvas drag latency under the paint SLO.
export const SNAP_GUIDE_NODE_LIMIT = 60;

export type LayoutDirection = "LR" | "TB";
export type AlignmentMode =
  | "left"
  | "center-x"
  | "right"
  | "top"
  | "center-y"
  | "bottom";
export type DistributionMode = "horizontal" | "vertical";

export interface LayoutNode {
  id: string;
  position: XYPosition;
  width?: number | null;
  height?: number | null;
  measured?: { width?: number; height?: number };
}

export interface LayoutEdge {
  source: string;
  target: string;
}

export interface AlignmentGuide {
  id: string;
  orientation: "vertical" | "horizontal";
  position: number;
  start: number;
  end: number;
}

function positiveDimension(value: number | null | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : fallback;
}

function nodeSize(node: LayoutNode) {
  return {
    width: positiveDimension(
      node.measured?.width,
      positiveDimension(node.width, DEFAULT_NODE_WIDTH),
    ),
    height: positiveDimension(
      node.measured?.height,
      positiveDimension(node.height, DEFAULT_NODE_HEIGHT),
    ),
  };
}

export function layoutWorkflow(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
  direction: LayoutDirection = "LR",
): Record<string, XYPosition> {
  if (nodes.length === 0) return {};

  const graph = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  graph.setGraph({
    acyclicer: "greedy",
    marginx: 80,
    marginy: 80,
    nodesep: 44,
    rankdir: direction,
    ranker: "network-simplex",
    ranksep: 120,
  });

  const nodeIds = new Set(nodes.map((node) => node.id));
  for (const node of nodes) {
    const size = nodeSize(node);
    graph.setNode(node.id, size);
  }
  for (const edge of edges) {
    if (nodeIds.has(edge.source) && nodeIds.has(edge.target)) {
      graph.setEdge(edge.source, edge.target);
    }
  }

  dagre.layout(graph);

  return Object.fromEntries(
    nodes.map((node) => {
      const size = nodeSize(node);
      const laidOut = graph.node(node.id);
      return [
        node.id,
        {
          x: Math.round((laidOut?.x ?? node.position.x + size.width / 2) - size.width / 2),
          y: Math.round((laidOut?.y ?? node.position.y + size.height / 2) - size.height / 2),
        },
      ];
    }),
  );
}

export function alignNodes(
  nodes: LayoutNode[],
  selectedIds: string[],
  mode: AlignmentMode,
): Record<string, XYPosition> {
  const selected = nodes.filter((node) => selectedIds.includes(node.id));
  if (selected.length < 2) return {};

  const sizes = new Map(selected.map((node) => [node.id, nodeSize(node)]));
  const left = Math.min(...selected.map((node) => node.position.x));
  const right = Math.max(
    ...selected.map((node) => node.position.x + (sizes.get(node.id)?.width ?? DEFAULT_NODE_WIDTH)),
  );
  const top = Math.min(...selected.map((node) => node.position.y));
  const bottom = Math.max(
    ...selected.map((node) => node.position.y + (sizes.get(node.id)?.height ?? DEFAULT_NODE_HEIGHT)),
  );
  const centerX = (left + right) / 2;
  const centerY = (top + bottom) / 2;

  return Object.fromEntries(
    selected.map((node) => {
      const size = sizes.get(node.id) ?? {
        width: DEFAULT_NODE_WIDTH,
        height: DEFAULT_NODE_HEIGHT,
      };
      let position = node.position;
      if (mode === "left") position = { ...position, x: left };
      if (mode === "center-x") position = { ...position, x: Math.round(centerX - size.width / 2) };
      if (mode === "right") position = { ...position, x: right - size.width };
      if (mode === "top") position = { ...position, y: top };
      if (mode === "center-y") position = { ...position, y: Math.round(centerY - size.height / 2) };
      if (mode === "bottom") position = { ...position, y: bottom - size.height };
      return [node.id, { x: Math.round(position.x), y: Math.round(position.y) }];
    }),
  );
}

export function distributeNodes(
  nodes: LayoutNode[],
  selectedIds: string[],
  mode: DistributionMode,
): Record<string, XYPosition> {
  const selected = nodes
    .filter((node) => selectedIds.includes(node.id))
    .sort((left, right) =>
      mode === "horizontal"
        ? left.position.x - right.position.x
        : left.position.y - right.position.y,
    );
  if (selected.length < 3) return {};

  const sizes = selected.map(nodeSize);
  const start = mode === "horizontal" ? selected[0].position.x : selected[0].position.y;
  const lastIndex = selected.length - 1;
  const lastNode = selected[lastIndex];
  const lastSize = sizes[lastIndex];
  const end =
    mode === "horizontal"
      ? lastNode.position.x + lastSize.width
      : lastNode.position.y + lastSize.height;
  const totalSize = sizes.reduce(
    (sum, size) => sum + (mode === "horizontal" ? size.width : size.height),
    0,
  );
  const availableGap = (end - start - totalSize) / lastIndex;
  if (!Number.isFinite(availableGap)) return {};
  // Keep a usable breathing room when the current span is too tight. This
  // also prevents a distribution command from producing an un-draggable row.
  const gap = Math.max(MIN_DISTRIBUTION_GAP, availableGap);

  let cursor = start;
  return Object.fromEntries(
    selected.map((node, index) => {
      const size = sizes[index];
      const position =
        mode === "horizontal"
          ? { x: Math.round(cursor), y: node.position.y }
          : { x: node.position.x, y: Math.round(cursor) };
      cursor += (mode === "horizontal" ? size.width : size.height) + gap;
      return [node.id, position];
    }),
  );
}

function closestSnap(
  values: Array<{ delta: number; position: number; id: string }>,
): { delta: number; position: number; id: string } | null {
  const candidate = values
    .filter((value) => Math.abs(value.delta) <= SNAP_THRESHOLD)
    .sort((left, right) => Math.abs(left.delta) - Math.abs(right.delta))[0];
  return candidate ?? null;
}

export function snapNodeChanges(
  nodes: LayoutNode[],
  changes: NodeChange[],
): { changes: NodeChange[]; guides: AlignmentGuide[] } {
  const movingChanges = changes.filter(
    (change): change is Extract<NodeChange, { type: "position" }> =>
      change.type === "position" && change.position !== undefined,
  );
  if (movingChanges.length === 0) return { changes, guides: [] };

  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const movingIds = new Set(movingChanges.map((change) => change.id));
  const moving = movingChanges
    .map((change) => {
      const node = nodeMap.get(change.id);
      return node ? { node, position: change.position! } : null;
    })
    .filter((entry): entry is { node: LayoutNode; position: XYPosition } => entry !== null);
  const stationary = nodes.filter((node) => !movingIds.has(node.id));
  if (moving.length === 0 || stationary.length === 0) return { changes, guides: [] };
  // Skip snap-guide computation on large canvases to keep drag paint latency
  // bounded; the aid targets precise alignment on small layouts.
  if (stationary.length + moving.length > SNAP_GUIDE_NODE_LIMIT) {
    return { changes, guides: [] };
  }

  const movingBounds = moving.reduce(
    (bounds, entry) => {
      const size = nodeSize(entry.node);
      return {
        left: Math.min(bounds.left, entry.position.x),
        right: Math.max(bounds.right, entry.position.x + size.width),
        top: Math.min(bounds.top, entry.position.y),
        bottom: Math.max(bounds.bottom, entry.position.y + size.height),
      };
    },
    { left: Infinity, right: -Infinity, top: Infinity, bottom: -Infinity },
  );
  const movingCenterX = (movingBounds.left + movingBounds.right) / 2;
  const movingCenterY = (movingBounds.top + movingBounds.bottom) / 2;

  const xCandidates: Array<{ delta: number; position: number; id: string }> = [];
  const yCandidates: Array<{ delta: number; position: number; id: string }> = [];
  for (const node of stationary) {
    const size = nodeSize(node);
    const nodeRight = node.position.x + size.width;
    const nodeBottom = node.position.y + size.height;
    xCandidates.push(
      { delta: node.position.x - movingBounds.left, position: node.position.x, id: `${node.id}-left` },
      { delta: nodeRight - movingBounds.right, position: nodeRight, id: `${node.id}-right` },
      { delta: node.position.x + size.width / 2 - movingCenterX, position: node.position.x + size.width / 2, id: `${node.id}-center` },
    );
    yCandidates.push(
      { delta: node.position.y - movingBounds.top, position: node.position.y, id: `${node.id}-top` },
      { delta: nodeBottom - movingBounds.bottom, position: nodeBottom, id: `${node.id}-bottom` },
      { delta: node.position.y + size.height / 2 - movingCenterY, position: node.position.y + size.height / 2, id: `${node.id}-center` },
    );
  }

  const xSnap = closestSnap(xCandidates);
  const ySnap = closestSnap(yCandidates);
  const dx = xSnap?.delta ?? 0;
  const dy = ySnap?.delta ?? 0;
  // Keep the guide visible even when the node is already exactly aligned.
  // A zero delta means the snap target was reached, not that no target exists.
  if (!xSnap && !ySnap) return { changes, guides: [] };

  const guides: AlignmentGuide[] = [];
  if (xSnap) {
    const guideTop = stationary.reduce(
      (min, node) => Math.min(min, node.position.y),
      movingBounds.top,
    ) - 16;
    const guideBottom = stationary.reduce(
      (max, node) => Math.max(max, node.position.y + nodeSize(node).height),
      movingBounds.bottom,
    ) + 16;
    guides.push({
      id: `v-${xSnap.id}`,
      orientation: "vertical",
      position: xSnap.position,
      start: guideTop,
      end: guideBottom,
    });
  }
  if (ySnap) {
    const guideLeft = stationary.reduce(
      (min, node) => Math.min(min, node.position.x),
      movingBounds.left,
    ) - 16;
    const guideRight = stationary.reduce(
      (max, node) => Math.max(max, node.position.x + nodeSize(node).width),
      movingBounds.right,
    ) + 16;
    guides.push({
      id: `h-${ySnap.id}`,
      orientation: "horizontal",
      position: ySnap.position,
      start: guideLeft,
      end: guideRight,
    });
  }

  return {
    changes: changes.map((change) => {
      if (change.type !== "position" || change.position === undefined) return change;
      return {
        ...change,
        position: { x: change.position.x + dx, y: change.position.y + dy },
      };
    }),
    guides,
  };
}
