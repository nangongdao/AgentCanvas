import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  Clock3,
  FileText,
  FlaskConical,
  Loader2,
  Pencil,
  RotateCcw,
  Search,
  Trash2,
  Upload,
} from "lucide-react";

import type {
  DocumentStatus,
  KnowledgeBaseDTO,
  KnowledgeDocumentDTO,
  RetrievalResultDTO,
} from "@/api/endpoints/knowledge";
import { OnlineSourcesPanel } from "@/features/knowledge/OnlineSourcesPanel";
import { RetrievalCaseDialog } from "@/features/knowledge/RetrievalCaseDialog";
import { useResourceStore } from "@/stores/resourceStore";
import { cn } from "@/utils/cn";

interface Props {
  knowledgeBase: KnowledgeBaseDTO | null;
  focusedDocumentId?: string | null;
  canEdit: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onToast: (message: string) => void;
}

interface RetrievalSnapshot {
  result: RetrievalResultDTO;
  topK: number;
  threshold: number;
}

const STATUS: Record<
  DocumentStatus,
  { label: string; className: string; icon: typeof Clock3 }
> = {
  pending: { label: "pending", className: "text-warn", icon: Clock3 },
  processing: { label: "processing", className: "text-pulse", icon: Loader2 },
  ready: { label: "ready", className: "text-ok", icon: CheckCircle2 },
  failed: { label: "failed", className: "text-bad", icon: AlertTriangle },
};

function bytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function highlightQuery(text: string, query: string): ReactNode[] {
  const terms = Array.from(
    new Set(query.trim().split(/\s+/u).filter(Boolean)),
  ).sort((left, right) => right.length - left.length);
  if (terms.length === 0) return [text];
  const escaped = terms.map((term) =>
    term.replace(/[.*+?^$()|[\]\\{}]/g, "\\$&"),
  );
  const pattern = new RegExp("(" + escaped.join("|") + ")", "giu");
  return text.split(pattern).map((part, index) =>
    terms.some((term) => term.toLocaleLowerCase() === part.toLocaleLowerCase()) ? (
      <mark
        key={index + "-" + part}
        className="bg-warn/25 px-0.5 text-ice"
      >
        {part}
      </mark>
    ) : (
      part
    ),
  );
}

function ScoreValue(props: { label: string; value?: number | null }) {
  return (
    <span className="font-mono text-[9px] text-ghost/55">
      {props.label} score{" "}
      <strong className="font-medium text-ice/80">
        {props.value == null ? "-" : props.value.toFixed(3)}
      </strong>
    </span>
  );
}

function RetrievalRunPanel(props: {
  label: string;
  snapshot: RetrievalSnapshot;
}) {
  const retrieval = props.snapshot.result;
  return (
    <section className="min-w-0">
      <div className="mb-2 flex min-h-8 flex-wrap items-center gap-2">
        <span className="font-mono text-[9px] font-semibold uppercase text-pulse">
          {props.label}
        </span>
        <span className="font-mono text-[9px] text-ghost/55">
          top {props.snapshot.topK} / min {props.snapshot.threshold}
        </span>
        <span className="rounded-sm border border-line px-1.5 py-0.5 font-mono text-[8px] uppercase text-ghost/70">
          {retrieval.retrieval_mode}
          {retrieval.rerank_applied ? " + rerank" : ""}
        </span>
        <span className="ml-auto font-mono text-[9px] uppercase text-ghost">
          Matches / {retrieval.hits.length}
        </span>
      </div>
      <p
        className="mb-2 truncate font-mono text-[9px] text-ghost/45"
        title={retrieval.query}
      >
        {retrieval.query}
      </p>
      <div className="divide-y divide-line border-y border-line">
        {retrieval.hits.map((hit, index) => {
          const detail = retrieval.score_detail?.find(
            (row) => row.hit_id === hit.id,
          );
          return (
            <article
              key={hit.id}
              className="grid gap-2 py-3 md:grid-cols-[40px_160px_minmax(0,1fr)]"
            >
              <span className="font-mono text-xs text-pulse">[{index + 1}]</span>
              <div className="min-w-0">
                <p className="truncate text-xs font-medium text-ice">{hit.filename}</p>
                <p className="font-mono text-[9px] text-ghost/50">
                  score {hit.score.toFixed(3)}
                  {hit.page ? " / page " + hit.page : ""}
                </p>
                <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5">
                  <ScoreValue label="vector" value={detail?.vector_score} />
                  {detail?.keyword_score != null && (
                    <ScoreValue label="keyword" value={detail.keyword_score} />
                  )}
                  {detail?.fused_score != null && (
                    <ScoreValue label="fused" value={detail.fused_score} />
                  )}
                  {detail?.rerank_score != null && (
                    <ScoreValue label="rerank" value={detail.rerank_score} />
                  )}
                </div>
              </div>
              <p className="text-xs leading-5 text-ghost/80">
                {highlightQuery(hit.text, retrieval.query)}
              </p>
            </article>
          );
        })}
        {retrieval.hits.length === 0 && (
          <p className="py-8 text-center text-xs text-ghost/45">无匹配结果</p>
        )}
      </div>
    </section>
  );
}

export function KnowledgeWorkspace(props: Props) {
  const documents = useResourceStore((state) => state.documents);
  const retrieval = useResourceStore((state) => state.retrieval);
  const loading = useResourceStore((state) => state.loading);
  const working = useResourceStore((state) => state.working);
  const uploadAndIngest = useResourceStore((state) => state.uploadAndIngest);
  const retryIngest = useResourceStore((state) => state.retryIngest);
  const deleteDocument = useResourceStore((state) => state.deleteDocument);
  const probeRetrieval = useResourceStore((state) => state.probeRetrieval);
  const selectKnowledgeBase = useResourceStore((state) => state.selectKnowledgeBase);
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(5);
  const [threshold, setThreshold] = useState(0.2);
  const [activeSettings, setActiveSettings] = useState<{
    topK: number;
    threshold: number;
  } | null>(null);
  const [previousRun, setPreviousRun] = useState<RetrievalSnapshot | null>(null);
  const [caseDialogOpen, setCaseDialogOpen] = useState(false);
  const retrievalEpochRef = useRef(0);
  const autoRequestedRef = useRef<string | null>(null);

  useEffect(() => {
    retrievalEpochRef.current += 1;
    autoRequestedRef.current = null;
    setActiveSettings(null);
    setPreviousRun(null);
    setCaseDialogOpen(false);
  }, [props.knowledgeBase?.id]);

  const comparison = useMemo(() => {
    if (!previousRun || !retrieval) return null;
    const before = new Set(previousRun.result.hits.map((hit) => hit.id));
    const current = new Set(retrieval.hits.map((hit) => hit.id));
    return {
      shared: Array.from(current).filter((id) => before.has(id)).length,
      added: Array.from(current).filter((id) => !before.has(id)).length,
      removed: Array.from(before).filter((id) => !current.has(id)).length,
    };
  }, [previousRun, retrieval]);

  const runRetrieval = useCallback(
    async (requestedQuery: string, requestedTopK: number, requestedThreshold: number) => {
      const epoch = ++retrievalEpochRef.current;
      try {
        const result = await probeRetrieval(
          requestedQuery,
          requestedTopK,
          requestedThreshold,
        );
        if (epoch !== retrievalEpochRef.current) return;
        const settingsChanged =
          activeSettings?.topK !== requestedTopK ||
          activeSettings.threshold !== requestedThreshold;
        if (
          retrieval &&
          activeSettings &&
          retrieval.query === requestedQuery &&
          settingsChanged
        ) {
          setPreviousRun({
            result: retrieval,
            topK: activeSettings.topK,
            threshold: activeSettings.threshold,
          });
        } else {
          setPreviousRun(null);
        }
        setActiveSettings({
          topK: requestedTopK,
          threshold: requestedThreshold,
        });
        return result;
      } catch {
        // The shared error band contains the API detail.
      }
    },
    [activeSettings, probeRetrieval, retrieval],
  );

  const retrieve = useCallback(async () => {
    const normalizedQuery = query.trim();
    if (!normalizedQuery) return;
    autoRequestedRef.current = [
      props.knowledgeBase?.id ?? "",
      normalizedQuery,
      topK,
      threshold,
    ].join("\u0000");
    return runRetrieval(normalizedQuery, topK, threshold);
  }, [props.knowledgeBase?.id, query, runRetrieval, threshold, topK]);

  useEffect(() => {
    const normalizedQuery = query.trim();
    if (
      !activeSettings ||
      !retrieval ||
      !normalizedQuery ||
      retrieval.query !== normalizedQuery ||
      working !== null ||
      (activeSettings.topK === topK && activeSettings.threshold === threshold)
    ) {
      return;
    }
    const requestKey = [
      props.knowledgeBase?.id ?? "",
      normalizedQuery,
      topK,
      threshold,
    ].join("\u0000");
    if (autoRequestedRef.current === requestKey) return;
    const timeout = window.setTimeout(() => {
      autoRequestedRef.current = requestKey;
      void runRetrieval(normalizedQuery, topK, threshold);
    }, 250);
    return () => window.clearTimeout(timeout);
  }, [
    activeSettings,
    props.knowledgeBase?.id,
    query,
    retrieval,
    runRetrieval,
    threshold,
    topK,
    working,
  ]);

  const visibleRetrieval =
    retrieval && activeSettings && retrieval.query === query.trim() ? retrieval : null;
  const retrievalIsSaveable = Boolean(
    visibleRetrieval &&
      activeSettings &&
      activeSettings.topK === topK &&
      activeSettings.threshold === threshold &&
      working !== "retrieve",
  );

  if (!props.knowledgeBase) {
    return (
      <main className="flex min-h-0 flex-1 items-center justify-center p-8 text-center">
        <div>
          <BookOpen size={28} className="mx-auto text-ghost/35" strokeWidth={1.3} />
          <p className="mt-3 text-sm text-ghost/60">未选择知识库</p>
        </div>
      </main>
    );
  }

  const upload = async (file: File) => {
    try {
      const result = await uploadAndIngest(file);
      props.onToast(
        result.document.status === "ready"
          ? `摄取完成 / ${result.document.chunk_count} chunks / ${result.cache_hits} cache hits`
          : `摄取失败 / ${result.document.error ?? "unknown error"}`,
      );
    } catch {
      // The shared error band contains the API detail.
    } finally {
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const retry = async (document: KnowledgeDocumentDTO) => {
    try {
      const result = await retryIngest(document.id);
      props.onToast(
        result.document.status === "ready"
          ? `重新摄取完成 / ${result.cache_hits} cache hits`
          : `重新摄取失败 / ${result.document.error ?? "unknown error"}`,
      );
    } catch {
      // The shared error band contains the API detail.
    }
  };

  const removeDocument = async (document: KnowledgeDocumentDTO) => {
    if (!window.confirm(`删除文档“${document.filename}”？`)) return;
    try {
      await deleteDocument(document.id);
      props.onToast("文档与向量已删除");
    } catch {
      // The shared error band contains the API detail.
    }
  };

  return (
    <main className="min-h-0 min-w-0 flex-1 overflow-auto">
      <section className="border-b border-line px-4 py-4 sm:px-6">
        <div className="flex flex-wrap items-start gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="truncate font-display text-lg font-semibold text-ice">
                {props.knowledgeBase.name}
              </h2>
              <span className="rounded-xs bg-ok/10 px-1.5 py-0.5 font-mono text-[9px] text-ok">
                {documents.filter((row) => row.status === "ready").length} ready
              </span>
            </div>
            {props.knowledgeBase.description && (
              <p className="mt-1 max-w-3xl text-xs leading-5 text-ghost/70">
                {props.knowledgeBase.description}
              </p>
            )}
            <p className="mt-2 font-mono text-[9px] text-ghost/45">
              {props.knowledgeBase.embedding_model_id} / chunk {props.knowledgeBase.chunk_size} / overlap {props.knowledgeBase.chunk_overlap}
            </p>
          </div>
          {props.canEdit && (
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={props.onEdit}
                className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice"
                title="编辑知识库"
              >
                <Pencil size={14} />
              </button>
              <button
                type="button"
                onClick={props.onDelete}
                className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-bad/10 hover:text-bad"
                title="删除知识库"
              >
                <Trash2 size={14} />
              </button>
            </div>
          )}
        </div>
      </section>

      <section className="border-b border-line px-4 py-5 sm:px-6">
        <div className="mb-3 flex min-h-9 flex-wrap items-center gap-3">
          <div>
            <h3 className="text-sm font-semibold text-ice">Documents</h3>
            <p className="font-mono text-[9px] uppercase text-ghost/50">
              {documents.length} files / {documents.reduce((sum, row) => sum + row.chunk_count, 0)} chunks
            </p>
          </div>
          {props.canEdit && (
            <label className="ml-auto flex h-8 cursor-pointer items-center gap-1.5 rounded-md bg-ok px-3 text-xs font-semibold text-void transition hover:brightness-110">
              {working === "upload" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Upload size={13} />
              )}
              上传文档
              <input
                ref={inputRef}
                type="file"
                className="hidden"
                accept=".pdf,.md,.markdown,.txt,application/pdf,text/plain,text/markdown"
                disabled={working !== null}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void upload(file);
                }}
              />
            </label>
          )}
        </div>

        <div className="grid gap-2 xl:grid-cols-2">
          {documents.map((document) => (
            <DocumentRow
              key={document.id}
              document={document}
              focused={document.id === props.focusedDocumentId}
              canEdit={props.canEdit}
              working={working === document.id}
              onRetry={() => void retry(document)}
              onDelete={() => void removeDocument(document)}
            />
          ))}
        </div>
        {!loading && documents.length === 0 && (
          <div className="flex min-h-28 items-center justify-center border-y border-dashed border-line text-xs text-ghost/45">
            暂无文档
          </div>
        )}
      </section>

      <OnlineSourcesPanel
        knowledgeBaseId={props.knowledgeBase.id}
        canEdit={props.canEdit}
        onToast={props.onToast}
        onDocumentsChanged={() => selectKnowledgeBase(props.knowledgeBase!.id)}
      />

      <section className="px-4 py-5 sm:px-6">
        <div className="mb-3 flex items-center gap-2">
          <Search size={14} className="text-pulse" />
          <h3 className="text-sm font-semibold text-ice">Retrieval Probe</h3>
        </div>
        <form
          className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_80px_110px_36px]"
          onSubmit={(event) => {
            event.preventDefault();
            void retrieve();
          }}
        >
          <input
            className="field-input h-9"
            value={query}
            onChange={(event) => {
              const nextQuery = event.target.value;
              retrievalEpochRef.current += 1;
              autoRequestedRef.current = null;
              setQuery(nextQuery);
              setActiveSettings(null);
              setPreviousRun(null);
              setCaseDialogOpen(false);
            }}
            placeholder="输入检索查询"
          />
          <input
            type="number"
            min={1}
            max={50}
            className="field-input h-9 font-mono"
            value={topK}
            title="Top K"
            aria-label="Top K"
            onChange={(event) => {
              autoRequestedRef.current = null;
              setTopK(Number(event.target.value));
            }}
          />
          <input
            type="number"
            min={0}
            max={1}
            step={0.05}
            className="field-input h-9 font-mono"
            value={threshold}
            title="Score threshold"
            aria-label="最低分数"
            onChange={(event) => {
              autoRequestedRef.current = null;
              setThreshold(Number(event.target.value));
            }}
          />
          <button
            type="submit"
            disabled={!query.trim() || working !== null}
            className="flex h-9 w-9 items-center justify-center rounded-md bg-pulse text-void transition hover:brightness-110 disabled:opacity-40"
            title="检索"
          >
            {working === "retrieve" ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Search size={14} />
            )}
          </button>
        </form>

        {visibleRetrieval && (
          <div className="mt-5 animate-fade-up">
            <div className="mb-3 flex min-h-8 flex-wrap items-center gap-2">
              <span className="font-mono text-[9px] uppercase text-ghost/60">
                检索调试
              </span>
              <span className="rounded-sm border border-line px-1.5 py-0.5 font-mono text-[8px] uppercase text-ghost/65">
                {visibleRetrieval.retrieval_mode}
              </span>
              {props.canEdit && (
                <button
                  type="button"
                  onClick={() => setCaseDialogOpen(true)}
                  disabled={!retrievalIsSaveable}
                  className="ml-auto flex h-8 items-center gap-1.5 rounded-md border border-pulse/45 px-2.5 text-xs font-medium text-pulse transition hover:bg-pulse/10 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <FlaskConical size={13} />
                  保存评测样本
                </button>
              )}
            </div>
            {comparison && (
              <div className="mb-3 flex flex-wrap items-center gap-3 border-y border-line/70 py-2 font-mono text-[9px] text-ghost/60">
                <span className="uppercase text-ghost">Result delta</span>
                <span>重合 {comparison.shared}</span>
                <span className="text-ok">新增 {comparison.added}</span>
                <span className="text-bad">移除 {comparison.removed}</span>
              </div>
            )}
            <div
              className={cn(
                "grid gap-5",
                previousRun && "xl:grid-cols-2 xl:gap-6",
              )}
            >
              {previousRun && (
                <RetrievalRunPanel label="A / 上一轮" snapshot={previousRun} />
              )}
              <RetrievalRunPanel
                label={previousRun ? "B / 当前" : "当前结果"}
                snapshot={{
                  result: visibleRetrieval,
                  topK: activeSettings?.topK ?? topK,
                  threshold: activeSettings?.threshold ?? threshold,
                }}
              />
            </div>
          </div>
        )}
        <RetrievalCaseDialog
          open={caseDialogOpen}
          knowledgeBaseName={props.knowledgeBase.name}
          retrieval={retrievalIsSaveable ? visibleRetrieval : null}
          onClose={() => setCaseDialogOpen(false)}
          onSaved={(name, version) =>
            props.onToast(name + " v" + version + " 已保存")
          }
        />
      </section>
    </main>
  );
}

function DocumentRow(props: {
  document: KnowledgeDocumentDTO;
  focused: boolean;
  canEdit: boolean;
  working: boolean;
  onRetry: () => void;
  onDelete: () => void;
}) {
  const status = STATUS[props.document.status];
  const StatusIcon = status.icon;
  const rowRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!props.focused) return;
    rowRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
    rowRef.current?.focus({ preventScroll: true });
  }, [props.focused]);

  return (
    <article
      ref={rowRef}
      tabIndex={-1}
      data-document-id={props.document.id}
      data-focused={props.focused ? "true" : undefined}
      className={cn(
        "min-w-0 rounded-md border bg-ink/45 p-3 outline-hidden transition",
        props.focused
          ? "border-pulse/70 bg-pulse/5 ring-1 ring-pulse/30"
          : "border-line",
      )}
    >
      <div className="flex items-start gap-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line bg-void/60 text-ghost">
          <FileText size={14} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium text-ice" title={props.document.filename}>
            {props.document.filename}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[9px] text-ghost/50">
            <span className={cn("flex items-center gap-1 uppercase", status.className)}>
              <StatusIcon size={10} className={cn((props.working || props.document.status === "processing") && "animate-spin")} />
              {props.working ? "processing" : status.label}
            </span>
            <span>{bytes(props.document.size_bytes)}</span>
            <span>{props.document.chunk_count} chunks</span>
          </div>
        </div>
        {props.canEdit && (
          <div className="flex shrink-0 items-center gap-0.5">
            {(props.document.status === "failed" || props.document.status === "pending") && (
              <button
                type="button"
                onClick={props.onRetry}
                disabled={props.working}
                className="flex h-7 w-7 items-center justify-center rounded-md text-ghost transition hover:bg-warn/10 hover:text-warn disabled:opacity-40"
                title="重新摄取"
              >
                <RotateCcw size={12} />
              </button>
            )}
            <button
              type="button"
              onClick={props.onDelete}
              disabled={props.working}
              className="flex h-7 w-7 items-center justify-center rounded-md text-ghost transition hover:bg-bad/10 hover:text-bad disabled:opacity-40"
              title="删除文档"
            >
              <Trash2 size={12} />
            </button>
          </div>
        )}
      </div>
      {props.document.error && (
        <p className="mt-2 wrap-break-word border-l-2 border-bad/60 pl-2 text-[10px] leading-4 text-bad/85">
          {props.document.error}
        </p>
      )}
    </article>
  );
}
