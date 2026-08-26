import type { Connection, Edge } from "@xyflow/react";

import type { FlowNode } from "@/stores/workflowStore";
import type { NodeType } from "@/types/dsl";

/**
 * C5-7: canvas connection validation shared by the drag-time target highlight
 * and the `isValidConnection` gate. Rules mirror the backend `validate_dsl`
 * structural checks (start has no inbound, end has no outbound, no self-loop,
 * no duplicate edge) so an invalid connection can never be committed and the
 * editor gets immediate feedback while dragging.
 */
export function canConnect(
  connection: Connection | Edge,
  nodes: FlowNode[],
  edges: Edge[],
): boolean {
  const sourceId = connection.source;
  const targetId = connection.target;
  if (!sourceId || !targetId) return false;
  if (sourceId === targetId) return false;

  const source = nodes.find((node) => node.id === sourceId);
  const target = nodes.find((node) => node.id === targetId);
  if (!source || !target) return false;

  const sourceType = source.data.nodeType;
  const targetType = target.data.nodeType;
  if (sourceType === "end") return false;
  if (targetType === "start") return false;

  if (sourceType === "condition") {
    const handleId = connection.sourceHandle ?? undefined;
    if (handleId && !conditionBranchIds(source.data.config).has(handleId)) {
      return false;
    }
  }

  if (edgeExists(edges, connection)) return false;

  return true;
}

/**
 * C5-7: whether a target node is a legal drop for an ongoing connection from
 * `sourceType`. Used by BaseNode to highlight legal targets while the user
 * drags a connection line. This is a coarse structural check (the final gate
 * is `canConnect`, which also rejects duplicates and bad condition handles).
 */
export function isLegalTarget(
  sourceType: NodeType | null,
  targetType: NodeType,
): boolean {
  if (!sourceType) return false;
  if (sourceType === "end") return false;
  if (targetType === "start") return false;
  return true;
}

export function conditionBranchIds(
  config: Record<string, unknown>,
): Set<string> {
  const branches = Array.isArray(config.branches) ? config.branches : [];
  const ids = branches
    .map((branch) =>
      branch && typeof branch === "object"
        ? (branch as { id?: unknown }).id
        : undefined,
    )
    .filter((value): value is string => typeof value === "string" && value.length > 0);
  const fallback =
    typeof config.default_branch === "string" && config.default_branch
      ? config.default_branch
      : "else";
  const set = new Set(ids);
  set.add(fallback);
  if (!set.has("else")) set.add("else");
  return set;
}

function edgeExists(edges: Edge[], connection: Connection | Edge): boolean {
  return edges.some(
    (edge) =>
      edge.source === connection.source &&
      edge.target === connection.target &&
      (edge.sourceHandle ?? null) === (connection.sourceHandle ?? null) &&
      (edge.targetHandle ?? null) === (connection.targetHandle ?? null),
  );
}
