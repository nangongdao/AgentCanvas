import { useCallback, useEffect, useRef, useState } from "react";
import { useBlocker, useLocation, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "@/api/client";
import {
  createWorkflow,
  getWorkflow,
  listWorkflowVersions,
  mergeWorkflowChanges,
  updateWorkflow,
  type WorkflowDTO,
} from "@/api/endpoints/workflows";
import { useWorkflowStore } from "@/stores/workflowStore";

export type SaveState = "idle" | "saving" | "saved" | "error" | "conflict";

export interface WorkflowConflict {
  remote: WorkflowDTO;
  localVersion: number;
  baseVersionId: string;
  mergeConflicts: string[];
}

interface SaveOptions {
  force?: boolean;
  silent?: boolean;
  changeSummary?: string;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string"
      ? error.detail
      : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

function announceWorkflowChange() {
  window.dispatchEvent(new Event("agentcanvas:workflows-changed"));
}

export function useWorkflowPersistence(
  notify: (message: string) => void,
  editingAllowed = true,
) {
  const { workflowId: routeId = "new" } = useParams();
  const workflowId = routeId === "new" ? null : routeId;
  const location = useLocation();
  const newWorkflowRequest = workflowId === null ? location.key : null;
  const navigate = useNavigate();
  const dirty = useWorkflowStore((state) => state.dirty);
  const changeId = useWorkflowStore((state) => state.changeId);
  const [loading, setLoading] = useState(true);
  const [routeError, setRouteError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [conflict, setConflict] = useState<WorkflowConflict | null>(null);
  const savePromise = useRef<Promise<string | null> | null>(null);
  const skipNextLoad = useRef<string | null>(null);
  const allowNavigation = useRef(false);

  const loadWorkflow = useCallback(async (id: string) => {
    setLoading(true);
    setRouteError(null);
    setConflict(null);
    try {
      const workflow = await getWorkflow(id);
      useWorkflowStore.getState().loadDSL(workflow.dsl, workflow.version);
      setSaveState("saved");
    } catch (error) {
      setRouteError(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!workflowId) {
      useWorkflowStore.getState().newWorkflow();
      setConflict(null);
      setRouteError(null);
      setSaveState("idle");
      setLoading(false);
      return;
    }
    if (skipNextLoad.current === workflowId) {
      skipNextLoad.current = null;
      setRouteError(null);
      setLoading(false);
      return;
    }
    void loadWorkflow(workflowId);
  }, [workflowId, loadWorkflow, newWorkflowRequest]);

  const persist = useCallback(
    async ({ force = false, silent = false, changeSummary }: SaveOptions = {}) => {
      if (savePromise.current) return savePromise.current;
      if (!editingAllowed) {
        notify("当前未持有工作流编辑权，无法保存更改");
        return null;
      }
      if (conflict && !force) {
        setSaveState("conflict");
        if (!silent) notify("请先处理版本冲突");
        return null;
      }

      const snapshot = useWorkflowStore.getState();
      if (workflowId && !snapshot.dirty && !force) {
        if (!silent) notify("工作流已是最新状态");
        return workflowId;
      }

      setSaveState("saving");
      const task = (async (): Promise<string | null> => {
        try {
          const dsl = snapshot.toDSL();
          const saved = workflowId
            ? await updateWorkflow(workflowId, {
                name: snapshot.name,
                dsl,
                ...(force ? {} : { version: snapshot.version }),
                change_summary:
                  changeSummary ?? (silent ? "Autosave" : "Manual save"),
              })
            : await createWorkflow(snapshot.name, dsl);

          snapshot.markClean(saved.version, snapshot.changeId);
          setConflict(null);
          setSaveState("saved");
          announceWorkflowChange();

          if (!workflowId) {
            skipNextLoad.current = saved.id;
            allowNavigation.current = true;
            navigate(`/workflows/${saved.id}`, { replace: true });
            window.setTimeout(() => {
              allowNavigation.current = false;
            }, 0);
          }
          if (!silent) notify(force ? "已覆盖远端版本" : "工作流已保存");
          return saved.id;
        } catch (error) {
          if (error instanceof ApiError && error.status === 409 && workflowId) {
            try {
              const [remote, versions] = await Promise.all([
                getWorkflow(workflowId),
                listWorkflowVersions(workflowId),
              ]);
              const base = versions.find(
                (version) => version.number === snapshot.version,
              );
              if (!base) {
                throw new Error(
                  `找不到本地基准版本 v${snapshot.version} 的不可变快照`,
                );
              }
              setConflict({
                remote,
                localVersion: snapshot.version,
                baseVersionId: base.id,
                mergeConflicts: [],
              });
              setSaveState("conflict");
              notify("检测到远端版本更新，请选择保留方式");
            } catch (loadError) {
              setSaveState("error");
              notify(`无法读取冲突版本：${errorMessage(loadError)}`);
            }
          } else {
            setSaveState("error");
            notify(`保存失败：${errorMessage(error)}`);
          }
          return null;
        } finally {
          savePromise.current = null;
        }
      })();

      savePromise.current = task;
      return task;
    },
    [conflict, editingAllowed, navigate, notify, workflowId],
  );

  useEffect(() => {
    if (
      !editingAllowed ||
      !dirty ||
      loading ||
      conflict ||
      saveState === "saving"
    )
      return;
    setSaveState("idle");
    const timer = window.setTimeout(() => {
      void persist({ silent: true });
    }, 800);
    return () => window.clearTimeout(timer);
  }, [changeId, conflict, dirty, editingAllowed, loading, persist, saveState]);

  useEffect(() => {
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!useWorkflowStore.getState().dirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, []);

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => {
      const nextState = nextLocation.state as
        | { newWorkflowCommand?: unknown }
        | null;
      const leavesCurrentDraft =
        currentLocation.pathname !== nextLocation.pathname ||
        nextState?.newWorkflowCommand !== undefined;
      return leavesCurrentDraft && dirty && !allowNavigation.current;
    },
  );

  const openWorkflow = useCallback(
    async (id: string | null): Promise<boolean> => {
      const target = id ? `/workflows/${id}` : "/workflows/new";
      if (window.location.pathname === target) return true;
      if (useWorkflowStore.getState().dirty) {
        const savedId = await persist({ silent: true });
        if (!savedId) return false;
      }
      allowNavigation.current = true;
      skipNextLoad.current = null;
      navigate(target);
      window.setTimeout(() => {
        allowNavigation.current = false;
      }, 0);
      return true;
    },
    [navigate, persist],
  );

  const openWithoutSaving = useCallback(
    (id: string | null) => {
      allowNavigation.current = true;
      skipNextLoad.current = null;
      navigate(id ? `/workflows/${id}` : "/workflows/new");
      window.setTimeout(() => {
        allowNavigation.current = false;
      }, 0);
    },
    [navigate],
  );

  const reloadConflict = useCallback(() => {
    if (!conflict) return;
    useWorkflowStore.getState().loadDSL(conflict.remote.dsl, conflict.remote.version);
    setConflict(null);
    setSaveState("saved");
    notify("已载入远端版本");
  }, [conflict, notify]);

  const overwriteConflict = useCallback(async () => {
    await persist({ force: true });
  }, [persist]);

  const mergeConflict = useCallback(async () => {
    if (!workflowId || !conflict) return;
    const snapshot = useWorkflowStore.getState();
    setSaveState("saving");
    try {
      const result = await mergeWorkflowChanges(workflowId, {
        base_version_id: conflict.baseVersionId,
        remote_version: conflict.remote.version,
        local_name: snapshot.name,
        local_dsl: snapshot.toDSL(),
        change_summary: `Merge local v${conflict.localVersion} changes`,
      });
      if (result.status === "conflict") {
        const mergeConflicts = result.conflicts.map((item) => item.path);
        setConflict({ ...conflict, mergeConflicts });
        setSaveState("conflict");
        notify(`自动合并仍有 ${mergeConflicts.length} 处冲突，未写入服务器`);
        return;
      }
      if (result.saved_version === null) {
        throw new Error("merge response did not include a saved version");
      }
      useWorkflowStore.getState().loadDSL(result.dsl, result.saved_version);
      setConflict(null);
      setSaveState("saved");
      announceWorkflowChange();
      notify("本地与远端修改已合并保存");
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        try {
          const remote = await getWorkflow(workflowId);
          setConflict({ ...conflict, remote, mergeConflicts: [] });
          setSaveState("conflict");
          notify("合并期间远端再次更新，请重新尝试");
        } catch (loadError) {
          setSaveState("error");
          notify(`无法读取最新远端版本：${errorMessage(loadError)}`);
        }
      } else {
        setSaveState("conflict");
        notify(`自动合并失败：${errorMessage(error)}`);
      }
    }
  }, [conflict, notify, workflowId]);

  const cancelBlockedNavigation = useCallback(() => {
    if (blocker.state === "blocked") blocker.reset();
  }, [blocker]);

  const discardBlockedNavigation = useCallback(() => {
    if (blocker.state !== "blocked") return;
    allowNavigation.current = true;
    blocker.proceed();
    window.setTimeout(() => {
      allowNavigation.current = false;
    }, 0);
  }, [blocker]);

  const saveAndContinue = useCallback(async () => {
    if (blocker.state !== "blocked") return;
    const savedId = await persist({ silent: true });
    if (savedId) blocker.proceed();
  }, [blocker, persist]);

  return {
    workflowId,
    loading,
    routeError,
    saveState,
    conflict,
    persist,
    openWorkflow,
    openWithoutSaving,
    reloadConflict,
    mergeConflict,
    overwriteConflict,
    retryLoad: () => workflowId && void loadWorkflow(workflowId),
    reloadCurrent: async () => {
      if (workflowId) await loadWorkflow(workflowId);
    },
    navigationBlocked: blocker.state === "blocked",
    cancelBlockedNavigation,
    discardBlockedNavigation,
    saveAndContinue,
  };
}
