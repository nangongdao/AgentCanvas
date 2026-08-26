import { useEffect, useRef, useState } from "react";
import { Braces, Check, Plus, Trash2, X } from "lucide-react";

import { useWorkflowStore } from "@/stores/workflowStore";
import type { WorkflowVariable } from "@/types/dsl";

type VariableType = WorkflowVariable["type"];

interface VariableDraft {
  name: string;
  type: VariableType;
  required: boolean;
  defaultText: string;
}

interface Props {
  canEdit: boolean;
  editingAllowed: boolean;
}

const VARIABLE_TYPES: Array<{ value: VariableType; label: string }> = [
  { value: "string", label: "文本" },
  { value: "number", label: "数字" },
  { value: "boolean", label: "布尔" },
  { value: "object", label: "对象" },
  { value: "array", label: "数组" },
];

function defaultText(variable: WorkflowVariable): string {
  if (variable.default === null || variable.default === undefined) return "";
  return variable.type === "string"
    ? String(variable.default)
    : JSON.stringify(variable.default);
}

function toDraft(variable: WorkflowVariable): VariableDraft {
  return {
    name: variable.name,
    type: variable.type,
    required: variable.required,
    defaultText: defaultText(variable),
  };
}

function newVariable(name: string): VariableDraft {
  return { name, type: "string", required: false, defaultText: "" };
}

function parseDefault(type: VariableType, value: string): unknown {
  const normalized = value.trim();
  if (!normalized) return null;
  if (type === "string") return value;
  if (type === "number") {
    const number = Number(normalized);
    if (!Number.isFinite(number)) throw new Error("数字默认值必须是有限数字");
    return number;
  }
  if (type === "boolean") {
    if (normalized !== "true" && normalized !== "false") {
      throw new Error("布尔默认值必须是 true 或 false");
    }
    return normalized === "true";
  }
  const parsed = JSON.parse(normalized) as unknown;
  if (type === "object" && (parsed === null || Array.isArray(parsed) || typeof parsed !== "object")) {
    throw new Error("对象默认值必须是 JSON 对象");
  }
  if (type === "array" && !Array.isArray(parsed)) {
    throw new Error("数组默认值必须是 JSON 数组");
  }
  return parsed;
}

function makeDraft(variables: WorkflowVariable[]): VariableDraft[] {
  return variables.map(toDraft);
}

export function WorkflowVariablesDialog({ canEdit, editingAllowed }: Props) {
  const variables = useWorkflowStore((state) => state.variables);
  const setVariables = useWorkflowStore((state) => state.setVariables);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<VariableDraft[]>([]);
  const [error, setError] = useState<string | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    window.requestAnimationFrame(() => {
      dialogRef.current?.querySelector<HTMLElement>("input, select, button")?.focus();
    });
  }, [open]);

  const openDialog = () => {
    openerRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : triggerRef.current;
    setDraft(makeDraft(variables));
    setError(null);
    setOpen(true);
  };

  const closeDialog = () => {
    setOpen(false);
    window.requestAnimationFrame(() => {
      const opener = openerRef.current;
      if (opener && opener.isConnected) opener.focus();
      else triggerRef.current?.focus();
    });
  };

  const updateDraft = (index: number, patch: Partial<VariableDraft>) => {
    setDraft((current) =>
      current.map((variable, currentIndex) =>
        currentIndex === index ? { ...variable, ...patch } : variable,
      ),
    );
    setError(null);
  };

  const apply = () => {
    const names = new Set<string>();
    try {
      const next = draft.map((variable) => {
        const name = variable.name.trim();
        if (!name) throw new Error("变量名称不能为空");
        if (names.has(name)) throw new Error(`变量名称重复：${name}`);
        names.add(name);
        return {
          name,
          type: variable.type,
          required: variable.required,
          default: parseDefault(variable.type, variable.defaultText),
        } satisfies WorkflowVariable;
      });
      setVariables(next);
      closeDialog();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const handleDialogKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeDialog();
      return;
    }
    if (event.key !== "Tab" || !dialogRef.current) return;
    const focusable = Array.from(
      dialogRef.current.querySelectorAll<HTMLElement>(
        'input, select, button:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    );
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  };

  if (!canEdit) return null;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={openDialog}
        className="flex h-8 shrink-0 items-center gap-1.5 rounded-md border border-line bg-ink/80 px-2 text-xs text-ghost transition hover:border-ghost/50 hover:bg-line/60 hover:text-ice"
        aria-label="工作流变量"
        aria-haspopup="dialog"
        aria-expanded={open}
        title="工作流变量"
      >
        <Braces size={13} />
        <span className="hidden sm:inline">变量 {variables.length}</span>
      </button>

      {open && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-void/80 p-3 backdrop-blur-xs sm:p-6">
          <button
            type="button"
            className="absolute inset-0"
            onClick={closeDialog}
            aria-label="关闭工作流变量"
          />
          <section
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="workflow-variables-title"
            onKeyDown={handleDialogKeyDown}
            className="relative flex max-h-[min(42rem,88dvh)] w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-line bg-ink shadow-card animate-fade-up"
          >
            <header className="flex shrink-0 items-center gap-3 border-b border-line px-4 py-3">
              <span className="flex h-8 w-8 items-center justify-center rounded-md border border-pulse/30 bg-pulse/10 text-pulse">
                <Braces size={15} />
              </span>
              <h2 id="workflow-variables-title" className="flex-1 text-sm font-semibold text-ice">
                工作流变量
              </h2>
              <button
                type="button"
                onClick={closeDialog}
                className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice"
                aria-label="关闭工作流变量"
                title="关闭"
              >
                <X size={15} />
              </button>
            </header>

            <div className="min-h-0 flex-1 overflow-auto p-4">
              <div className="space-y-2">
                {draft.map((variable, index) => (
                  <div
                    key={`${index}-${variable.name}`}
                    data-variable-row
                    className="grid gap-2 border-b border-line/70 pb-3 pt-1 last:border-b-0 sm:grid-cols-[minmax(0,1.4fr)_9rem_5.5rem_minmax(0,1fr)_2rem] sm:items-end"
                  >
                    <label className="flex min-w-0 flex-col gap-1">
                      <span className="font-mono text-[9px] uppercase text-ghost">变量名称</span>
                      <input
                        aria-label="变量名称"
                        value={variable.name}
                        onChange={(event) => updateDraft(index, { name: event.target.value })}
                        disabled={!editingAllowed}
                        className="field-input h-8 font-mono text-xs"
                      />
                    </label>
                    <label className="flex min-w-0 flex-col gap-1">
                      <span className="font-mono text-[9px] uppercase text-ghost">变量类型</span>
                      <select
                        aria-label="变量类型"
                        value={variable.type}
                        onChange={(event) =>
                          updateDraft(index, { type: event.target.value as VariableType })
                        }
                        disabled={!editingAllowed}
                        className="field-input h-8 font-mono text-xs"
                      >
                        {VARIABLE_TYPES.map((type) => (
                          <option key={type.value} value={type.value}>{type.label}</option>
                        ))}
                      </select>
                    </label>
                    <label className="flex h-8 items-center gap-2 text-xs text-ghost">
                      <input
                        type="checkbox"
                        aria-label="必填"
                        checked={variable.required}
                        onChange={(event) => updateDraft(index, { required: event.target.checked })}
                        disabled={!editingAllowed}
                        className="h-3.5 w-3.5 accent-pulse"
                      />
                      必填
                    </label>
                    <label className="flex min-w-0 flex-col gap-1">
                      <span className="font-mono text-[9px] uppercase text-ghost">默认值</span>
                      <input
                        aria-label="默认值"
                        value={variable.defaultText}
                        onChange={(event) => updateDraft(index, { defaultText: event.target.value })}
                        disabled={!editingAllowed}
                        placeholder={variable.type === "string" ? "可选" : "JSON / 数字 / true"}
                        className="field-input h-8 font-mono text-xs"
                      />
                    </label>
                    <button
                      type="button"
                      aria-label={`删除变量 ${variable.name || index + 1}`}
                      title="删除变量"
                      onClick={() => setDraft((current) => current.filter((_, currentIndex) => currentIndex !== index))}
                      disabled={!editingAllowed}
                      className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-bad/10 hover:text-bad disabled:opacity-35"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>

              {error && (
                <p role="alert" className="mt-3 border-l-2 border-bad px-3 py-1.5 text-xs text-bad">
                  {error}
                </p>
              )}

              <button
                type="button"
                onClick={() => {
                  const names = new Set(draft.map((variable) => variable.name));
                  let index = draft.length + 1;
                  while (names.has(`variable_${index}`)) index += 1;
                  setDraft((current) => [...current, newVariable(`variable_${index}`)]);
                }}
                disabled={!editingAllowed}
                className="mt-3 flex h-8 items-center gap-1.5 rounded-md border border-dashed border-line px-3 text-xs text-ghost transition hover:border-pulse/50 hover:text-ice disabled:opacity-35"
              >
                <Plus size={13} /> 添加变量
              </button>
            </div>

            <footer className="flex shrink-0 justify-end gap-2 border-t border-line/70 px-4 py-3">
              <button
                type="button"
                onClick={closeDialog}
                className="h-8 rounded-md px-3 text-xs text-ghost transition hover:bg-line hover:text-ice"
              >
                取消
              </button>
              <button
                type="button"
                onClick={apply}
                disabled={!editingAllowed}
                className="flex h-8 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void transition hover:brightness-110 disabled:opacity-35"
              >
                <Check size={13} /> 应用变量
              </button>
            </footer>
          </section>
        </div>
      )}
    </>
  );
}
