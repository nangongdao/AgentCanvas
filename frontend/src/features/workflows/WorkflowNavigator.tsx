import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Archive,
  ChevronRight,
  CirclePlus,
  Clock3,
  FolderKanban,
  FileUp,
  Loader2,
  RefreshCw,
  Search,
  Sparkles,
  Workflow,
  X,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  archiveWorkflow,
  importWorkflow,
  listWorkflows,
  type WorkflowDTO,
  type WorkflowImportPayload,
} from "@/api/endpoints/workflows";
import { instantiateWorkflowTemplate } from "@/api/endpoints/templates";
import { useDialogFocus } from "@/components/useDialogFocus";
import { useT } from "@/features/i18n/i18n";
import { cn } from "@/utils/cn";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string"
      ? error.detail
      : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

function updatedLabel(value?: string | null): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

interface Props {
  activeId: string | null;
  canEdit: boolean;
  onOpen: (id: string | null) => Promise<boolean>;
  onActiveArchived: () => void;
}

export function WorkflowNavigator({
  activeId,
  canEdit,
  onOpen,
  onActiveArchived,
}: Props) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<WorkflowDTO[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [openingId, setOpeningId] = useState<string | null>(null);
  const [archivingId, setArchivingId] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [creatingExample, setCreatingExample] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const dialogRef = useDialogFocus<HTMLElement>({
    open,
    onClose: () => setOpen(false),
    initialFocusRef: searchRef,
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows((await listWorkflows({ limit: 200 })).items);
    } catch (loadError) {
      setError(errorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    void load();
  }, [load, open]);

  useEffect(() => {
    const refresh = () => {
      if (open) void load();
    };
    window.addEventListener("agentcanvas:workflows-changed", refresh);
    return () => window.removeEventListener("agentcanvas:workflows-changed", refresh);
  }, [load, open]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return rows;
    return rows.filter(
      (row) =>
        row.name.toLocaleLowerCase().includes(normalized) ||
        row.description.toLocaleLowerCase().includes(normalized) ||
        row.id.toLocaleLowerCase().includes(normalized),
    );
  }, [query, rows]);

  const choose = async (id: string | null) => {
    setOpeningId(id ?? "new");
    const changed = await onOpen(id);
    setOpeningId(null);
    if (changed) setOpen(false);
  };

  const archive = async (row: WorkflowDTO) => {
    const confirmed = window.confirm(`归档工作流“${row.name}”？`);
    if (!confirmed) return;
    setArchivingId(row.id);
    setError(null);
    try {
      await archiveWorkflow(row.id);
      setRows((current) => current.filter((item) => item.id !== row.id));
      if (row.id === activeId) onActiveArchived();
      window.dispatchEvent(new Event("agentcanvas:workflows-changed"));
    } catch (archiveError) {
      setError(errorMessage(archiveError));
    } finally {
      setArchivingId(null);
    }
  };

  const importFile = async (file?: File) => {
    if (!file) return;
    setError(null);
    if (file.size > 1024 * 1024) {
      setError("导入文件不能超过 1 MiB");
      return;
    }
    setImporting(true);
    try {
      const parsed: unknown = JSON.parse(await file.text());
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("导入文件必须是 JSON 对象");
      }
      const source = parsed as Record<string, unknown>;
      const payload = (
        source.dsl && typeof source.dsl === "object"
          ? source
          : { dsl: source }
      ) as unknown as WorkflowImportPayload;
      const imported = await importWorkflow(payload);
      window.dispatchEvent(new Event("agentcanvas:workflows-changed"));
      const changed = await onOpen(imported.id);
      if (changed) setOpen(false);
    } catch (importError) {
      setError(errorMessage(importError));
    } finally {
      setImporting(false);
      if (importRef.current) importRef.current.value = "";
    }
  };

  // C5-10: one-click example workflow from the official starter template —
  // its single parameter has a non-empty default, so an empty instantiate
  // body renders a complete runnable workflow.
  const createExample = async () => {
    setCreatingExample(true);
    setError(null);
    try {
      const created = await instantiateWorkflowTemplate("official-linear", {});
      window.dispatchEvent(new Event("agentcanvas:workflows-changed"));
      const changed = await onOpen(created.id);
      if (changed) setOpen(false);
    } catch (exampleError) {
      setError(errorMessage(exampleError));
    } finally {
      setCreatingExample(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex h-8 items-center gap-1.5 rounded-md border border-line bg-ink/80 px-2.5 text-xs text-ice transition hover:border-pulse/40 hover:bg-pulse/10 hover:text-pulse"
        title="打开工作流"
      >
        <FolderKanban size={14} />
        <span className="hidden md:inline">工作流</span>
      </button>

      {open &&
        createPortal(
          <div className="fixed inset-0 z-50 bg-void/75 backdrop-blur-xs">
            <button
              type="button"
              aria-label="关闭工作流导航"
              className="absolute inset-0 h-full w-full cursor-default"
              onClick={() => setOpen(false)}
            />
            <section
              ref={dialogRef}
              tabIndex={-1}
              role="dialog"
              aria-modal="true"
              aria-labelledby="workflow-navigator-title"
              className="glass relative flex h-full w-[min(410px,92vw)] animate-slide-in flex-col border-r border-line shadow-card"
            >
              <header className="flex min-h-16 items-center gap-3 border-b border-line px-4">
                <span className="flex h-9 w-9 items-center justify-center rounded-md border border-pulse/30 bg-pulse/10 text-pulse">
                  <Workflow size={17} />
                </span>
                <div className="min-w-0">
                  <h2 id="workflow-navigator-title" className="text-sm font-semibold text-ice">
                    工作流目录
                  </h2>
                  <p className="font-mono text-[9px] uppercase text-ghost">
                    {rows.length} flows · recent first
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void load()}
                  disabled={loading}
                  className="ml-auto flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-pulse disabled:opacity-40"
                  title="刷新"
                >
                  <RefreshCw size={14} className={cn(loading && "animate-spin")} />
                </button>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice"
                  title="关闭"
                >
                  <X size={15} />
                </button>
              </header>

              <div className="border-b border-line p-3">
                {canEdit && (
                  <div className="grid grid-cols-[1fr_auto] gap-2">
                    <button
                      type="button"
                      onClick={() => void choose(null)}
                      disabled={openingId !== null || importing}
                      className="flex h-10 items-center justify-center gap-2 rounded-md bg-pulse text-xs font-semibold text-void transition hover:brightness-110 disabled:opacity-50"
                    >
                      {openingId === "new" ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <CirclePlus size={14} />
                      )}
                      新建工作流
                    </button>
                    <button
                      type="button"
                      onClick={() => importRef.current?.click()}
                      disabled={openingId !== null || importing}
                      className="flex h-10 w-10 items-center justify-center rounded-md border border-line text-ghost transition hover:border-pulse/40 hover:text-pulse disabled:opacity-40"
                      title="导入工作流"
                    >
                      {importing ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <FileUp size={14} />
                      )}
                    </button>
                    <input
                      ref={importRef}
                      type="file"
                      accept="application/json,.json"
                      className="hidden"
                      aria-label="选择工作流 JSON"
                      onChange={(event) => void importFile(event.target.files?.[0])}
                    />
                  </div>
                )}
                <label className={cn("relative block", canEdit && "mt-3")}>
                  <Search
                    size={13}
                    className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ghost/60"
                  />
                  <input
                    ref={searchRef}
                    aria-label="搜索工作流"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    className="field-input h-9 pl-8"
                    placeholder="搜索名称或 ID"
                  />
                </label>
              </div>

              <div className="min-h-0 flex-1 overflow-auto px-2 py-3">
                {error && (
                  <div className="mx-2 mb-3 border-l-2 border-bad bg-bad/5 px-3 py-2 text-xs text-bad">
                    {error}
                  </div>
                )}
                {!loading && filtered.length === 0 && canEdit && !query && rows.length === 0 && (
                  <div
                    className="mx-2 mb-3 rounded-md border border-line bg-ink/70 p-4"
                    data-testid="workflow-onboarding-card"
                  >
                    <span className="flex h-9 w-9 items-center justify-center rounded-md border border-pulse/30 bg-pulse/10 text-pulse">
                      <Sparkles size={16} />
                    </span>
                    <h3 className="mt-3 text-[13px] font-semibold text-ice">
                      {t("onboarding.navigator.title")}
                    </h3>
                    <p className="mt-1.5 text-xs leading-5 text-ghost">
                      {t("onboarding.navigator.body")}
                    </p>
                    <div className="mt-4 grid gap-2">
                      <button
                        type="button"
                        onClick={() => void choose(null)}
                        disabled={openingId !== null || creatingExample || importing}
                        className="flex h-9 items-center justify-center gap-2 rounded-md bg-pulse text-xs font-semibold text-void transition hover:brightness-110 disabled:opacity-50"
                      >
                        <CirclePlus size={13} />
                        {t("onboarding.navigator.createBlank")}
                      </button>
                      <button
                        type="button"
                        onClick={() => void createExample()}
                        disabled={openingId !== null || creatingExample || importing}
                        className="flex h-9 items-center justify-center gap-2 rounded-md border border-line text-xs font-medium text-ice transition hover:border-pulse/40 hover:text-pulse disabled:opacity-50"
                      >
                        {creatingExample ? (
                          <Loader2 size={13} className="animate-spin" />
                        ) : (
                          <Sparkles size={13} />
                        )}
                        {creatingExample
                          ? t("onboarding.navigator.creating")
                          : t("onboarding.navigator.createExample")}
                      </button>
                    </div>
                    <p className="mt-3 font-mono text-[9px] leading-4 text-ghost/55">
                      {t("onboarding.navigator.hint")}
                    </p>
                  </div>
                )}
                {!loading && filtered.length === 0 && !(canEdit && !query && rows.length === 0) && (
                  <div className="px-5 py-12 text-center">
                    <FolderKanban size={22} className="mx-auto mb-3 text-ghost/40" />
                    <p className="text-xs text-ghost/70">
                      {query ? "没有匹配的工作流" : "还没有已保存的工作流"}
                    </p>
                  </div>
                )}
                <ul className="space-y-1">
                  {filtered.map((row) => {
                    const active = row.id === activeId;
                    return (
                      <li
                        key={row.id}
                        className={cn(
                          "group flex items-center rounded-md border transition",
                          active
                            ? "border-pulse/40 bg-pulse/10"
                            : "border-transparent hover:border-line hover:bg-ink/80",
                        )}
                      >
                        <button
                          type="button"
                          onClick={() => void choose(row.id)}
                          disabled={openingId !== null}
                          className="flex min-w-0 flex-1 items-center gap-3 px-3 py-3 text-left disabled:opacity-50"
                        >
                          <span
                            className={cn(
                              "h-8 w-1 shrink-0 rounded-xs",
                              active ? "bg-pulse shadow-glow-cyan" : "bg-line",
                            )}
                          />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-[13px] font-medium text-ice">
                              {row.name}
                            </span>
                            <span className="mt-1 flex items-center gap-2 font-mono text-[9px] text-ghost">
                              <Clock3 size={10} /> {updatedLabel(row.updated_at)}
                              <span>v{row.version}</span>
                              <span>{row.id.slice(0, 8)}</span>
                            </span>
                          </span>
                          {openingId === row.id ? (
                            <Loader2 size={14} className="animate-spin text-pulse" />
                          ) : (
                            <ChevronRight size={14} className="text-ghost/40" />
                          )}
                        </button>
                        {canEdit && (
                          <button
                            type="button"
                            onClick={() => void archive(row)}
                            disabled={archivingId !== null}
                            className="mr-2 flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ghost/50 opacity-100 transition hover:bg-bad/10 hover:text-bad focus:opacity-100 disabled:opacity-40 sm:opacity-0 sm:group-hover:opacity-100"
                            title="归档"
                            aria-label={`归档 ${row.name}`}
                          >
                            {archivingId === row.id ? (
                              <Loader2 size={13} className="animate-spin" />
                            ) : (
                              <Archive size={13} />
                            )}
                          </button>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>
            </section>
          </div>,
          document.body,
        )}
    </>
  );
}
