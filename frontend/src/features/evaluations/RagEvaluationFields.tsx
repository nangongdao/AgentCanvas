import { Database, ScanSearch } from "lucide-react";

export interface EvaluationNodeOption {
  id: string;
  label: string;
  topK?: number;
}

export function RagEvaluationFields(props: {
  ragNodes: EvaluationNodeOption[];
  agentNodes: EvaluationNodeOption[];
  ragNodeId: string;
  citationNodeId: string;
  retrievalK: number;
  minRecall: number;
  minMrr: number;
  minCitationCoverage: number;
  requireCorrectNoAnswer: boolean;
  onRagNode: (value: string) => void;
  onCitationNode: (value: string) => void;
  onRetrievalK: (value: number) => void;
  onMinRecall: (value: number) => void;
  onMinMrr: (value: number) => void;
  onMinCitationCoverage: (value: number) => void;
  onRequireCorrectNoAnswer: (value: boolean) => void;
}) {
  const selectedRag = props.ragNodes.find((node) => node.id === props.ragNodeId);
  return (
    <div className="mt-3 border-y border-pulse/25 bg-pulse/5 py-3">
      <div className="flex items-center gap-2 px-1">
        <ScanSearch size={14} className="text-pulse" />
        <span className="font-mono text-[9px] uppercase text-pulse">RAG regression</span>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="text-[11px] text-ghost">
          RAG 节点
          <select
            value={props.ragNodeId}
            onChange={(event) => props.onRagNode(event.target.value)}
            className="field-input mt-1"
          >
            <option value="">请选择</option>
            {props.ragNodes.map((node) => (
              <option key={node.id} value={node.id}>{node.label}</option>
            ))}
          </select>
        </label>
        <label className="text-[11px] text-ghost">
          引用 Agent
          <select
            value={props.citationNodeId}
            onChange={(event) => props.onCitationNode(event.target.value)}
            className="field-input mt-1"
          >
            <option value="">不评估回答引用</option>
            {props.agentNodes.map((node) => (
              <option key={node.id} value={node.id}>{node.label}</option>
            ))}
          </select>
        </label>
        <NumberField
          label="Recall@k"
          value={props.retrievalK}
          min={1}
          max={selectedRag?.topK ?? 50}
          step={1}
          onChange={props.onRetrievalK}
        />
        <ThresholdField label="最小 Recall@k" value={props.minRecall} onChange={props.onMinRecall} />
        <ThresholdField label="最小 MRR" value={props.minMrr} onChange={props.onMinMrr} />
        <ThresholdField
          label="最小 Citation coverage"
          value={props.minCitationCoverage}
          onChange={props.onMinCitationCoverage}
        />
      </div>
      <label className="mt-3 flex items-center gap-2 px-1 text-[11px] text-ghost">
        <input
          type="checkbox"
          checked={props.requireCorrectNoAnswer}
          onChange={(event) => props.onRequireCorrectNoAnswer(event.target.checked)}
          className="h-4 w-4 accent-cyan-400"
        />
        <Database size={12} />
        <span>严格校验 no-answer</span>
      </label>
    </div>
  );
}

function ThresholdField(props: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <NumberField
      label={props.label}
      value={props.value}
      min={0}
      max={1}
      step={0.05}
      onChange={props.onChange}
    />
  );
}

function NumberField(props: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="text-[11px] text-ghost">
      {props.label}
      <input
        type="number"
        value={props.value}
        min={props.min}
        max={props.max}
        step={props.step}
        onChange={(event) => props.onChange(Number(event.target.value))}
        className="field-input mt-1 font-mono"
      />
    </label>
  );
}
