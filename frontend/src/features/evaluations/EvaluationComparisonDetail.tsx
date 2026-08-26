import { GitCompareArrows } from "lucide-react";

import type { EvaluationComparisonDTO } from "@/api/endpoints/evaluations";
import { cn } from "@/utils/cn";

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function percent(value: unknown) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "-";
}

function milliseconds(value: unknown) {
  return typeof value === "number" ? `${Math.round(value)}ms` : "-";
}

function dollars(value: unknown) {
  return typeof value === "string" ? `$${Number(value).toFixed(6)}` : "unknown";
}

export function EvaluationComparisonDetail({
  comparison,
}: {
  comparison: EvaluationComparisonDTO;
}) {
  const variantA = record(comparison.summary.variant_a);
  const variantB = record(comparison.summary.variant_b);
  const delta = record(comparison.summary.delta_b_minus_a);
  const ragA = record(variantA.rag);
  const ragB = record(variantB.rag);
  const ragDelta = record(delta.rag);
  const hasRag = Object.keys(ragA).length > 0 || Object.keys(ragB).length > 0;
  return (
    <div>
      <div className="flex items-center gap-2">
        <Status status={comparison.status} />
        <GitCompareArrows size={13} className="text-volt" />
        <span className="font-mono text-[9px] text-ghost">
          {comparison.id.slice(0, 8)}
        </span>
      </div>
      {comparison.error && (
        <p className="mt-2 rounded-md border border-bad/30 bg-bad/10 p-2 text-[10px] text-bad">
          {comparison.error}
        </p>
      )}
      <div className="mt-3 overflow-hidden rounded-md border border-line">
        <div className="grid grid-cols-[1fr_0.8fr_0.8fr_0.8fr] bg-void/60 px-2 py-1.5 font-mono text-[8px] uppercase text-ghost/50">
          <span>Metric</span><span>A</span><span>B</span><span>Delta</span>
        </div>
        <ComparisonRow label="Quality" a={percent(variantA.pass_rate)} b={percent(variantB.pass_rate)} delta={percent(delta.pass_rate)} />
        <ComparisonRow label="Latency" a={milliseconds(variantA.average_duration_ms)} b={milliseconds(variantB.average_duration_ms)} delta={milliseconds(delta.average_duration_ms)} />
        <ComparisonRow label="Failure" a={percent(variantA.failure_rate)} b={percent(variantB.failure_rate)} delta={percent(delta.failure_rate)} />
        <ComparisonRow label="Cost" a={dollars(variantA.estimated_cost_usd)} b={dollars(variantB.estimated_cost_usd)} delta={dollars(delta.estimated_cost_usd)} />
        <ComparisonRow label="Coverage" a={percent(variantA.cost_coverage)} b={percent(variantB.cost_coverage)} delta="-" />
        {hasRag && <ComparisonRow label={`Recall@${String(ragA.k ?? ragB.k ?? "?")}`} a={percent(ragA.recall_at_k)} b={percent(ragB.recall_at_k)} delta={percent(ragDelta.recall_at_k)} />}
        {hasRag && <ComparisonRow label="MRR" a={percent(ragA.mrr)} b={percent(ragB.mrr)} delta={percent(ragDelta.mrr)} />}
        {hasRag && <ComparisonRow label="Citation" a={percent(ragA.citation_coverage)} b={percent(ragB.citation_coverage)} delta={percent(ragDelta.citation_coverage)} />}
        {hasRag && <ComparisonRow label="No-answer accuracy" a={percent(ragA.no_answer_accuracy)} b={percent(ragB.no_answer_accuracy)} delta={percent(ragDelta.no_answer_accuracy)} />}
      </div>
      <div className="mt-3 space-y-2">
        {comparison.cases.map((item) => (
          <article key={item.case_id} className="rounded-md border border-line bg-ink/60 p-2.5">
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-[11px] text-ice">
                {item.name || item.case_id}
              </span>
              <span className="font-mono text-[8px] text-ghost/50">{item.case_id}</span>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-2">
              <CaseVariant label="A" result={item.variant_a} />
              <CaseVariant label="B" result={item.variant_b} />
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

function ComparisonRow(props: { label: string; a: string; b: string; delta: string }) {
  return (
    <div className="grid grid-cols-[1fr_0.8fr_0.8fr_0.8fr] border-t border-line px-2 py-1.5 font-mono text-[9px]">
      <span className="text-ghost">{props.label}</span><span>{props.a}</span><span>{props.b}</span><span className="text-volt">{props.delta}</span>
    </div>
  );
}

function CaseVariant({ label, result }: { label: string; result: EvaluationComparisonDTO["cases"][number]["variant_a"] }) {
  return (
    <div className="min-w-0 border-l border-line pl-2">
      <div className="flex items-center gap-1.5">
        <span className="font-mono text-[8px] text-ghost/50">{label}</span>
        <span className={cn("font-mono text-[9px]", result.status === "passed" ? "text-ok" : result.status === "failed" ? "text-bad" : "text-warn")}>{result.status}</span>
      </div>
      <p className="mt-1 truncate font-mono text-[8px] text-ghost/50">
        {result.duration_ms ?? "-"}ms / {result.estimated_cost_usd == null ? "cost unknown" : `$${Number(result.estimated_cost_usd).toFixed(6)}`}
      </p>
      {result.execution_id && <p className="mt-1 truncate font-mono text-[8px] text-ghost/40">exec {result.execution_id}</p>}
    </div>
  );
}

function Status({ status }: { status: string }) {
  return <span className={cn("rounded-sm border px-1.5 py-0.5 font-mono text-[8px] uppercase", status === "completed" ? "border-ok/40 bg-ok/10 text-ok" : status === "failed" ? "border-bad/40 bg-bad/10 text-bad" : "border-warn/40 bg-warn/10 text-warn")}>{status}</span>;
}
