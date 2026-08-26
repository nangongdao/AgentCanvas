import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError } from "@/api/client";
import {
  approveMcpCatalogVersion,
  createMcpCatalogEntry,
  createMcpCatalogVersion,
  getMcpCatalogVersionDiff,
  listMcpCatalog,
  previewMcpCatalogRollout,
  revokeMcpCatalogVersion,
  rolloutMcpCatalogVersion,
  type McpCatalogEntryDTO,
  type McpCatalogManifestDTO,
  type McpCatalogRolloutPreviewDTO,
  type McpCatalogVersionDTO,
  type McpCatalogVersionDiffDTO,
  type McpTransport,
} from "@/api/endpoints/mcp";
import { useAuth } from "@/features/auth/AuthProvider";

export interface CatalogForm {
  id: string;
  name: string;
  description: string;
  sourceUrl: string;
  version: string;
  sourceRef: string;
  transport: McpTransport;
  network: string;
  filesystem: string;
  commands: string;
}

const EMPTY_FORM: CatalogForm = {
  id: "",
  name: "",
  description: "",
  sourceUrl: "",
  version: "1.0.0",
  sourceRef: "",
  transport: "stdio",
  network: "",
  filesystem: "",
  commands: "",
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string" ? error.detail : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

function lines(value: string): string[] {
  return value
    .split(/[\n,]/u)
    .map((item) => item.trim())
    .filter(Boolean);
}

function manifestFrom(form: CatalogForm): McpCatalogManifestDTO {
  return {
    transport: form.transport,
    permissions: {
      network: lines(form.network),
      filesystem: lines(form.filesystem),
      commands: lines(form.commands),
    },
  };
}

function formFromVersion(version: McpCatalogVersionDTO): CatalogForm {
  return {
    ...EMPTY_FORM,
    version: version.version,
    sourceRef: version.source_ref,
    transport: version.manifest.transport,
    network: version.manifest.permissions.network.join("\n"),
    filesystem: version.manifest.permissions.filesystem.join("\n"),
    commands: version.manifest.permissions.commands.join("\n"),
  };
}

export function useMcpCatalog() {
  const { ready, authenticated, can } = useAuth();
  const canAdmin = can("admin");
  const [entries, setEntries] = useState<McpCatalogEntryDTO[]>([]);
  const [selectedEntryId, setSelectedEntryId] = useState<string | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [baseVersionId, setBaseVersionId] = useState<string | null>(null);
  const [diff, setDiff] = useState<McpCatalogVersionDiffDTO | null>(null);
  const [preview, setPreview] = useState<McpCatalogRolloutPreviewDTO | null>(null);
  const [selectedRolloutIds, setSelectedRolloutIds] = useState<string[]>([]);
  const [form, setForm] = useState<CatalogForm>(EMPTY_FORM);
  const [creatingEntry, setCreatingEntry] = useState(false);
  const [creatingVersion, setCreatingVersion] = useState(false);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const selectedEntry = useMemo(
    () => entries.find((entry) => entry.id === selectedEntryId) ?? null,
    [entries, selectedEntryId],
  );
  const selectedVersion = useMemo(
    () => selectedEntry?.versions.find((version) => version.id === selectedVersionId) ?? null,
    [selectedEntry, selectedVersionId],
  );
  const approvedVersion = selectedEntry?.versions.find((version) => version.status === "approved");

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 3500);
  }, []);

  const load = useCallback(async (preferredEntryId?: string | null) => {
    if (!canAdmin) return;
    setLoading(true);
    setError(null);
    try {
      const rows = await listMcpCatalog();
      setEntries(rows);
      const nextEntry =
        rows.find((entry) => entry.id === preferredEntryId) ??
        rows.find((entry) => entry.id === selectedEntryId) ??
        rows[0] ??
        null;
      setSelectedEntryId(nextEntry?.id ?? null);
      setSelectedVersionId(nextEntry?.versions[0]?.id ?? null);
      setBaseVersionId(nextEntry?.versions[1]?.id ?? null);
      setDiff(null);
      setPreview(null);
      setSelectedRolloutIds([]);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setLoading(false);
    }
  }, [canAdmin]);

  useEffect(() => {
    if (ready) void load();
  }, [ready, load]);

  useEffect(() => {
    if (selectedVersion) setForm((current) => ({ ...current, ...formFromVersion(selectedVersion) }));
  }, [selectedVersion]);

  const selectEntry = (entry: McpCatalogEntryDTO) => {
    setSelectedEntryId(entry.id);
    setSelectedVersionId(entry.versions[0]?.id ?? null);
    setBaseVersionId(entry.versions[1]?.id ?? null);
    setDiff(null);
    setPreview(null);
    setSelectedRolloutIds([]);
  };

  const selectVersion = (versionId: string) => {
    const versions = selectedEntry?.versions ?? [];
    const targetIndex = versions.findIndex((version) => version.id === versionId);
    setSelectedVersionId(versionId);
    setBaseVersionId(targetIndex >= 0 ? (versions[targetIndex + 1]?.id ?? null) : null);
    setDiff(null);
    setPreview(null);
    setSelectedRolloutIds([]);
  };

  const updateForm = (patch: Partial<CatalogForm>) => setForm((current) => ({ ...current, ...patch }));

  const createEntry = async () => {
    setWorking("create-entry");
    setError(null);
    try {
      if (!form.id.trim() || !form.name.trim() || !form.sourceRef.trim()) {
        throw new Error("Catalog ID、名称和来源引用不能为空");
      }
      const row = await createMcpCatalogEntry({
        id: form.id.trim(),
        name: form.name.trim(),
        description: form.description.trim(),
        source_url: form.sourceUrl.trim() || null,
        version: form.version.trim(),
        source_ref: form.sourceRef.trim(),
        manifest: manifestFrom(form),
      });
      setCreatingEntry(false);
      showToast("Catalog entry 已创建，等待审批");
      await load(row.id);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setWorking(null);
    }
  };

  const addVersion = async () => {
    if (!selectedEntry) return;
    setWorking("create-version");
    setError(null);
    try {
      const row = await createMcpCatalogVersion(selectedEntry.id, {
        version: form.version.trim(),
        source_ref: form.sourceRef.trim(),
        manifest: manifestFrom(form),
      });
      setCreatingVersion(false);
      showToast(`版本 ${row.version} 已创建，等待审批`);
      await load(selectedEntry.id);
      selectVersion(row.id);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setWorking(null);
    }
  };

  const approve = async () => {
    if (!selectedEntry || !selectedVersion || selectedVersion.status !== "draft") return;
    setWorking("approve");
    setError(null);
    try {
      await approveMcpCatalogVersion(selectedEntry.id, selectedVersion.id);
      showToast(`版本 ${selectedVersion.version} 已批准`);
      await load(selectedEntry.id);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setWorking(null);
    }
  };

  const revoke = async () => {
    if (!selectedEntry || !selectedVersion || selectedVersion.status !== "approved") return;
    if (!window.confirm(`撤销 ${selectedEntry.name} v${selectedVersion.version}？`)) return;
    setWorking("revoke");
    setError(null);
    try {
      await revokeMcpCatalogVersion(selectedEntry.id, selectedVersion.id);
      showToast("版本已撤销");
      await load(selectedEntry.id);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setWorking(null);
    }
  };

  const calculateDiff = async () => {
    if (!selectedEntry || !selectedVersion) return;
    setWorking("diff");
    setError(null);
    try {
      setDiff(await getMcpCatalogVersionDiff(selectedEntry.id, selectedVersion.id, baseVersionId));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setWorking(null);
    }
  };

  const calculatePreview = async () => {
    if (!selectedEntry || !selectedVersion || selectedVersion.status !== "approved") return;
    setWorking("preview");
    setError(null);
    try {
      const next = await previewMcpCatalogRollout(selectedEntry.id, selectedVersion.id);
      setPreview(next);
      setSelectedRolloutIds(
        next.servers.filter((server) => server.compatible).map((server) => server.server_id),
      );
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setWorking(null);
    }
  };

  const rollout = async () => {
    if (!selectedEntry || !selectedVersion || selectedVersion.status !== "approved") return;
    if (selectedRolloutIds.length === 0) {
      setError("请选择至少一个兼容的 MCP 服务作为升级目标");
      return;
    }
    setWorking("rollout");
    setError(null);
    try {
      const result = await rolloutMcpCatalogVersion(
        selectedEntry.id,
        selectedVersion.id,
        selectedRolloutIds,
      );
      showToast(
        `已升级 ${result.updated_server_ids.length} 个 MCP 服务，${result.unchanged_server_ids.length} 个已是目标版本`,
      );
      await calculatePreview();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setWorking(null);
    }
  };

  const toggleRollout = (serverId: string) => {
    setSelectedRolloutIds((current) =>
      current.includes(serverId)
        ? current.filter((value) => value !== serverId)
        : [...current, serverId],
    );
  };

  return {
    ready,
    authenticated,
    canAdmin,
    entries,
    selectedEntryId,
    selectedVersionId,
    baseVersionId,
    diff,
    preview,
    selectedRolloutIds,
    form,
    creatingEntry,
    creatingVersion,
    loading,
    working,
    error,
    toast,
    selectedEntry,
    selectedVersion,
    approvedVersion,
    refresh: () => void load(selectedEntryId),
    dismissError: () => setError(null),
    openCreateEntry: () => {
      setForm(EMPTY_FORM);
      setCreatingEntry(true);
    },
    openCreateVersion: () => {
      const firstVersion = selectedEntry?.versions[0];
      setForm(firstVersion ? formFromVersion(firstVersion) : EMPTY_FORM);
      setCreatingVersion(true);
    },
    closeForm: () => {
      setCreatingEntry(false);
      setCreatingVersion(false);
    },
    selectEntry,
    selectVersion,
    selectBaseVersion: setBaseVersionId,
    updateForm,
    createEntry,
    addVersion,
    approve,
    revoke,
    calculateDiff,
    calculatePreview,
    rollout,
    toggleRollout,
  };
}
