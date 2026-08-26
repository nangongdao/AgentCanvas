import { useCallback, useEffect, useRef, useState } from "react";
import {
  FlaskConical,
  Loader2,
  Plus,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";

import {
  createEvaluationDataset,
  createEvaluationComparison,
  createEvaluationRun,
  deleteEvaluationDataset,
  getEvaluationDataset,
  getEvaluationComparison,
  getEvaluationRun,
  listEvaluationDatasets,
  listEvaluationComparisons,
  listEvaluationRuns,
  updateEvaluationDataset,
  type EvaluationDatasetDetailDTO,
  type EvaluationComparisonDTO,
  type EvaluationComparisonInput,
  type EvaluationDatasetInput,
  type EvaluationRunDTO,
  type EvaluationRunInput,
} from "@/api/endpoints/evaluations";
import { useAuth } from "@/features/auth/AuthProvider";
import { DatasetDialog } from "@/features/evaluations/DatasetDialog";
import {
  DatasetSidebar,
  DatasetWorkspace,
  EvaluationReportPanel,
} from "@/features/evaluations/EvaluationPanels";
import { EvaluationRunDialog } from "@/features/evaluations/EvaluationRunDialog";

export function EvaluationPage() {
  const { can } = useAuth();
  const canEdit = can("editor");
  const [datasets, setDatasets] = useState<Awaited<ReturnType<typeof listEvaluationDatasets>>["items"]>([]);
  const [activeDataset, setActiveDataset] = useState<EvaluationDatasetDetailDTO | null>(null);
  const [activeVersionId, setActiveVersionId] = useState("");
  const [runs, setRuns] = useState<EvaluationRunDTO[]>([]);
  const [comparisons, setComparisons] = useState<EvaluationComparisonDTO[]>([]);
  const [activeRun, setActiveRun] = useState<EvaluationRunDTO | null>(null);
  const [activeComparison, setActiveComparison] = useState<EvaluationComparisonDTO | null>(null);
  const [reportMode, setReportMode] = useState<"runs" | "comparisons">("runs");
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [datasetDialog, setDatasetDialog] = useState(false);
  const [runDialog, setRunDialog] = useState<"run" | "compare" | null>(null);
  const [editing, setEditing] = useState(false);
  const pageRequestRef = useRef(0);
  const datasetRequestRef = useRef(0);

  const loadDataset = useCallback(async (id: string) => {
    const requestId = ++datasetRequestRef.current;
    const detail = await getEvaluationDataset(id);
    if (requestId !== datasetRequestRef.current) return;
    setActiveDataset(detail);
    setActiveVersionId(detail.versions[0]?.id ?? "");
  }, []);

  const reload = useCallback(async () => {
    const requestId = ++pageRequestRef.current;
    const datasetRequestId = ++datasetRequestRef.current;
    setLoading(true);
    setError(null);
    try {
      const [datasetRows, runRows, comparisonRows] = await Promise.all([
        listEvaluationDatasets({ limit: 200 }),
        listEvaluationRuns({ limit: 200 }),
        listEvaluationComparisons({ limit: 200 }),
      ]);
      if (
        requestId !== pageRequestRef.current
        || datasetRequestId !== datasetRequestRef.current
      ) return;
      const datasetId = datasetRows.items.some((row) => row.id === activeDataset?.id)
        ? activeDataset?.id
        : datasetRows.items[0]?.id;
      const datasetDetail = datasetId ? await getEvaluationDataset(datasetId) : null;
      if (
        requestId !== pageRequestRef.current
        || datasetRequestId !== datasetRequestRef.current
      ) return;
      setDatasets(datasetRows.items);
      setRuns(runRows.items);
      setComparisons(comparisonRows.items);
      setActiveDataset(datasetDetail);
      setActiveVersionId(datasetDetail?.versions[0]?.id ?? "");
      const selectedRun = runRows.items.find((row) => row.id === activeRun?.id) ?? runRows.items[0] ?? null;
      setActiveRun(selectedRun);
      setActiveComparison(
        comparisonRows.items.find((row) => row.id === activeComparison?.id)
          ?? comparisonRows.items[0]
          ?? null,
      );
    } catch (err) {
      if (requestId === pageRequestRef.current) {
        setError(err instanceof Error ? err.message : "加载评测工作台失败");
      }
    } finally {
      if (requestId === pageRequestRef.current) setLoading(false);
    }
  }, [activeComparison?.id, activeDataset?.id, activeRun?.id]);

  const invalidateLoads = useCallback(() => {
    pageRequestRef.current += 1;
    datasetRequestRef.current += 1;
    setLoading(false);
  }, []);

  useEffect(() => {
    void reload();
    // Initial load only; explicit refresh and mutations keep the state current.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!activeRun || !["queued", "running"].includes(activeRun.status)) return;
    const timer = window.setInterval(() => {
      void getEvaluationRun(activeRun.id)
        .then((row) => {
          setActiveRun(row);
          setRuns((items) => items.map((item) => (item.id === row.id ? row : item)));
        })
        .catch((err) => setError(err instanceof Error ? err.message : "刷新报告失败"));
    }, 500);
    return () => window.clearInterval(timer);
  }, [activeRun]);

  useEffect(() => {
    if (!activeComparison || !["queued", "running"].includes(activeComparison.status)) return;
    const timer = window.setInterval(() => {
      void getEvaluationComparison(activeComparison.id)
        .then((row) => {
          setActiveComparison(row);
          setComparisons((items) => items.map((item) => (item.id === row.id ? row : item)));
        })
        .catch((err) => setError(err instanceof Error ? err.message : "刷新对比报告失败"));
    }, 500);
    return () => window.clearInterval(timer);
  }, [activeComparison]);

  const activeId = activeDataset?.id ?? null;
  const saveDataset = async (body: EvaluationDatasetInput) => {
    invalidateLoads();
    setWorking(true);
    try {
      const saved = editing && activeDataset
        ? await updateEvaluationDataset(activeDataset.id, {
            ...body,
            expected_version: activeDataset.current_version,
          })
        : await createEvaluationDataset(body);
      setDatasetDialog(false);
      setEditing(false);
      setActiveDataset(saved);
      setActiveVersionId(saved.versions[0]?.id ?? "");
      setDatasets((await listEvaluationDatasets({ limit: 200 })).items);
    } finally {
      setWorking(false);
    }
  };

  const removeDataset = async () => {
    if (!activeDataset || !window.confirm(`删除数据集“${activeDataset.name}”？`)) return;
    invalidateLoads();
    setWorking(true);
    setError(null);
    try {
      await deleteEvaluationDataset(activeDataset.id);
      setActiveDataset(null);
      const rows = (await listEvaluationDatasets({ limit: 200 })).items;
      setDatasets(rows);
      if (rows[0]) await loadDataset(rows[0].id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除数据集失败");
    } finally {
      setWorking(false);
    }
  };

  const runEvaluation = async (body: EvaluationRunInput) => {
    invalidateLoads();
    setWorking(true);
    try {
      const run = await createEvaluationRun(body);
      setRuns((items) => [run, ...items]);
      setActiveRun(run);
      setRunDialog(null);
      setReportMode("runs");
    } finally {
      setWorking(false);
    }
  };

  const runComparison = async (body: EvaluationComparisonInput) => {
    invalidateLoads();
    setWorking(true);
    try {
      const comparison = await createEvaluationComparison(body);
      setComparisons((items) => [comparison, ...items]);
      setActiveComparison(comparison);
      setReportMode("comparisons");
      setRunDialog(null);
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="ambient-stage flex h-full w-full flex-col bg-void text-ice">
      <header role="presentation" className="glass relative z-20 flex min-h-14 flex-wrap items-center gap-2 border-b border-line px-3 py-2 sm:px-5">
        <span className="flex h-8 w-8 items-center justify-center text-volt"><FlaskConical size={19} /></span>
        <div className="min-w-0"><h1 className="text-sm font-semibold text-ice sm:text-base">AgentCanvas Evaluations</h1><p className="font-mono text-[9px] uppercase text-ghost/50">datasets / evaluators / reports</p></div>
        <div role="toolbar" aria-label="评测页面操作" className="ml-auto flex items-center gap-1.5">
          <button type="button" onClick={() => void reload()} disabled={loading} className="flex h-8 w-8 items-center justify-center rounded-md text-ghost hover:bg-line hover:text-pulse disabled:opacity-40" title="刷新"><RefreshCw size={14} className={loading ? "animate-spin" : undefined} /></button>
          {canEdit && <button type="button" onClick={() => { setEditing(false); setDatasetDialog(true); }} className="flex h-8 items-center gap-1.5 rounded-md bg-volt px-3 text-xs font-semibold text-white"><Plus size={13} /><span className="hidden sm:inline">新建数据集</span></button>}
        </div>
      </header>
      {error && <div className="relative z-10 flex min-h-9 items-center gap-2 border-b border-bad/30 bg-bad/10 px-4 text-xs text-bad"><TriangleAlert size={13} /><span className="min-w-0 flex-1 truncate">{error}</span><button type="button" onClick={() => setError(null)} className="h-7 px-2 font-mono text-[9px] uppercase">dismiss</button></div>}
      <div className="relative z-10 flex min-h-0 flex-1 flex-col md:flex-row">
        <DatasetSidebar rows={datasets} activeId={activeId} canEdit={canEdit} onSelect={(id) => void loadDataset(id).catch((err) => setError(err instanceof Error ? err.message : "加载数据集失败"))} onCreate={() => { setEditing(false); setDatasetDialog(true); }} />
        <DatasetWorkspace dataset={activeDataset} versionId={activeVersionId} canEdit={canEdit} onVersion={setActiveVersionId} onEdit={() => { setEditing(true); setDatasetDialog(true); }} onDelete={() => void removeDataset()} onRun={() => setRunDialog("run")} onCompare={() => setRunDialog("compare")} />
        <EvaluationReportPanel runs={runs} active={activeRun} comparisons={comparisons} activeComparison={activeComparison} mode={reportMode} onMode={setReportMode} onSelect={(id) => void getEvaluationRun(id).then(setActiveRun).catch((err) => setError(err instanceof Error ? err.message : "加载报告失败"))} onSelectComparison={(id) => void getEvaluationComparison(id).then(setActiveComparison).catch((err) => setError(err instanceof Error ? err.message : "加载对比报告失败"))} />
        {loading && <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-void/40"><Loader2 size={18} className="animate-spin text-pulse" /></div>}
      </div>
      <DatasetDialog open={datasetDialog} dataset={editing ? activeDataset : null} working={working} onClose={() => setDatasetDialog(false)} onSave={saveDataset} />
      <EvaluationRunDialog open={runDialog !== null} mode={runDialog ?? "run"} dataset={activeDataset} working={working} onClose={() => setRunDialog(null)} onRun={runEvaluation} onCompare={runComparison} />
    </div>
  );
}
