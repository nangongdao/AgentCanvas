import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  BookmarkPlus,
  Boxes,
  Crown,
  Loader2,
  RefreshCw,
  Search,
  Trash2,
  X,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  deleteWorkflowTemplate,
  listWorkflowTemplates,
  type WorkflowTemplateDTO,
} from "@/api/endpoints/templates";
import { useDialogFocus } from "@/components/useDialogFocus";
import { TemplateCreateForm } from "@/features/templates/TemplateCreateForm";
import { TemplateInstantiateView } from "@/features/templates/TemplateInstantiateView";
import { cn } from "@/utils/cn";

interface Props {
  workflowId: string | null;
  workflowName: string;
  canEdit: boolean;
  onOpenWorkflow: (id: string | null) => Promise<boolean>;
  onNotify: (message: string) => void;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string"
      ? error.detail
      : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

export function WorkflowTemplateGallery(props: Props) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<WorkflowTemplateDTO[]>([]);
  const [query, setQuery] = useState("");
  const [tag, setTag] = useState("");
  const [loading, setLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [creatingTemplate, setCreatingTemplate] = useState(false);
  const [selected, setSelected] = useState<WorkflowTemplateDTO | null>(null);
  const [error, setError] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const close = useCallback(() => {
    setOpen(false);
    setSelected(null);
    setCreatingTemplate(false);
    setError(null);
  }, []);
  const dialogRef = useDialogFocus<HTMLElement>({
    open,
    onClose: close,
    initialFocusRef: searchRef,
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await listWorkflowTemplates({ search: query.trim(), tag }));
    } catch (loadError) {
      setError(errorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [query, tag]);

  useEffect(() => {
    if (!open || selected || creatingTemplate) return;
    const timer = window.setTimeout(() => void load(), 200);
    return () => window.clearTimeout(timer);
  }, [creatingTemplate, load, open, selected]);

  const availableTags = useMemo(
    () => [...new Set(rows.flatMap((row) => row.tags))].sort(),
    [rows],
  );

  const remove = async (row: WorkflowTemplateDTO) => {
    if (!window.confirm(`删除模板“${row.name}”？`)) return;
    setDeletingId(row.id);
    setError(null);
    try {
      await deleteWorkflowTemplate(row.id);
      await load();
      props.onNotify("模板已删除");
    } catch (deleteError) {
      setError(errorMessage(deleteError));
    } finally {
      setDeletingId(null);
    }
  };

  const createdFromTemplate = async (workflowId: string) => {
    props.onNotify("已从模板创建工作流");
    close();
    await props.onOpenWorkflow(workflowId);
  };

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setSelected(null);
          setCreatingTemplate(false);
          setOpen(true);
        }}
        className="flex h-8 items-center gap-1.5 rounded-md border border-line bg-ink/80 px-2.5 text-xs text-ice transition hover:border-warn/40 hover:bg-warn/10 hover:text-warn"
        title="模板库"
      >
        <Boxes size={13} />
        <span className="hidden xl:inline">模板</span>
      </button>

      {open &&
        createPortal(
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-void/80 p-0 backdrop-blur-xs sm:p-5">
            <button
              type="button"
              aria-label="关闭模板库"
              className="absolute inset-0 h-full w-full cursor-default"
              onClick={close}
            />
            <section
              ref={dialogRef}
              tabIndex={-1}
              role="dialog"
              aria-modal="true"
              aria-labelledby="template-gallery-title"
              className="glass relative flex h-full w-full flex-col border-line shadow-card sm:h-[min(760px,92vh)] sm:max-w-4xl sm:rounded-lg sm:border"
            >
              <header className="flex min-h-16 items-center gap-3 border-b border-line px-4">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-warn/35 bg-warn/10 text-warn">
                  <Boxes size={17} />
                </span>
                <div className="min-w-0">
                  <h2 id="template-gallery-title" className="text-sm font-semibold text-ice">
                    工作流模板
                  </h2>
                  <p className="font-mono text-[9px] uppercase text-ghost">
                    {rows.length} templates · official and team
                  </p>
                </div>
                <button
                  type="button"
                  disabled={loading}
                  onClick={() => void load()}
                  className="ml-auto flex h-8 w-8 items-center justify-center rounded-md text-ghost hover:bg-line hover:text-ice disabled:opacity-40"
                  title="刷新"
                >
                  <RefreshCw size={14} className={cn(loading && "animate-spin")} />
                </button>
                <button
                  type="button"
                  onClick={close}
                  className="flex h-8 w-8 items-center justify-center rounded-md text-ghost hover:bg-line hover:text-ice"
                  title="关闭"
                >
                  <X size={15} />
                </button>
              </header>

              {error && (
                <p className="border-b border-bad/30 bg-bad/10 px-4 py-2 text-xs text-bad">
                  {error}
                </p>
              )}

              {selected ? (
                <TemplateInstantiateView
                  template={selected}
                  onBack={() => setSelected(null)}
                  onCreated={createdFromTemplate}
                  onError={setError}
                />
              ) : (
                <>
                  {creatingTemplate && props.workflowId && (
                    <TemplateCreateForm
                      workflowId={props.workflowId}
                      defaultName={props.workflowName}
                      onCancel={() => setCreatingTemplate(false)}
                      onError={setError}
                      onCreated={async () => {
                        setCreatingTemplate(false);
                        await load();
                        props.onNotify("当前工作流已保存为模板");
                      }}
                    />
                  )}

                  <div className="border-b border-line p-3">
                    <div className="flex gap-2">
                      <label className="relative min-w-0 flex-1">
                        <Search
                          size={13}
                          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ghost/60"
                        />
                        <input
                          ref={searchRef}
                          value={query}
                          onChange={(event) => setQuery(event.target.value)}
                          className="field-input h-9 pl-8"
                          placeholder="搜索模板"
                          aria-label="搜索模板"
                        />
                      </label>
                      {props.canEdit && props.workflowId && (
                        <button
                          type="button"
                          onClick={() => setCreatingTemplate((value) => !value)}
                          className="flex h-9 shrink-0 items-center gap-1.5 rounded-md border border-pulse/35 bg-pulse/10 px-3 text-xs text-pulse hover:bg-pulse/20"
                        >
                          <BookmarkPlus size={13} />
                          <span className="hidden sm:inline">保存为模板</span>
                        </button>
                      )}
                    </div>
                    {availableTags.length > 0 && (
                      <div className="mt-2 flex max-w-full gap-1.5 overflow-x-auto pb-1">
                        <button
                          type="button"
                          onClick={() => setTag("")}
                          className={cn(
                            "shrink-0 rounded-md border px-2 py-1 font-mono text-[9px]",
                            !tag
                              ? "border-warn/40 bg-warn/10 text-warn"
                              : "border-line text-ghost",
                          )}
                        >
                          all
                        </button>
                        {availableTags.map((value) => (
                          <button
                            key={value}
                            type="button"
                            onClick={() => setTag(value)}
                            className={cn(
                              "shrink-0 rounded-md border px-2 py-1 font-mono text-[9px]",
                              tag === value
                                ? "border-warn/40 bg-warn/10 text-warn"
                                : "border-line text-ghost",
                            )}
                          >
                            {value}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="min-h-0 flex-1 overflow-auto p-3 sm:p-4">
                    {rows.length === 0 && !loading && (
                      <p className="py-16 text-center text-xs text-ghost/60">没有匹配模板</p>
                    )}
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                      {rows.map((row) => (
                        <article
                          key={row.id}
                          className="flex min-h-48 flex-col rounded-md border border-line bg-ink/70 p-3 transition hover:border-ghost/45"
                        >
                          <div className="flex items-start gap-2">
                            <div className="min-w-0 flex-1">
                              <h3 className="truncate text-[13px] font-semibold text-ice">
                                {row.name}
                              </h3>
                              <p className="mt-1 font-mono text-[9px] uppercase text-ghost/60">
                                {row.category} · {row.dsl.nodes.length} nodes
                              </p>
                            </div>
                            {row.is_official && (
                              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-warn/30 bg-warn/10 text-warn" title="官方模板">
                                <Crown size={12} />
                              </span>
                            )}
                          </div>
                          <p className="mt-3 line-clamp-3 text-[11px] leading-5 text-ghost/75">
                            {row.description || "No description"}
                          </p>
                          <div className="mt-3 flex flex-wrap gap-1">
                            {row.tags.map((value) => (
                              <span
                                key={value}
                                className="rounded-xs bg-line/60 px-1.5 py-0.5 font-mono text-[8px] text-ghost"
                              >
                                {value}
                              </span>
                            ))}
                          </div>
                          <div className="mt-auto flex items-center gap-2 pt-4">
                            {props.canEdit && (
                              <button
                                type="button"
                                onClick={() => setSelected(row)}
                                className="h-8 flex-1 rounded-md bg-pulse text-xs font-semibold text-void hover:brightness-110"
                              >
                                使用模板
                              </button>
                            )}
                            {props.canEdit && !row.is_official && (
                              <button
                                type="button"
                                disabled={deletingId !== null}
                                onClick={() => void remove(row)}
                                className="flex h-8 w-8 items-center justify-center rounded-md border border-line text-ghost hover:border-bad/40 hover:text-bad disabled:opacity-40"
                                title="删除模板"
                              >
                                {deletingId === row.id ? (
                                  <Loader2 size={13} className="animate-spin" />
                                ) : (
                                  <Trash2 size={13} />
                                )}
                              </button>
                            )}
                          </div>
                        </article>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </section>
          </div>,
          document.body,
        )}
    </>
  );
}
