import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { backendUrl } from "@/api/backendOrigin";

import { ApiError } from "@/api/client";
import {
  acquireWorkflowLock,
  heartbeatWorkflowCollaboration,
  leaveWorkflowCollaboration,
  leaveWorkflowCollaborationKeepalive,
  type CollaborationSnapshotDTO,
  workflowCollaborationStreamUrl,
} from "@/api/endpoints/collaboration";
import { registerSessionCleanup } from "@/features/auth/sessionCleanup";

const HEARTBEAT_INTERVAL_MS = 10_000;
const CLIENT_ID_STORAGE_KEY = "agentcanvas:collaboration-client-id";
const CLIENT_ID_PATTERN = /^[A-Za-z0-9._:-]{8,128}$/;
const pendingLeaves = new Map<string, number>();
let pageClientId: string | null = null;

export type CollaborationConnection =
  | "idle"
  | "connecting"
  | "online"
  | "offline";

export interface WorkflowCollaborationState {
  workflowId: string | null;
  clientId: string;
  snapshot: CollaborationSnapshotDTO | null;
  connection: CollaborationConnection;
  canEdit: boolean;
  editingAllowed: boolean;
  takeoverBusy: boolean;
  takeover: () => Promise<void>;
  retry: () => void;
}

function makeClientId(): string {
  if (pageClientId) return pageClientId;
  try {
    const stored = globalThis.sessionStorage?.getItem(CLIENT_ID_STORAGE_KEY);
    if (stored && CLIENT_ID_PATTERN.test(stored)) {
      pageClientId = stored;
      return pageClientId;
    }
  } catch {
    // Storage can be unavailable in hardened/private browser contexts.
  }
  const generated = globalThis.crypto?.randomUUID
    ? globalThis.crypto.randomUUID()
    : `tab-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  pageClientId = generated;
  try {
    globalThis.sessionStorage?.setItem(CLIENT_ID_STORAGE_KEY, generated);
  } catch {
    // The in-memory identity still preserves stability for this document.
  }
  return pageClientId;
}

function messageFrom(error: unknown): string {
  if (error instanceof ApiError && typeof error.detail === "string") {
    return error.detail;
  }
  return error instanceof Error ? error.message : String(error);
}

export function useWorkflowCollaboration(
  roleCanEdit: boolean,
  notify: (message: string) => void,
): WorkflowCollaborationState {
  const { workflowId: routeId = "new" } = useParams();
  const workflowId = routeId === "new" ? null : routeId;
  const clientIdRef = useRef(makeClientId());
  const leaseIdRef = useRef<string | null>(null);
  const streamOnlineRef = useRef(false);
  const generationRef = useRef(0);
  const [snapshot, setSnapshot] = useState<CollaborationSnapshotDTO | null>(null);
  const [connection, setConnection] = useState<CollaborationConnection>(
    workflowId ? "connecting" : "idle",
  );
  const [takeoverBusy, setTakeoverBusy] = useState(false);
  const [retryGeneration, setRetryGeneration] = useState(0);

  const applySnapshot = useCallback(
    (next: CollaborationSnapshotDTO, streamConfirmed: boolean) => {
      const lostLease = Boolean(leaseIdRef.current) && !next.lease_id;
      leaseIdRef.current = next.lease_id;
      setSnapshot(next);
      if (streamConfirmed) {
        streamOnlineRef.current = true;
        setConnection("online");
      }
      if (lostLease) notify("编辑权已转移，当前画布已切换为只读");
    },
    [notify],
  );

  useEffect(() => {
    if (!workflowId) {
      generationRef.current += 1;
      leaseIdRef.current = null;
      streamOnlineRef.current = false;
      setSnapshot(null);
      setConnection("idle");
      return;
    }

    const generation = ++generationRef.current;
    const clientId = clientIdRef.current;
    const leaveKey = `${workflowId}:${clientId}`;
    const pendingLeave = pendingLeaves.get(leaveKey);
    if (pendingLeave !== undefined) {
      window.clearTimeout(pendingLeave);
      pendingLeaves.delete(leaveKey);
    }
    let active = true;
    let heartbeatTimer: number | undefined;
    let source: EventSource | undefined;
    streamOnlineRef.current = false;
    leaseIdRef.current = null;
    setSnapshot(null);
    setConnection("connecting");

    const isCurrent = () => active && generationRef.current === generation;

    const stopLiveUpdates = () => {
      if (heartbeatTimer !== undefined) {
        window.clearInterval(heartbeatTimer);
        heartbeatTimer = undefined;
      }
      source?.close();
      source = undefined;
      streamOnlineRef.current = false;
    };

    const syncHeartbeat = async () => {
      try {
        const leaseId = streamOnlineRef.current ? leaseIdRef.current : null;
        const next = await heartbeatWorkflowCollaboration(
          workflowId,
          clientId,
          leaseId,
        );
        if (isCurrent()) applySnapshot(next, false);
      } catch (error) {
        if (!isCurrent()) return;
        if (error instanceof ApiError && [403, 409].includes(error.status)) {
          leaseIdRef.current = null;
        }
        streamOnlineRef.current = false;
        setConnection("offline");
      }
    };

    const openStream = () => {
      source = new EventSource(
        backendUrl(workflowCollaborationStreamUrl(workflowId, clientId)),
        { withCredentials: true },
      );
      source.addEventListener("snapshot", (event) => {
        if (!isCurrent()) return;
        try {
          applySnapshot(
            JSON.parse((event as MessageEvent<string>).data) as CollaborationSnapshotDTO,
            true,
          );
        } catch {
          streamOnlineRef.current = false;
          setConnection("offline");
        }
      });
      source.onerror = () => {
        if (!isCurrent()) return;
        streamOnlineRef.current = false;
        setConnection("offline");
      };
    };

    const initialize = async () => {
      try {
        let initial = await heartbeatWorkflowCollaboration(workflowId, clientId);
        if (roleCanEdit && initial.can_edit) {
          try {
            initial = await acquireWorkflowLock(workflowId, clientId);
          } catch (error) {
            if (!(error instanceof ApiError) || error.status !== 409) throw error;
          }
        }
        if (!isCurrent()) return;
        applySnapshot(initial, false);
        openStream();
        heartbeatTimer = window.setInterval(
          () => void syncHeartbeat(),
          HEARTBEAT_INTERVAL_MS,
        );
      } catch (error) {
        if (!isCurrent()) return;
        setConnection("offline");
        notify(`协作连接失败：${messageFrom(error)}`);
      }
    };

    const onPageHide = () => {
      leaveWorkflowCollaborationKeepalive(workflowId, clientId);
    };
    window.addEventListener("pagehide", onPageHide);
    const initialization = initialize();
    const unregisterSessionCleanup = registerSessionCleanup(async () => {
      active = false;
      stopLiveUpdates();
      setConnection("offline");
      await initialization;
      await leaveWorkflowCollaboration(workflowId, clientId);
    });
    void initialization;

    return () => {
      active = false;
      stopLiveUpdates();
      unregisterSessionCleanup();
      window.removeEventListener("pagehide", onPageHide);
      const leaveTimer = window.setTimeout(() => {
        void leaveWorkflowCollaboration(workflowId, clientId).catch(() => undefined);
        if (pendingLeaves.get(leaveKey) === leaveTimer) {
          pendingLeaves.delete(leaveKey);
        }
      }, 0);
      pendingLeaves.set(leaveKey, leaveTimer);
    };
  }, [applySnapshot, notify, retryGeneration, roleCanEdit, workflowId]);

  const takeover = useCallback(async () => {
    if (!workflowId || !roleCanEdit || !snapshot?.can_edit || takeoverBusy) return;
    setTakeoverBusy(true);
    try {
      const next = await acquireWorkflowLock(
        workflowId,
        clientIdRef.current,
        { takeover: true },
      );
      applySnapshot(next, streamOnlineRef.current);
      notify("已取得工作流编辑权");
    } catch (error) {
      notify(`无法取得编辑权：${messageFrom(error)}`);
    } finally {
      setTakeoverBusy(false);
    }
  }, [applySnapshot, notify, roleCanEdit, snapshot?.can_edit, takeoverBusy, workflowId]);

  const canEdit = workflowId === null ? roleCanEdit : snapshot?.can_edit === true;

  const editingAllowed =
    workflowId === null
      ? roleCanEdit
      : roleCanEdit &&
        canEdit &&
        connection === "online" &&
        snapshot?.lock?.owned_by_self === true &&
        Boolean(snapshot.lease_id);

  return {
    workflowId,
    clientId: clientIdRef.current,
    snapshot,
    connection,
    canEdit,
    editingAllowed,
    takeoverBusy,
    takeover,
    retry: () => setRetryGeneration((value) => value + 1),
  };
}
