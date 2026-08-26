import { Link } from "react-router-dom";
import {
  ArrowUpRight,
  Database,
  FileJson2,
  FlaskConical,
  GitCompareArrows,
  Pencil,
  Play,
  Plus,
  Trash2,
} from "lucide-react";

import type {
  EvaluationDatasetDTO,
  EvaluationDatasetDetailDTO,
  EvaluationComparisonDTO,
  EvaluationRunDTO,
} from "@/api/endpoints/evaluations";
import { cn } from "@/utils/cn";
import { EvaluationComparisonDetail } from "@/features/evaluations/EvaluationComparisonDetail";
import { RagReportMetrics } from "@/features/evaluations/RagReportMetrics";

export function DatasetSidebar(props: {
  rows: EvaluationDatasetDTO[];
  activeId: string | null;
  canEdit: boolean;
  onSelect: (id: string) => void;
  onCreate: () => void;
}) {
  return (
    <aside className="glass flex max-h-56 w-full shrink-0 flex-col border-b border-line md:max-h-none md:w-64 md:border-b-0 md:border-r">
      <div className="flex h-11 items-center justify-between border-b border-line px-3">
        <span className="font-mono text-[9px] uppercase text-ghost/60">Datasets / {props.rows.length}</span>
        {props.canEdit && <button type="button" onClick={props.onCreate} className="flex h-7 w-7 items-center justify-center text-ghost hover:text-pulse" title="新建数据集"><Plus size={14} /></button>}
      </div>
      <div className="flex min-h-0 flex-1 gap-1 overflow-auto p-2 md:flex-col">
        {props.rows.map((row) => (
          <button key={row.id} type="button" onClick={() => props.onSelect(row.id)} className={cn("min-w-48 rounded-md border px-3 py-2 text-left transition md:min-w-0", row.id === props.activeId ? "border-pulse/45 bg-pulse/10" : "border-transparent hover:border-line hover:bg-line/40") }>
            <div className="flex items-center gap-2"><Database size={12} className={row.id === props.activeId ? "text-pulse" : "text-ghost"} /><span className="min-w-0 flex-1 truncate text-xs text-ice">{row.name}</span></div>
            <div className="mt-1 flex gap-2 font-mono text-[9px] text-ghost/55"><span>v{row.current_version}</span><span>{row.case_count} cases</span></div>
          </button>
        ))}
        {props.rows.length === 0 && <p className="px-2 py-4 text-xs text-ghost/50">尚无评测数据集。</p>}
      </div>
    </aside>
  );
}

export function DatasetWorkspace(props: {
  dataset: EvaluationDatasetDetailDTO | null;
  versionId: string;
  canEdit: boolean;
  onVersion: (id: string) => void;
  onEdit: () => void;
  onDelete: () => void;
  onRun: () => void;
  onCompare: () => void;
}) {
  if (!props.dataset) {
    return <main className="flex min-h-64 min-w-0 flex-1 items-center justify-center p-6 text-xs text-ghost/50">选择或创建数据集</main>;
  }
  const version = props.dataset.versions.find((row) => row.id === props.versionId) ?? props.dataset.versions[0];
  return (
    <main className="min-w-0 flex-1 overflow-auto p-3 sm:p-5">
      <div className="mx-auto max-w-5xl">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1 basis-full sm:basis-auto">
            <h2 className="truncate text-base font-semibold text-ice">{props.dataset.name}</h2>
            <p className="mt-1 text-xs text-ghost">{props.dataset.description || "无描述"}</p>
          </div>
          <select aria-label="数据集版本" value={version?.id ?? ""} onChange={(e) => props.onVersion(e.target.value)} className="h-8 rounded-md border border-line bg-ink px-2 font-mono text-[10px] text-ice">
            {props.dataset.versions.map((row) => <option key={row.id} value={row.id}>v{row.number} / {row.cases.length} cases</option>)}
          </select>
          {props.canEdit && <button type="button" onClick={props.onEdit} className="flex h-8 w-8 items-center justify-center rounded-md border border-line text-ghost hover:text-ice" title="创建新版本"><Pencil size={13} /></button>}
          {props.canEdit && <button type="button" onClick={props.onDelete} className="flex h-8 w-8 items-center justify-center rounded-md border border-line text-ghost hover:border-bad/40 hover:text-bad" title="删除数据集"><Trash2 size={13} /></button>}
          {props.canEdit && <button type="button" onClick={props.onRun} className="flex h-8 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void"><Play size={12} fill="currentColor" />运行评测</button>}
          {props.canEdit && <button type="button" onClick={props.onCompare} className="flex h-8 items-center gap-1.5 rounded-md border border-volt/50 bg-volt/10 px-3 text-xs font-semibold text-volt"><GitCompareArrows size={13} />A/B 对比</button>}
        </div>
        {version && <div className="mt-3 flex flex-wrap items-center gap-3 border-y border-line py-2 font-mono text-[9px] text-ghost/55"><span>VERSION {version.number}</span><span>{version.change_summary}</span><span>{version.created_at ? new Date(version.created_at).toLocaleString() : ""}</span></div>}
        <div className="mt-3 space-y-2">
          {version?.cases.map((row, index) => (
            <article key={row.id} className="rounded-md border border-line bg-ink/60 p-3">
              <div className="flex items-center gap-2"><span className="flex h-5 w-5 items-center justify-center rounded-sm bg-line font-mono text-[9px] text-ghost">{index + 1}</span><span className="min-w-0 flex-1 truncate text-xs font-medium text-ice">{row.name || row.id}</span><code className="text-[9px] text-ghost/50">{row.id}</code></div>
              <div className="mt-2 grid gap-2 lg:grid-cols-2"><JsonBlock label="Inputs" value={row.inputs} /><JsonBlock label="Expected" value={row.expected} /></div>
            </article>
          ))}
        </div>
      </div>
    </main>
  );
}

export function EvaluationReportPanel(props: {
  runs: EvaluationRunDTO[];
  active: EvaluationRunDTO | null;
  comparisons: EvaluationComparisonDTO[];
  activeComparison: EvaluationComparisonDTO | null;
  mode: "runs" | "comparisons";
  onMode: (mode: "runs" | "comparisons") => void;
  onSelect: (id: string) => void;
  onSelectComparison: (id: string) => void;
}) {
  const comparisonMode = props.mode === "comparisons";
  return (
    <aside className="glass flex max-h-[48vh] min-h-64 w-full shrink-0 flex-col border-t border-line xl:max-h-none xl:w-96 xl:border-l xl:border-t-0">
      <div className="flex h-11 items-center gap-2 border-b border-line px-3">
        <FlaskConical size={13} className="text-volt" />
        <div className="flex rounded-md border border-line bg-void/50 p-0.5">
          <button type="button" onClick={() => props.onMode("runs")} className={cn("h-6 rounded-sm px-2 font-mono text-[8px] uppercase", !comparisonMode ? "bg-pulse text-void" : "text-ghost")}>Runs {props.runs.length}</button>
          <button type="button" onClick={() => props.onMode("comparisons")} className={cn("h-6 rounded-sm px-2 font-mono text-[8px] uppercase", comparisonMode ? "bg-volt text-void" : "text-ghost")}>A/B {props.comparisons.length}</button>
        </div>
      </div>
      <div className="flex max-h-32 gap-1 overflow-auto border-b border-line p-2 xl:max-h-44 xl:flex-col">
        {!comparisonMode && props.runs.map((run) => (
          <button key={run.id} type="button" onClick={() => props.onSelect(run.id)} className={cn("min-w-56 rounded-md border px-2.5 py-2 text-left xl:min-w-0", props.active?.id === run.id ? "border-volt/50 bg-volt/10" : "border-transparent hover:border-line") }>
            <div className="flex items-center gap-2"><StatusDot status={run.status} /><span className="min-w-0 flex-1 truncate text-[11px] text-ice">{run.workflow_name ?? "Workflow"} v{run.workflow_version_number ?? "?"}</span><span className="font-mono text-[8px] uppercase text-ghost">{run.evaluator_type}</span></div>
            <p className="mt-1 truncate text-[9px] text-ghost/55">{run.dataset_name} v{run.dataset_version_number}</p>
          </button>
        ))}
        {comparisonMode && props.comparisons.map((comparison) => (
          <button key={comparison.id} type="button" onClick={() => props.onSelectComparison(comparison.id)} className={cn("min-w-56 rounded-md border px-2.5 py-2 text-left xl:min-w-0", props.activeComparison?.id === comparison.id ? "border-volt/50 bg-volt/10" : "border-transparent hover:border-line") }>
            <div className="flex items-center gap-2"><StatusDot status={comparison.status} /><span className="min-w-0 flex-1 truncate text-[11px] text-ice">v{comparison.variant_a.workflow_version_number ?? "?"} vs v{comparison.variant_b.workflow_version_number ?? "?"}</span><GitCompareArrows size={11} className="text-volt" /></div>
            <p className="mt-1 truncate text-[9px] text-ghost/55">{comparison.dataset_name} v{comparison.dataset_version_number}</p>
          </button>
        ))}
        {(!comparisonMode ? props.runs.length === 0 : props.comparisons.length === 0) && <p className="p-2 text-xs text-ghost/50">尚无评测报告。</p>}
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-3">
        {comparisonMode ? (
          props.activeComparison ? <EvaluationComparisonDetail comparison={props.activeComparison} /> : <p className="text-xs text-ghost/50">选择一条对比报告。</p>
        ) : props.active ? <RunDetail run={props.active} /> : <p className="text-xs text-ghost/50">选择一条运行查看逐样本结果。</p>}
      </div>
    </aside>
  );
}

function RunDetail({ run }: { run: EvaluationRunDTO }) {
  const summary = run.summary;
  return <div>
    <div className="flex flex-wrap items-center gap-2"><StatusBadge status={run.status} /><span className="font-mono text-[9px] text-ghost">{run.id.slice(0, 8)}</span>{run.workflow_id && <Link to={`/workflows/${run.workflow_id}`} className="ml-auto flex items-center gap-1 text-[10px] text-pulse hover:underline">打开工作流 <ArrowUpRight size={11} /></Link>}</div>
    {run.error && <p className="mt-2 rounded-md border border-bad/30 bg-bad/10 p-2 text-[10px] text-bad">{run.error}</p>}
    <div className="mt-3 grid grid-cols-3 gap-1"><Metric label="Pass" value={formatPercent(summary.pass_rate)} /><Metric label="Latency" value={formatMs(summary.average_duration_ms)} /><Metric label="Cost" value={formatCost(summary.estimated_cost_usd)} /></div>
    <RagReportMetrics summary={summary} />
    <div className="mt-3 space-y-2">{run.cases.map((row) => <article key={row.id} className="rounded-md border border-line bg-ink/60 p-2.5"><div className="flex items-center gap-2"><StatusDot status={row.status} /><span className="min-w-0 flex-1 truncate text-[11px] text-ice">{row.name || row.case_id}</span>{row.score != null && <span className="font-mono text-[9px] text-ghost">{row.score.toFixed(2)}</span>}</div><p className="mt-1 text-[10px] text-ghost">{row.message}</p>{row.execution_id && <span className="mt-1 block font-mono text-[8px] text-ghost/45">exec {row.execution_id.slice(0, 10)} / {row.duration_ms ?? "-"}ms</span>}<details className="mt-2"><summary className="cursor-pointer text-[9px] text-ghost/60">输入与输出</summary><div className="mt-1 space-y-1"><JsonBlock label="Actual" value={row.actual} /><JsonBlock label="Expected" value={row.expected} /></div></details></article>)}</div>
  </div>;
}

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  return <div className="min-w-0 rounded-sm border border-line/80 bg-void/55 p-2"><div className="mb-1 flex items-center gap-1 font-mono text-[8px] uppercase text-ghost/50"><FileJson2 size={10} />{label}</div><pre className="max-h-32 overflow-auto whitespace-pre-wrap break-all font-mono text-[9px] leading-4 text-ice/75">{JSON.stringify(value, null, 2)}</pre></div>;
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="rounded-sm border border-line bg-void/50 px-2 py-1.5"><div className="font-mono text-[8px] uppercase text-ghost/45">{label}</div><div className="mt-0.5 text-sm font-semibold text-ice">{value}</div></div>; }
function formatPercent(value: unknown) { return typeof value === "number" ? `${Math.round(value * 100)}%` : "-"; }
function formatMs(value: unknown) { return typeof value === "number" ? `${Math.round(value)}ms` : "-"; }
function formatCost(value: unknown) { return typeof value === "string" ? `$${Number(value).toFixed(6)}` : "unknown"; }
function StatusDot({ status }: { status: string }) { return <span className={cn("h-2 w-2 shrink-0 rounded-full", status === "passed" || status === "completed" ? "bg-ok" : status === "failed" || status === "error" ? "bg-bad" : "animate-pulse bg-warn")} />; }
function StatusBadge({ status }: { status: string }) { return <span className={cn("rounded-sm border px-1.5 py-0.5 font-mono text-[8px] uppercase", status === "completed" ? "border-ok/40 bg-ok/10 text-ok" : status === "failed" ? "border-bad/40 bg-bad/10 text-bad" : "border-warn/40 bg-warn/10 text-warn")}>{status}</span>; }
