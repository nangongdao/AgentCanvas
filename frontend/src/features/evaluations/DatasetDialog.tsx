import { useEffect, useState } from "react";
import { Plus, Save, ScanSearch, Trash2, X } from "lucide-react";

import type {
  EvaluationCaseInput,
  EvaluationDatasetDetailDTO,
  EvaluationDatasetInput,
} from "@/api/endpoints/evaluations";
import { useDialogFocus } from "@/components/useDialogFocus";

interface DraftCase {
  draftKey: string;
  id: string;
  name: string;
  inputs: string;
  expected: string;
}

const blankCase = (): DraftCase => ({
  draftKey: crypto.randomUUID(),
  id: crypto.randomUUID().replaceAll("-", "").slice(0, 12),
  name: "",
  inputs: "{}",
  expected: '""',
});

function toDraft(caseInput: EvaluationCaseInput): DraftCase {
  return {
    draftKey: crypto.randomUUID(),
    id: caseInput.id,
    name: caseInput.name,
    inputs: JSON.stringify(caseInput.inputs, null, 2),
    expected: JSON.stringify(caseInput.expected, null, 2),
  };
}

export function DatasetDialog(props: {
  open: boolean;
  dataset: EvaluationDatasetDetailDTO | null;
  working: boolean;
  onClose: () => void;
  onSave: (body: EvaluationDatasetInput) => Promise<void>;
}) {
  const dialogRef = useDialogFocus<HTMLDivElement>({
    open: props.open,
    onClose: props.onClose,
    escapeEnabled: !props.working,
  });
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [summary, setSummary] = useState("");
  const [cases, setCases] = useState<DraftCase[]>([blankCase()]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!props.open) return;
    const current = props.dataset?.versions[0];
    setName(props.dataset?.name ?? "");
    setDescription(props.dataset?.description ?? "");
    setSummary(props.dataset ? "更新回归样本" : "Initial dataset");
    setCases(current?.cases.map(toDraft) ?? [blankCase()]);
    setError(null);
  }, [props.dataset, props.open]);

  if (!props.open) return null;

  const updateCase = (index: number, patch: Partial<DraftCase>) => {
    setCases((rows) => rows.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row)));
  };

  const submit = async () => {
    setError(null);
    if (!name.trim()) {
      setError("请输入数据集名称");
      return;
    }
    try {
      const parsed = cases.map((row, index): EvaluationCaseInput => {
        const inputs: unknown = JSON.parse(row.inputs);
        if (!inputs || typeof inputs !== "object" || Array.isArray(inputs)) {
          throw new Error(`样本 ${index + 1} 的 inputs 必须是 JSON 对象`);
        }
        return {
          id: row.id.trim() || `case-${index + 1}`,
          name: row.name.trim(),
          inputs: inputs as Record<string, unknown>,
          expected: JSON.parse(row.expected),
        };
      });
      if (parsed.length === 0) throw new Error("至少需要一个样本");
      await props.onSave({
        name: name.trim(),
        description: description.trim(),
        change_summary: summary.trim(),
        cases: parsed,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "JSON 格式无效");
    }
  };

  return (
    <div
      ref={dialogRef}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-labelledby="dataset-dialog-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-void/75 p-3 backdrop-blur-xs"
    >
      <div className="glass flex max-h-[92vh] w-full max-w-4xl flex-col rounded-lg border border-line shadow-card">
        <div className="flex items-center gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2 id="dataset-dialog-title" className="text-sm font-semibold text-ice">
              {props.dataset ? "创建数据集版本" : "新建评测数据集"}
            </h2>
            <p className="mt-0.5 font-mono text-[9px] uppercase text-ghost/50">
              immutable cases / json inputs / expected output
            </p>
          </div>
          <button type="button" onClick={props.onClose} className="h-8 w-8 text-ghost hover:text-ice" title="关闭">
            <X size={16} className="mx-auto" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-[11px] text-ghost">
              名称
              <input data-dialog-initial-focus value={name} onChange={(e) => setName(e.target.value)} className="field-input mt-1" />
            </label>
            <label className="text-[11px] text-ghost">
              版本说明
              <input value={summary} onChange={(e) => setSummary(e.target.value)} className="field-input mt-1" />
            </label>
          </div>
          <label className="mt-3 block text-[11px] text-ghost">
            描述
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} className="field-input mt-1 min-h-16 resize-y" />
          </label>
          <div className="mt-4 flex items-center justify-between">
            <span className="font-mono text-[10px] uppercase text-ghost/60">Cases / {cases.length}</span>
            <button type="button" onClick={() => setCases((rows) => [...rows, blankCase()])} className="flex h-8 items-center gap-1 rounded-md border border-line px-2 text-xs text-ice hover:border-pulse/50">
              <Plus size={13} /> 添加样本
            </button>
          </div>
          <div className="mt-2 space-y-2">
            {cases.map((row, index) => (
              <section key={row.draftKey} className="rounded-md border border-line bg-ink/65 p-3">
                <div className="grid gap-2 sm:grid-cols-[140px_minmax(0,1fr)_32px]">
                  <input aria-label={`样本 ${index + 1} ID`} value={row.id} onChange={(e) => updateCase(index, { id: e.target.value })} className="field-input font-mono" placeholder="case-id" />
                  <input aria-label={`样本 ${index + 1} 名称`} value={row.name} onChange={(e) => updateCase(index, { name: e.target.value })} className="field-input" placeholder={`样本 ${index + 1}`} />
                  <button type="button" disabled={cases.length === 1} onClick={() => setCases((rows) => rows.filter((_, i) => i !== index))} className="flex h-9 w-8 items-center justify-center text-ghost hover:text-bad disabled:opacity-30" title="删除样本">
                    <Trash2 size={14} />
                  </button>
                </div>
                <div className="mt-2 grid gap-2 lg:grid-cols-2">
                  <label className="text-[10px] text-ghost">Inputs JSON<textarea value={row.inputs} onChange={(e) => updateCase(index, { inputs: e.target.value })} className="field-input mt-1 min-h-28 resize-y font-mono" /></label>
                  <div className="text-[10px] text-ghost">
                    <div className="flex h-5 items-center justify-between">
                      <label htmlFor={`expected-${row.draftKey}`}>Expected JSON</label>
                      <button
                        type="button"
                        onClick={() => updateCase(index, {
                          expected: JSON.stringify({
                            answerable: true,
                            relevant_document_ids: ["document-id"],
                          }, null, 2),
                        })}
                        className="flex h-5 w-5 items-center justify-center text-ghost hover:text-pulse"
                        title="填充 RAG 期望模板"
                        aria-label="填充 RAG 期望模板"
                      >
                        <ScanSearch size={11} />
                      </button>
                    </div>
                    <textarea id={`expected-${row.draftKey}`} value={row.expected} onChange={(e) => updateCase(index, { expected: e.target.value })} className="field-input min-h-28 resize-y font-mono" />
                  </div>
                </div>
              </section>
            ))}
          </div>
        </div>
        <div className="flex min-h-14 items-center gap-3 border-t border-line px-4 py-2">
          {error && <p className="min-w-0 flex-1 text-xs text-bad">{error}</p>}
          <button type="button" onClick={props.onClose} disabled={props.working} className="ml-auto h-8 px-3 text-xs text-ghost hover:text-ice">取消</button>
          <button type="button" onClick={() => void submit()} disabled={props.working} className="flex h-8 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void disabled:opacity-50">
            <Save size={13} /> {props.working ? "保存中…" : "保存版本"}
          </button>
        </div>
      </div>
    </div>
  );
}
