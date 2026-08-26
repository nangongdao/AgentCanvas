import { useEffect, useRef, useState } from "react";
import { Check, Globe2, Loader2, Plus, X } from "lucide-react";

import { ApiError } from "@/api/client";
import {
  createOnlineSource,
  deleteOnlineSource,
  listOnlineSources,
  syncOnlineSource,
  updateOnlineSource,
  type OnlineSourceDTO,
} from "@/api/endpoints/knowledge";
import {
  EMPTY_DRAFT,
  SCHEDULES,
  SourceRow,
  intervalValue,
  type SourceDraft,
} from "@/features/knowledge/OnlineSourceParts";
import { cn } from "@/utils/cn";

interface Props {
  knowledgeBaseId: string;
  canEdit: boolean;
  onToast: (message: string) => void;
  onDocumentsChanged: () => Promise<void>;
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string" ? error.detail : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

function replaceSource(rows: OnlineSourceDTO[], replacement: OnlineSourceDTO) {
  return rows.map((row) => (row.id === replacement.id ? replacement : row));
}

export function OnlineSourcesPanel(props: Props) {
  const [sources, setSources] = useState<OnlineSourceDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<SourceDraft>(EMPTY_DRAFT);
  const sourceDocumentsRef = useRef("");
  const onDocumentsChangedRef = useRef(props.onDocumentsChanged);

  useEffect(() => {
    onDocumentsChangedRef.current = props.onDocumentsChanged;
  }, [props.onDocumentsChanged]);

  useEffect(() => {
    let current = true;
    let poll: number | undefined;
    const load = async (initial: boolean) => {
      if (initial) {
        setLoading(true);
        setError(null);
      }
      try {
        const rows = await listOnlineSources(props.knowledgeBaseId);
        if (!current) return;
        const nextDocuments = rows.map((row) => row.document_id).sort().join(",");
        const documentsChanged = !initial && sourceDocumentsRef.current !== nextDocuments;
        sourceDocumentsRef.current = nextDocuments;
        setSources(rows);
        if (documentsChanged) await onDocumentsChangedRef.current();
        if (current) setError(null);
      } catch (reason) {
        if (current) setError(errorText(reason));
      } finally {
        if (current && initial) setLoading(false);
        if (current) poll = window.setTimeout(() => void load(false), 5_000);
      }
    };

    setAdding(false);
    setEditingId(null);
    void load(true);
    return () => {
      current = false;
      if (poll !== undefined) window.clearTimeout(poll);
    };
  }, [props.knowledgeBaseId]);

  const beginAdd = () => {
    setDraft(EMPTY_DRAFT);
    setEditingId(null);
    setAdding(true);
    setError(null);
  };

  const beginEdit = (source: OnlineSourceDTO) => {
    setDraft({
      url: source.url,
      maxPages: source.max_pages,
      depth: source.depth,
      interval: intervalValue(source.sync_interval_minutes),
    });
    setAdding(false);
    setEditingId(source.id);
    setError(null);
  };

  const create = async () => {
    if (!draft.url.trim()) return;
    setWorking("create");
    setError(null);
    try {
      const created = await createOnlineSource(props.knowledgeBaseId, {
        url: draft.url.trim(),
        max_pages: draft.maxPages,
        depth: draft.depth,
        sync_interval_minutes: draft.interval ? Number(draft.interval) : null,
      });
      setSources((rows) => [...rows, created]);
      setAdding(false);
      props.onToast("在线源已创建");
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setWorking(null);
    }
  };

  const save = async (sourceId: string) => {
    setWorking("edit:" + sourceId);
    setError(null);
    try {
      const updated = await updateOnlineSource(props.knowledgeBaseId, sourceId, {
        max_pages: draft.maxPages,
        depth: draft.depth,
        sync_interval_minutes: draft.interval ? Number(draft.interval) : null,
      });
      setSources((rows) => replaceSource(rows, updated));
      setEditingId(null);
      props.onToast("在线源设置已更新");
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setWorking(null);
    }
  };

  const sync = async (source: OnlineSourceDTO) => {
    setWorking("sync:" + source.id);
    setError(null);
    try {
      const updated = await syncOnlineSource(props.knowledgeBaseId, source.id);
      setSources((rows) => replaceSource(rows, updated));
      await props.onDocumentsChanged();
      props.onToast(
        updated.document_id === source.document_id ? "在线源无内容变化" : "在线源同步完成",
      );
    } catch (reason) {
      setError(errorText(reason));
      try {
        setSources(await listOnlineSources(props.knowledgeBaseId));
      } catch {
        // Keep the actionable sync error visible.
      }
    } finally {
      setWorking(null);
    }
  };

  const remove = async (source: OnlineSourceDTO) => {
    if (!window.confirm(`删除在线源“${source.url}”及其索引文档？`)) return;
    setWorking("delete:" + source.id);
    setError(null);
    try {
      await deleteOnlineSource(props.knowledgeBaseId, source.id);
      setSources((rows) => rows.filter((row) => row.id !== source.id));
      await props.onDocumentsChanged();
      props.onToast("在线源与索引文档已删除");
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setWorking(null);
    }
  };

  return (
    <section className="border-b border-line px-4 py-5 sm:px-6">
      <div className="mb-3 flex min-h-9 flex-wrap items-center gap-3">
        <div>
          <h3 className="text-sm font-semibold text-ice">Online sources</h3>
          <p className="font-mono text-[9px] uppercase text-ghost/50">
            {sources.length} sources / {sources.filter((row) => row.status === "ready").length} ready
          </p>
        </div>
        {props.canEdit && !adding && (
          <button
            type="button"
            onClick={beginAdd}
            className="ml-auto flex h-8 items-center gap-1.5 rounded-md border border-line px-3 text-xs font-semibold text-ice transition hover:border-pulse/60 hover:text-pulse"
          >
            <Plus size={13} />
            添加在线源
          </button>
        )}
      </div>

      {error && (
        <p role="alert" className="mb-3 border-l-2 border-bad/70 pl-2 text-[10px] leading-4 text-bad">
          {error}
        </p>
      )}

      {adding && (
        <SourceForm
          draft={draft}
          busy={working === "create"}
          includeUrl
          onChange={setDraft}
          onCancel={() => setAdding(false)}
          onSubmit={() => void create()}
        />
      )}

      <div className="divide-y divide-line border-y border-line">
        {sources.map((source) =>
          editingId === source.id ? (
            <SourceForm
              key={source.id}
              draft={draft}
              busy={working === "edit:" + source.id}
              onChange={setDraft}
              onCancel={() => setEditingId(null)}
              onSubmit={() => void save(source.id)}
            />
          ) : (
            <SourceRow
              key={source.id}
              source={source}
              canEdit={props.canEdit}
              working={working}
              onEdit={() => beginEdit(source)}
              onSync={() => void sync(source)}
              onDelete={() => void remove(source)}
            />
          ),
        )}
        {loading && (
          <div className="flex min-h-20 items-center justify-center text-ghost/55">
            <Loader2 size={16} className="animate-spin" aria-label="加载在线源" />
          </div>
        )}
        {!loading && sources.length === 0 && !adding && (
          <div className="flex min-h-20 items-center justify-center gap-2 text-xs text-ghost/45">
            <Globe2 size={15} strokeWidth={1.4} />
            暂无在线源
          </div>
        )}
      </div>
    </section>
  );
}

function SourceForm(props: {
  draft: SourceDraft;
  busy: boolean;
  includeUrl?: boolean;
  onChange: (draft: SourceDraft) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const inputClass =
    "h-8 min-w-0 rounded-md border border-line bg-void px-2 text-xs text-ice outline-hidden transition focus:border-pulse/70";
  return (
    <div
      data-testid="online-source-form"
      className="grid gap-2 border-y border-pulse/30 bg-pulse/5 px-2 py-3 md:grid-cols-[minmax(220px,1fr)_90px_90px_130px_auto] md:items-end"
    >
      {props.includeUrl ? (
        <label className="min-w-0 text-[10px] text-ghost/65">
          URL
          <input
            type="url"
            required
            value={props.draft.url}
            onChange={(event) =>
              props.onChange({ ...props.draft, url: event.target.value })
            }
            className={cn(inputClass, "mt-1 w-full")}
            placeholder="https://docs.example.com"
          />
        </label>
      ) : (
        <div className="hidden md:block" />
      )}
      <label className="text-[10px] text-ghost/65">
        Pages
        <input
          type="number"
          min={1}
          max={50}
          value={props.draft.maxPages}
          onChange={(event) =>
            props.onChange({ ...props.draft, maxPages: Number(event.target.value) })
          }
          className={cn(inputClass, "mt-1 w-full")}
        />
      </label>
      <label className="text-[10px] text-ghost/65">
        Depth
        <select
          value={props.draft.depth}
          onChange={(event) =>
            props.onChange({ ...props.draft, depth: Number(event.target.value) })
          }
          className={cn(inputClass, "mt-1 w-full")}
        >
          {[0, 1, 2, 3].map((value) => (
            <option key={value} value={value}>{value}</option>
          ))}
        </select>
      </label>
      <label className="text-[10px] text-ghost/65">
        Schedule
        <select
          value={props.draft.interval}
          onChange={(event) =>
            props.onChange({ ...props.draft, interval: event.target.value })
          }
          className={cn(inputClass, "mt-1 w-full")}
        >
          {SCHEDULES.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </label>
      <div className="flex h-8 items-center gap-1 md:justify-end">
        <button
          type="button"
          onClick={props.onSubmit}
          disabled={props.busy || (props.includeUrl && !props.draft.url.trim())}
          className="flex h-8 w-8 items-center justify-center rounded-md bg-ok text-void transition hover:brightness-110 disabled:opacity-40"
          title="保存"
        >
          {props.busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
        </button>
        <button
          type="button"
          onClick={props.onCancel}
          disabled={props.busy}
          className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice disabled:opacity-40"
          title="取消"
        >
          <X size={13} />
        </button>
      </div>
    </div>
  );
}
