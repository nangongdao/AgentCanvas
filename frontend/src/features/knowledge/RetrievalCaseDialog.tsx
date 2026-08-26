import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FlaskConical, Loader2, Save, X } from "lucide-react";

import { ApiError } from "@/api/client";
import {
  createEvaluationDataset,
  getEvaluationDataset,
  listEvaluationDatasets,
  updateEvaluationDataset,
  type EvaluationCaseInput,
  type EvaluationDatasetDTO,
} from "@/api/endpoints/evaluations";
import type { RetrievalResultDTO } from "@/api/endpoints/knowledge";
import { useDialogFocus } from "@/components/useDialogFocus";

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string"
      ? error.detail
      : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

export function RetrievalCaseDialog(props: {
  open: boolean;
  knowledgeBaseName: string;
  retrieval: RetrievalResultDTO | null;
  onClose: () => void;
  onSaved: (datasetName: string, version: number) => void;
}) {
  const saveEpochRef = useRef(0);
  const [datasets, setDatasets] = useState<EvaluationDatasetDTO[]>([]);
  const [targetId, setTargetId] = useState("new");
  const [datasetName, setDatasetName] = useState("");
  const [caseName, setCaseName] = useState("");
  const [inputVariable, setInputVariable] = useState("query");
  const [answerable, setAnswerable] = useState(true);
  const [selectedHits, setSelectedHits] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useDialogFocus<HTMLDivElement>({
    open: props.open,
    onClose: props.onClose,
    escapeEnabled: !working,
  });

  useEffect(() => {
    saveEpochRef.current += 1;
    setWorking(false);
  }, [props.open, props.retrieval]);

  useEffect(() => {
    if (!props.open || !props.retrieval) return;
    const retrieval = props.retrieval;
    let current = true;
    setTargetId("new");
    setDatasetName(props.knowledgeBaseName + " RAG 回归");
    setCaseName(retrieval.query.slice(0, 120));
    setInputVariable("query");
    setAnswerable(retrieval.hits.length > 0);
    setSelectedHits(new Set(retrieval.hits.map((hit) => hit.id)));
    setError(null);
    setLoading(true);
    void listEvaluationDatasets({ limit: 200 })
      .then((result) => {
        if (current) setDatasets(result.items);
      })
      .catch((reason: unknown) => {
        if (current) setError(errorText(reason));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [props.knowledgeBaseName, props.open, props.retrieval]);

  const selectedCount = useMemo(
    () =>
      props.retrieval?.hits.filter((hit) => selectedHits.has(hit.id)).length ?? 0,
    [props.retrieval, selectedHits],
  );

  if (!props.open || !props.retrieval) return null;
  const retrieval = props.retrieval;

  const save = async () => {
    setError(null);
    const variable = inputVariable.trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(variable)) {
      setError("输入变量必须是合法的工作流变量名");
      return;
    }
    if (targetId === "new" && !datasetName.trim()) {
      setError("请输入数据集名称");
      return;
    }
    if (answerable && selectedCount === 0) {
      setError("有答案样本至少需要一个相关分块");
      return;
    }

    const evaluationCase: EvaluationCaseInput = {
      id: "retrieval-" + crypto.randomUUID().replaceAll("-", "").slice(0, 20),
      name: caseName.trim() || retrieval.query.slice(0, 120),
      inputs: { [variable]: retrieval.query },
      expected: answerable
        ? {
            answerable: true,
            relevant_chunk_ids: retrieval.hits
              .filter((hit) => selectedHits.has(hit.id))
              .map((hit) => hit.id),
          }
        : { answerable: false },
    };

    const saveEpoch = ++saveEpochRef.current;
    setWorking(true);
    try {
      const saved =
        targetId === "new"
          ? await createEvaluationDataset({
              name: datasetName.trim(),
              description:
                "Created from the " +
                props.knowledgeBaseName +
                " retrieval debug console.",
              cases: [evaluationCase],
              change_summary: "Initial retrieval debug case",
            })
          : await (async () => {
              const detail = await getEvaluationDataset(targetId);
              const currentVersion = detail.versions[0];
              if (!currentVersion) throw new Error("目标数据集没有可追加的版本");
              return updateEvaluationDataset(targetId, {
                name: detail.name,
                description: detail.description,
                expected_version: detail.current_version,
                cases: [...currentVersion.cases, evaluationCase],
                change_summary: "Added from retrieval debug console",
              });
            })();
      if (saveEpochRef.current === saveEpoch) {
        props.onSaved(saved.name, saved.current_version);
        props.onClose();
      }
    } catch (reason) {
      if (saveEpochRef.current === saveEpoch) setError(errorText(reason));
    } finally {
      if (saveEpochRef.current === saveEpoch) setWorking(false);
    }
  };

  return createPortal(
    <div
      ref={dialogRef}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-labelledby="retrieval-case-dialog-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-void/80 p-3 backdrop-blur-xs"
    >
      <div className="glass flex max-h-[92vh] w-full max-w-2xl flex-col rounded-lg border border-line shadow-card">
        <header className="flex items-center gap-3 border-b border-line px-4 py-3">
          <span className="flex h-8 w-8 items-center justify-center rounded-md border border-line text-pulse">
            <FlaskConical size={15} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="retrieval-case-dialog-title" className="text-sm font-semibold text-ice">
              保存检索评测样本
            </h2>
            <p className="mt-0.5 font-mono text-[9px] uppercase text-ghost/90">
              D2 dataset / immutable version
            </p>
          </div>
          <button
            type="button"
            onClick={props.onClose}
            disabled={working}
            className="flex h-8 w-8 items-center justify-center text-ghost hover:text-ice disabled:opacity-40"
            title="关闭"
          >
            <X size={16} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-[11px] text-ghost">
              目标数据集
              <select
                value={targetId}
                onChange={(event) => setTargetId(event.target.value)}
                disabled={loading}
                className="field-input mt-1"
              >
                <option value="new">新建数据集</option>
                {datasets.map((dataset) => (
                  <option key={dataset.id} value={dataset.id}>
                    {dataset.name} / v{dataset.current_version}
                  </option>
                ))}
              </select>
            </label>
            {targetId === "new" ? (
              <label className="text-[11px] text-ghost">
                数据集名称
                <input
                  data-dialog-initial-focus
                  value={datasetName}
                  onChange={(event) => setDatasetName(event.target.value)}
                  className="field-input mt-1"
                />
              </label>
            ) : (
              <div className="self-end pb-2 font-mono text-[9px] text-ghost/55">
                保存后创建新的不可变版本
              </div>
            )}
            <label className="text-[11px] text-ghost">
              样本名称
              <input
                value={caseName}
                onChange={(event) => setCaseName(event.target.value)}
                className="field-input mt-1"
              />
            </label>
            <label className="text-[11px] text-ghost">
              输入变量
              <input
                value={inputVariable}
                onChange={(event) => setInputVariable(event.target.value)}
                className="field-input mt-1 font-mono"
                placeholder="query"
              />
            </label>
          </div>

          <div className="mt-4 flex items-center gap-2 border-y border-line py-2.5">
            <input
              id="retrieval-case-answerable"
              type="checkbox"
              checked={answerable}
              onChange={(event) => setAnswerable(event.target.checked)}
              className="h-4 w-4 accent-pulse"
            />
            <label
              htmlFor="retrieval-case-answerable"
              className="text-xs font-medium text-ice"
            >
              该查询应当有答案
            </label>
            <span className="ml-auto font-mono text-[9px] text-ghost/85">
              相关分块 {answerable ? selectedCount : 0}
            </span>
          </div>

          <div className="mt-3 space-y-1.5">
            {retrieval.hits.map((hit, index) => (
              <label
                key={hit.id}
                className="flex cursor-pointer items-start gap-3 rounded-md border border-line/80 bg-ink/55 p-2.5"
              >
                <input
                  type="checkbox"
                  checked={answerable && selectedHits.has(hit.id)}
                  disabled={!answerable}
                  onChange={(event) => {
                    setSelectedHits((current) => {
                      const next = new Set(current);
                      if (event.target.checked) next.add(hit.id);
                      else next.delete(hit.id);
                      return next;
                    });
                  }}
                  aria-label={"将 [" + (index + 1) + "] 标记为相关分块"}
                  className="mt-0.5 h-4 w-4 shrink-0 accent-pulse"
                />
                <span className="min-w-0">
                  <span className="block truncate text-[11px] font-medium text-ice">
                    [{index + 1}] {hit.filename} / chunk {hit.chunk_index}
                  </span>
                  <span className="mt-0.5 line-clamp-2 text-[10px] leading-4 text-ghost/85">
                    {hit.text}
                  </span>
                </span>
              </label>
            ))}
            {retrieval.hits.length === 0 && (
              <p className="py-5 text-center text-xs text-ghost/50">
                当前无命中，可保存为无答案样本。
              </p>
            )}
          </div>
        </div>

        <footer className="flex min-h-14 items-center gap-3 border-t border-line px-4 py-2">
          {error && <p className="min-w-0 flex-1 text-xs text-bad">{error}</p>}
          <button
            type="button"
            onClick={props.onClose}
            disabled={working}
            className="ml-auto h-8 px-3 text-xs text-ghost hover:text-ice disabled:opacity-40"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={working || loading}
            className="flex h-8 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void disabled:opacity-45"
          >
            {working ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
            保存样本
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
