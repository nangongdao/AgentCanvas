import { Quote, ScanSearch } from "lucide-react";

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function percent(value: unknown) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "-";
}

export function RagReportMetrics({ summary }: { summary: Record<string, unknown> }) {
  const rag = record(summary.rag);
  if (Object.keys(rag).length === 0) return null;
  const k = typeof rag.k === "number" ? rag.k : "?";
  const fingerprints = Array.isArray(rag.corpus_fingerprints)
    ? rag.corpus_fingerprints.map(String)
    : [];
  return (
    <div className="mt-3 border-y border-pulse/25 py-2.5">
      <div className="flex items-center gap-2 font-mono text-[8px] uppercase text-pulse">
        <ScanSearch size={11} /> retrieval evidence
        {fingerprints[0] && (
          <span className="ml-auto text-ghost/45">{fingerprints[0].slice(0, 10)}</span>
        )}
      </div>
      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-2 sm:grid-cols-4">
        <RagMetric label={`Recall@${k}`} value={percent(rag.recall_at_k)} />
        <RagMetric label="MRR" value={percent(rag.mrr)} />
        <RagMetric label="Citation" value={percent(rag.citation_coverage)} icon />
        <RagMetric label="No-answer" value={percent(rag.no_answer_rate)} />
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[8px] text-ghost/55">
        <span>accuracy {percent(rag.no_answer_accuracy)}</span>
        <span>abstention {percent(rag.correct_abstention_rate)}</span>
        <span>false no-answer {percent(rag.false_no_answer_rate)}</span>
        <span>false answer {percent(rag.false_answer_rate)}</span>
      </div>
    </div>
  );
}

function RagMetric(props: { label: string; value: string; icon?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1 font-mono text-[8px] uppercase text-ghost/45">
        {props.icon && <Quote size={9} />}{props.label}
      </div>
      <div className="mt-0.5 text-sm font-semibold text-ice">{props.value}</div>
    </div>
  );
}
