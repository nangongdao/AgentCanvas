/** Workflow presence and soft-lock API endpoints. */

import { apiGet, apiSend } from "@/api/client";
import { withQuery } from "@/api/pagination";

export interface CollaborationPresenceDTO {
  client_id: string;
  subject: string;
  last_seen_at: string;
  expires_at: string;
  is_self: boolean;
}

export interface CollaborationLockDTO {
  client_id: string;
  subject: string;
  acquired_at: string;
  expires_at: string;
  owned_by_self: boolean;
}

export interface CollaborationSnapshotDTO {
  workflow_id: string;
  revision: number;
  can_edit: boolean;
  participants: CollaborationPresenceDTO[];
  lock: CollaborationLockDTO | null;
  lease_id: string | null;
  /** Collaboration coordination backend: memory | redis | unavailable. */
  backend?: string;
  /** True when shared Redis is required but currently unusable. */
  read_only?: boolean;
}

function basePath(workflowId: string): string {
  return `/api/workflows/${workflowId}/collaboration`;
}

export function getWorkflowCollaboration(
  workflowId: string,
  clientId: string,
): Promise<CollaborationSnapshotDTO> {
  return apiGet<CollaborationSnapshotDTO>(
    withQuery(basePath(workflowId), { client_id: clientId }),
  );
}

export function heartbeatWorkflowCollaboration(
  workflowId: string,
  clientId: string,
  leaseId?: string | null,
): Promise<CollaborationSnapshotDTO> {
  return apiSend<CollaborationSnapshotDTO>(
    `${basePath(workflowId)}/heartbeat`,
    "POST",
    { client_id: clientId, ...(leaseId ? { lease_id: leaseId } : {}) },
  );
}

export function acquireWorkflowLock(
  workflowId: string,
  clientId: string,
  options: { takeover?: boolean; leaseId?: string | null } = {},
): Promise<CollaborationSnapshotDTO> {
  return apiSend<CollaborationSnapshotDTO>(
    `${basePath(workflowId)}/lock`,
    "PUT",
    {
      client_id: clientId,
      takeover: options.takeover ?? false,
      ...(options.leaseId ? { lease_id: options.leaseId } : {}),
    },
  );
}

export function leaveWorkflowCollaboration(
  workflowId: string,
  clientId: string,
): Promise<CollaborationSnapshotDTO> {
  return apiSend<CollaborationSnapshotDTO>(
    `${basePath(workflowId)}/presence/${encodeURIComponent(clientId)}`,
    "DELETE",
  );
}

export function leaveWorkflowCollaborationKeepalive(
  workflowId: string,
  clientId: string,
): void {
  void fetch(
    `${basePath(workflowId)}/presence/${encodeURIComponent(clientId)}`,
    {
      method: "DELETE",
      credentials: "include",
      headers: { Accept: "application/json" },
      keepalive: true,
    },
  ).catch(() => undefined);
}

export function workflowCollaborationStreamUrl(
  workflowId: string,
  clientId: string,
): string {
  return withQuery(`${basePath(workflowId)}/stream`, {
    client_id: clientId,
  });
}
