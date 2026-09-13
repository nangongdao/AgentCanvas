import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { getOverview, type OverviewDTO } from "@/api/endpoints/overview";
import {
  getProjectQuotas,
  listProjects,
  type ProjectDTO,
  type ProjectQuotaDTO,
} from "@/api/endpoints/projectQuotas";
import { useAuth } from "@/features/auth/AuthProvider";

/** Wrap even a null/undefined rejection so failure never looks like success. */
export interface OverviewRequestFailure {
  cause: unknown;
}

/** Owns project scope and request generations for every overview consumer.
 * A response may only update the project generation that requested it. */
export function useOverviewData() {
  const { ready } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [projects, setProjects] = useState<ProjectDTO[]>([]);
  const [discoveryAttempt, setDiscoveryAttempt] = useState(0);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [overview, setOverview] = useState<OverviewDTO | null>(null);
  const [quota, setQuota] = useState<ProjectQuotaDTO | null>(null);
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [loading, setLoading] = useState(true);
  const [projectError, setProjectError] = useState<OverviewRequestFailure | null>(null);
  const [overviewError, setOverviewError] = useState<OverviewRequestFailure | null>(null);
  const [quotaError, setQuotaError] = useState<OverviewRequestFailure | null>(null);
  const requestGeneration = useRef(0);
  const requestedProjectId = searchParams.get("project_id") ?? "";
  const resolvedProjectId = projects.some((project) => project.id === requestedProjectId)
    ? requestedProjectId
    : (projects[0]?.id ?? "");
  const selectionReady = ready && !loadingProjects && !projectError && selectedProjectId === resolvedProjectId;

  useEffect(() => {
    if (!ready) return;
    let active = true;
    requestGeneration.current += 1;
    setLoadingProjects(true);
    setProjectError(null);
    void listProjects()
      .then((rows) => {
        if (active) setProjects(rows);
      })
      .catch((cause: unknown) => {
        if (!active) return;
        setProjects([]);
        setOverview(null);
        setQuota(null);
        setProjectError({ cause });
      })
      .finally(() => {
        if (active) setLoadingProjects(false);
      });
    return () => {
      active = false;
    };
  }, [ready, discoveryAttempt]);

  useEffect(() => {
    // Failed discovery must not silently broaden a project-scoped request.
    if (loadingProjects || projectError) return;
    if (resolvedProjectId !== requestedProjectId) {
      const updated = new URLSearchParams(searchParams);
      if (resolvedProjectId) updated.set("project_id", resolvedProjectId);
      else updated.delete("project_id");
      setSearchParams(updated, { replace: true });
    }
    if (selectedProjectId === resolvedProjectId) return;
    requestGeneration.current += 1;
    setSelectedProjectId(resolvedProjectId);
    setOverview(null);
    setQuota(null);
    setLoading(true);
    setOverviewError(null);
    setQuotaError(null);
  }, [loadingProjects, projectError, requestedProjectId, resolvedProjectId, searchParams, selectedProjectId, setSearchParams]);

  const reload = useCallback(async (projectId: string) => {
    const generation = ++requestGeneration.current;
    setLoading(true);
    setOverviewError(null);
    setQuotaError(null);
    // Retain this project's data while refreshing; never substitute zeroes.
    const [overviewResult, quotaResult] = await Promise.allSettled([
      getOverview(projectId || undefined),
      projectId ? getProjectQuotas(projectId) : Promise.resolve(null),
    ]);
    if (generation !== requestGeneration.current) return;
    if (overviewResult.status === "fulfilled") {
      setOverview(overviewResult.value);
    } else {
      setOverview(null);
      setOverviewError({ cause: overviewResult.reason });
    }
    if (quotaResult.status === "fulfilled") {
      setQuota(quotaResult.value);
    } else {
      setQuota(null);
      setQuotaError({ cause: quotaResult.reason });
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    if (selectionReady) void reload(selectedProjectId);
    return () => {
      // Also invalidates responses when the page unmounts or scope changes.
      requestGeneration.current += 1;
    };
  }, [reload, selectedProjectId, selectionReady]);

  const selectProject = (projectId: string) => {
    if (projectId === selectedProjectId) return;
    requestGeneration.current += 1;
    setSelectedProjectId(projectId);
    setOverview(null);
    setQuota(null);
    setLoading(true);
    setOverviewError(null);
    setQuotaError(null);
    const updated = new URLSearchParams(searchParams);
    if (projectId) updated.set("project_id", projectId);
    else updated.delete("project_id");
    setSearchParams(updated, { replace: true });
  };

  const refresh = () => {
    if (projectError) setDiscoveryAttempt((attempt) => attempt + 1);
    else if (selectionReady && !loading) void reload(selectedProjectId);
  };

  return {
    projects,
    selectedProjectId,
    selectProject,
    overview,
    quota,
    loadingProjects,
    busy: loadingProjects || (!projectError && loading),
    projectError,
    overviewError,
    quotaError,
    refresh,
  };
}
