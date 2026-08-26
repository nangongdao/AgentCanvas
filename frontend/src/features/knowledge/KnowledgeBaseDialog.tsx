import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { Database, Loader2, Save, X } from "lucide-react";

import type {
  KnowledgeBaseDTO,
  KnowledgeBaseInput,
} from "@/api/endpoints/knowledge";
import { listModels, type ModelConfigDTO } from "@/api/endpoints/meta";
import { useDialogFocus } from "@/components/useDialogFocus";

interface Props {
  open: boolean;
  knowledgeBase: KnowledgeBaseDTO | null;
  working: boolean;
  onClose: () => void;
  onSave: (input: KnowledgeBaseInput) => Promise<void>;
}

interface Draft {
  name: string;
  description: string;
  embeddingModelId: string;
  chunkSize: number;
  chunkOverlap: number;
}

const EMPTY_DRAFT: Draft = {
  name: "",
  description: "",
  embeddingModelId: "default-embedding",
  chunkSize: 1000,
  chunkOverlap: 150,
};

export function KnowledgeBaseDialog(props: Props) {
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [models, setModels] = useState<ModelConfigDTO[]>([]);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useDialogFocus<HTMLDivElement>({
    open: props.open,
    onClose: props.onClose,
    escapeEnabled: !props.working,
  });

  useEffect(() => {
    if (!props.open) return;
    const row = props.knowledgeBase;
    setDraft(
      row
        ? {
            name: row.name,
            description: row.description,
            embeddingModelId: row.embedding_model_id,
            chunkSize: row.chunk_size,
            chunkOverlap: row.chunk_overlap,
          }
        : EMPTY_DRAFT,
    );
    setError(null);
    void listModels().then((rows) => setModels(rows.filter((item) => item.kind === "embedding")));
  }, [props.knowledgeBase, props.open]);

  const valid = useMemo(
    () =>
      draft.name.trim().length > 0 &&
      draft.chunkSize >= 128 &&
      draft.chunkOverlap >= 0 &&
      draft.chunkOverlap < draft.chunkSize,
    [draft],
  );

  if (!props.open) return null;

  const save = async () => {
    if (!valid) {
      setError("请检查名称和分块窗口");
      return;
    }
    setError(null);
    try {
      await props.onSave({
        name: draft.name.trim(),
        description: draft.description.trim(),
        embedding_model_id: draft.embeddingModelId,
        chunk_size: draft.chunkSize,
        chunk_overlap: draft.chunkOverlap,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  return createPortal(
    <div
      ref={dialogRef}
      tabIndex={-1}
      className="fixed inset-0 z-50 flex items-center justify-center bg-void/80 p-4 backdrop-blur-xs"
      role="dialog"
      aria-modal="true"
      aria-labelledby="knowledge-dialog-title"
    >
      <div className="glass w-full max-w-lg overflow-hidden rounded-lg border border-line shadow-card animate-fade-up">
        <header className="flex min-h-14 items-center gap-3 border-b border-line px-5">
          <span className="flex h-8 w-8 items-center justify-center rounded-md border border-ok/30 bg-ok/10 text-ok">
            <Database size={15} />
          </span>
          <div>
            <h2 id="knowledge-dialog-title" className="text-sm font-semibold text-ice">
              {props.knowledgeBase ? "编辑知识库" : "新建知识库"}
            </h2>
            <p className="font-mono text-[9px] uppercase text-ghost/55">
              collection / embedding / chunking
            </p>
          </div>
          <button
            type="button"
            onClick={props.onClose}
            className="ml-auto flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice"
            title="关闭"
          >
            <X size={15} />
          </button>
        </header>

        <div className="space-y-4 p-5">
          <Field label="名称">
            <input
              autoFocus
              data-dialog-initial-focus
              className="field-input h-9"
              value={draft.name}
              onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            />
          </Field>
          <Field label="描述">
            <textarea
              className="field-input min-h-20 resize-y leading-5"
              value={draft.description}
              onChange={(event) => setDraft({ ...draft, description: event.target.value })}
            />
          </Field>
          <Field label="Embedding 模型">
            <select
              className="field-input h-9"
              value={draft.embeddingModelId}
              onChange={(event) =>
                setDraft({ ...draft, embeddingModelId: event.target.value })
              }
            >
              {models.length === 0 && (
                <option value="default-embedding">Default Embedding</option>
              )}
              {models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name} / {model.model_name}
                </option>
              ))}
            </select>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Chunk size">
              <input
                type="number"
                min={128}
                max={8000}
                className="field-input h-9 font-mono"
                value={draft.chunkSize}
                onChange={(event) =>
                  setDraft({ ...draft, chunkSize: Number(event.target.value) })
                }
              />
            </Field>
            <Field label="Overlap">
              <input
                type="number"
                min={0}
                max={2000}
                className="field-input h-9 font-mono"
                value={draft.chunkOverlap}
                onChange={(event) =>
                  setDraft({ ...draft, chunkOverlap: Number(event.target.value) })
                }
              />
            </Field>
          </div>
          {error && <p className="border-l-2 border-bad pl-3 text-xs text-bad">{error}</p>}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-line px-5 py-3">
          <button
            type="button"
            onClick={props.onClose}
            className="h-8 rounded-md border border-line px-3 text-xs text-ghost transition hover:text-ice"
          >
            取消
          </button>
          <button
            type="button"
            disabled={!valid || props.working}
            onClick={() => void save()}
            className="flex h-8 items-center gap-1.5 rounded-md bg-ok px-3 text-xs font-semibold text-void transition hover:brightness-110 disabled:opacity-40"
          >
            {props.working ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
            保存
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}

function Field(props: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="font-mono text-[9px] uppercase text-ghost">{props.label}</span>
      {props.children}
    </label>
  );
}
