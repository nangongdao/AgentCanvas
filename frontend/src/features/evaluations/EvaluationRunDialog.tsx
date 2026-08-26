import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FlaskConical, Loader2, X } from "lucide-react";

import type {
  EvaluationComparisonInput,
  EvaluationDatasetDetailDTO,
  EvaluationRunInput,
  EvaluatorType,
} from "@/api/endpoints/evaluations";
import { listModels, type ModelConfigDTO } from "@/api/endpoints/meta";
import {
  listWorkflows,
  listWorkflowVersions,
  type WorkflowDTO,
  type WorkflowVersionDTO,
} from "@/api/endpoints/workflows";
import { useDialogFocus } from "@/components/useDialogFocus";
import {
  RagEvaluationFields,
  type EvaluationNodeOption,
} from "@/features/evaluations/RagEvaluationFields";
import { cn } from "@/utils/cn";

const EVALUATORS: Array<{ value: EvaluatorType; label: string }> = [
  { value: "exact", label: "Exact" },
  { value: "contains", label: "Contains" },
  { value: "json_schema", label: "JSON Schema" },
  { value: "llm_judge", label: "LLM Judge" },
  { value: "rag", label: "RAG" },
];

export function EvaluationRunDialog(props: {
  open: boolean;
  mode: "run" | "compare";
  dataset: EvaluationDatasetDetailDTO | null;
  working: boolean;
  onClose: () => void;
  onRun: (body: EvaluationRunInput) => Promise<void>;
  onCompare: (body: EvaluationComparisonInput) => Promise<void>;
}) {
  const dialogRef = useDialogFocus<HTMLDivElement>({
    open: props.open,
    onClose: props.onClose,
    escapeEnabled: !props.working,
  });
  const [workflows, setWorkflows] = useState<WorkflowDTO[]>([]);
  const [versions, setVersions] = useState<WorkflowVersionDTO[]>([]);
  const [models, setModels] = useState<ModelConfigDTO[]>([]);
  const [workflowId, setWorkflowId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [versionBId, setVersionBId] = useState("");
  const [datasetVersionId, setDatasetVersionId] = useState("");
  const [evaluator, setEvaluator] = useState<EvaluatorType>("exact");
  const [actualPath, setActualPath] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(true);
  const [modelId, setModelId] = useState("");
  const [rubric, setRubric] = useState("");
  const [threshold, setThreshold] = useState(0.5);
  const [allowJudge, setAllowJudge] = useState(false);
  const [allowSideEffects, setAllowSideEffects] = useState(false);
  const [ragNodeId, setRagNodeId] = useState("");
  const [citationNodeId, setCitationNodeId] = useState("");
  const [retrievalK, setRetrievalK] = useState(5);
  const [minRecall, setMinRecall] = useState(1);
  const [minMrr, setMinMrr] = useState(1);
  const [minCitationCoverage, setMinCitationCoverage] = useState(0);
  const [requireCorrectNoAnswer, setRequireCorrectNoAnswer] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const versionRequestRef = useRef(0);

  const chatModels = useMemo(() => models.filter((model) => model.kind === "chat"), [models]);
  const datasetId = props.dataset?.id;
  const defaultDatasetVersionId = props.dataset?.versions[0]?.id;
  const configureVersion = useCallback((version: WorkflowVersionDTO | undefined) => {
    const ragNodes = version?.dsl.nodes.filter((node) => node.type === "rag") ?? [];
    const agentNodes = version?.dsl.nodes.filter((node) => node.type === "agent") ?? [];
    const ragNode = ragNodes[0];
    setRagNodeId(ragNode?.id ?? "");
    setCitationNodeId(agentNodes.length === 1 ? agentNodes[0].id : "");
    const topK = Number(ragNode?.config.top_k ?? 5);
    setRetrievalK(Math.min(5, Number.isFinite(topK) ? topK : 5));
  }, []);

  useEffect(() => {
    if (!props.open) return;
    const requestId = ++versionRequestRef.current;
    let active = true;
    setDatasetVersionId(defaultDatasetVersionId ?? "");
    setEvaluator("exact");
    setActualPath("");
    setCaseSensitive(true);
    setRubric("");
    setThreshold(0.5);
    setAllowJudge(false);
    setAllowSideEffects(false);
    setRagNodeId("");
    setCitationNodeId("");
    setRetrievalK(5);
    setMinRecall(1);
    setMinMrr(1);
    setMinCitationCoverage(0);
    setRequireCorrectNoAnswer(true);
    setError(null);
    setLoading(true);
    void (async () => {
      try {
        const [workflowPage, modelRows] = await Promise.all([
          listWorkflows({ limit: 200 }),
          listModels(),
        ]);
        if (!active || requestId !== versionRequestRef.current) return;
        setWorkflows(workflowPage.items);
        setModels(modelRows);
        setModelId(modelRows.find((row) => row.kind === "chat" && row.is_default)?.id ?? "");
        const first = workflowPage.items[0]?.id ?? "";
        setWorkflowId(first);
        const rows = first ? await listWorkflowVersions(first) : [];
        if (!active || requestId !== versionRequestRef.current) return;
        const published = rows.filter((row) => Boolean(row.published_at));
        setVersions(published);
        setVersionId(published[0]?.id ?? "");
        setVersionBId(published[1]?.id ?? "");
        configureVersion(published[0]);
      } catch (err) {
        if (active && requestId === versionRequestRef.current) {
          setError(err instanceof Error ? err.message : "加载运行配置失败");
        }
      } finally {
        if (active && requestId === versionRequestRef.current) setLoading(false);
      }
    })();
    return () => {
      active = false;
      if (versionRequestRef.current === requestId) versionRequestRef.current += 1;
    };
  }, [configureVersion, datasetId, defaultDatasetVersionId, props.open]);

  const selectWorkflow = async (id: string) => {
    const requestId = ++versionRequestRef.current;
    setWorkflowId(id);
    setVersionId("");
    setVersionBId("");
    setLoading(true);
    try {
      const rows = (await listWorkflowVersions(id)).filter((row) => Boolean(row.published_at));
      if (requestId !== versionRequestRef.current) return;
      setVersions(rows);
      setVersionId(rows[0]?.id ?? "");
      setVersionBId(rows[1]?.id ?? "");
      configureVersion(rows[0]);
    } catch (err) {
      if (requestId === versionRequestRef.current) {
        setError(err instanceof Error ? err.message : "加载版本失败");
      }
    } finally {
      if (requestId === versionRequestRef.current) setLoading(false);
    }
  };

  const selectVersion = (id: string) => {
    setVersionId(id);
    configureVersion(versions.find((version) => version.id === id));
  };

  const selectedVersion = versions.find((version) => version.id === versionId);
  const ragNodes: EvaluationNodeOption[] = (selectedVersion?.dsl.nodes ?? [])
    .filter((node) => node.type === "rag")
    .map((node) => ({
      id: node.id,
      label: node.name || node.id,
      topK: Number(node.config.top_k ?? 5),
    }));
  const agentNodes: EvaluationNodeOption[] = (selectedVersion?.dsl.nodes ?? [])
    .filter((node) => node.type === "agent")
    .map((node) => ({ id: node.id, label: node.name || node.id }));

  if (!props.open || !props.dataset) return null;

  const submit = async () => {
    if (!datasetVersionId || !versionId || (props.mode === "compare" && !versionBId)) {
      setError("请选择数据集版本和已发布工作流版本");
      return;
    }
    if (props.mode === "compare" && versionId === versionBId) {
      setError("A/B 对比需要选择两个不同的已发布版本");
      return;
    }
    if (evaluator === "llm_judge" && (!allowJudge || !modelId || !rubric.trim())) {
      setError("LLM Judge 需要模型、评分规则和显式授权");
      return;
    }
    if (evaluator === "rag" && !ragNodeId) {
      setError("RAG 评测需要选择检索节点");
      return;
    }
    if (evaluator === "rag" && minCitationCoverage > 0 && !citationNodeId) {
      setError("引用覆盖率大于 0 时需要选择生成答案的 Agent 节点");
      return;
    }
    setError(null);
    try {
      const config = {
        dataset_version_id: datasetVersionId,
        evaluator_type: evaluator,
        actual_path: actualPath.trim(),
        case_sensitive: caseSensitive,
        model_config_id: evaluator === "llm_judge" ? modelId : undefined,
        rubric: evaluator === "llm_judge" ? rubric.trim() : undefined,
        threshold,
        allow_llm_judge: evaluator === "llm_judge" ? allowJudge : false,
        allow_side_effects: allowSideEffects,
        ...(evaluator === "rag"
          ? {
              rag_node_id: ragNodeId,
              citation_node_id: citationNodeId || undefined,
              retrieval_k: retrievalK,
              min_recall_at_k: minRecall,
              min_mrr: minMrr,
              min_citation_coverage: minCitationCoverage,
              require_correct_no_answer: requireCorrectNoAnswer,
            }
          : {}),
      };
      if (props.mode === "compare") {
        await props.onCompare({
          ...config,
          workflow_version_a_id: versionId,
          workflow_version_b_id: versionBId,
        });
      } else {
        await props.onRun({ ...config, workflow_version_id: versionId });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动评测失败");
    }
  };

  return (
    <div ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="evaluation-run-dialog-title" className="fixed inset-0 z-50 flex items-center justify-center bg-void/75 p-3 backdrop-blur-xs">
      <div className="glass max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-line p-4 shadow-card">
        <div className="flex items-start gap-3">
          <FlaskConical size={18} className="mt-0.5 text-pulse" />
          <div className="min-w-0 flex-1">
            <h2 id="evaluation-run-dialog-title" className="text-sm font-semibold text-ice">{props.mode === "compare" ? "对比已发布版本" : "运行评测"}</h2>
            <p className="mt-0.5 truncate text-[10px] text-ghost">{props.dataset.name}</p>
          </div>
          <button type="button" onClick={props.onClose} className="h-8 w-8 text-ghost hover:text-ice" title="关闭"><X size={16} className="mx-auto" /></button>
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <SelectField label="数据集版本" value={datasetVersionId} onChange={setDatasetVersionId}>
            {props.dataset.versions.map((version) => <option key={version.id} value={version.id}>v{version.number} / {version.cases.length} cases</option>)}
          </SelectField>
          <SelectField label="工作流" value={workflowId} onChange={(value) => void selectWorkflow(value)}>
            {workflows.map((workflow) => <option key={workflow.id} value={workflow.id}>{workflow.name}</option>)}
          </SelectField>
          <SelectField label="已发布版本" value={versionId} onChange={selectVersion}>
            <option value="">请选择</option>
            {versions.map((version) => <option key={version.id} value={version.id}>v{version.number} / {version.status}</option>)}
          </SelectField>
          {props.mode === "compare" && (
            <SelectField label="对比版本 B" value={versionBId} onChange={setVersionBId}>
              <option value="">请选择</option>
              {versions.map((version) => <option key={version.id} value={version.id}>v{version.number} / {version.status}</option>)}
            </SelectField>
          )}
          <label className="text-[11px] text-ghost">实际输出路径<input value={actualPath} onChange={(e) => setActualPath(e.target.value)} className="field-input mt-1 font-mono" placeholder="留空使用完整输出，或 /answer" /></label>
        </div>

        <div className="mt-4">
          <span className="text-[11px] text-ghost">评测器</span>
          <div className="mt-1 grid grid-cols-2 gap-1 rounded-md border border-line bg-void/60 p-1 sm:grid-cols-5">
            {EVALUATORS.map((item) => (
              <button key={item.value} type="button" onClick={() => setEvaluator(item.value)} className={cn("h-8 rounded-sm font-mono text-[10px] transition", evaluator === item.value ? "bg-pulse text-void" : "text-ghost hover:bg-line hover:text-ice")}>{item.label}</button>
            ))}
          </div>
        </div>

        {evaluator === "contains" && <Toggle label="区分大小写" checked={caseSensitive} onChange={setCaseSensitive} />}
        {evaluator === "json_schema" && <p className="mt-3 rounded-md border border-line bg-ink/60 px-3 py-2 text-[11px] text-ghost">每个样本的 Expected JSON 将作为 Draft 2020-12 Schema。</p>}
        {evaluator === "llm_judge" && (
          <div className="mt-3 space-y-3 rounded-md border border-volt/30 bg-volt/5 p-3">
            <SelectField label="Judge 模型" value={modelId} onChange={setModelId}>
              <option value="">请选择</option>{chatModels.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
            </SelectField>
            <label className="block text-[11px] text-ghost">评分规则<textarea value={rubric} onChange={(e) => setRubric(e.target.value)} className="field-input mt-1 min-h-20 resize-y" placeholder="判断回答是否正确、完整且有依据" /></label>
            <label className="block text-[11px] text-ghost">通过阈值<input type="number" min="0" max="1" step="0.05" value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} className="field-input mt-1" /></label>
            <Toggle label="允许调用 LLM Judge（会产生模型调用）" checked={allowJudge} onChange={setAllowJudge} />
          </div>
        )}
        {evaluator === "rag" && (
          <RagEvaluationFields
            ragNodes={ragNodes}
            agentNodes={agentNodes}
            ragNodeId={ragNodeId}
            citationNodeId={citationNodeId}
            retrievalK={retrievalK}
            minRecall={minRecall}
            minMrr={minMrr}
            minCitationCoverage={minCitationCoverage}
            requireCorrectNoAnswer={requireCorrectNoAnswer}
            onRagNode={setRagNodeId}
            onCitationNode={setCitationNodeId}
            onRetrievalK={setRetrievalK}
            onMinRecall={setMinRecall}
            onMinMrr={setMinMrr}
            onMinCitationCoverage={setMinCitationCoverage}
            onRequireCorrectNoAnswer={setRequireCorrectNoAnswer}
          />
        )}
        <div className="mt-3"><Toggle label="允许工具、MCP 或记忆写入等副作用节点" checked={allowSideEffects} onChange={setAllowSideEffects} /></div>

        {error && <p className="mt-3 text-xs text-bad">{error}</p>}
        {loading && <p className="mt-3 flex items-center gap-2 text-xs text-ghost"><Loader2 size={13} className="animate-spin" />加载发布版本…</p>}
        <div className="mt-5 flex justify-end gap-2 border-t border-line pt-3">
          <button type="button" onClick={props.onClose} disabled={props.working} className="h-8 px-3 text-xs text-ghost hover:text-ice">取消</button>
          <button type="button" onClick={() => void submit()} disabled={props.working || loading} className="flex h-8 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void disabled:opacity-50"><FlaskConical size={13} />{props.working ? "启动中…" : props.mode === "compare" ? "开始对比" : "开始评测"}</button>
        </div>
      </div>
    </div>
  );
}

function SelectField(props: { label: string; value: string; onChange: (value: string) => void; children: React.ReactNode }) {
  return <label className="text-[11px] text-ghost">{props.label}<select value={props.value} onChange={(e) => props.onChange(e.target.value)} className="field-input mt-1">{props.children}</select></label>;
}

function Toggle(props: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return <label className="flex items-center gap-2 text-[11px] text-ghost"><input type="checkbox" checked={props.checked} onChange={(e) => props.onChange(e.target.checked)} className="h-4 w-4 accent-cyan-400" /><span>{props.label}</span></label>;
}
