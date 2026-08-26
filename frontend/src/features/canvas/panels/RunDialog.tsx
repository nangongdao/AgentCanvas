import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { Braces, Bug, Loader2, Play, X } from "lucide-react";

import { useDialogFocus } from "@/components/useDialogFocus";
import type { DebugRunOptions } from "@/api/endpoints/workflows";
import { useWorkflowStore, type FlowNode } from "@/stores/workflowStore";
import type { WorkflowVariable } from "@/types/dsl";
import { cn } from "@/utils/cn";

type InputType = WorkflowVariable["type"];

interface InputField {
  name: string;
  type: InputType;
  required: boolean;
  default?: unknown;
}

const INPUT_TYPES = new Set<InputType>(["string", "number", "boolean", "object"]);

/** Node types that cannot host an injected breakpoint (see backend
 * should_break): routing is conditional/Command or they are terminals. */
const NON_BREAKPOINT_TYPES = new Set<string>([
  "start",
  "end",
  "condition",
  "switch",
]);

function inputFields(variables: WorkflowVariable[], nodes: FlowNode[]): InputField[] {
  const fields = new Map<string, InputField>();
  for (const variable of variables) {
    fields.set(variable.name, {
      name: variable.name,
      type: variable.type,
      required: variable.required ?? false,
      default: variable.default,
    });
  }

  const start = nodes.find((node) => node.data.nodeType === "start");
  const startFields = start?.data.config.input_schema;
  if (!Array.isArray(startFields)) return [...fields.values()];
  for (const candidate of startFields) {
    if (typeof candidate !== "object" || candidate === null) continue;
    const row = candidate as Record<string, unknown>;
    const name = typeof row.name === "string" ? row.name : "";
    const type = row.type;
    if (!name || typeof type !== "string" || !INPUT_TYPES.has(type as InputType)) {
      continue;
    }
    if (!fields.has(name)) {
      fields.set(name, {
        name,
        type: type as InputType,
        required: row.required !== false,
        default: row.default,
      });
    }
  }
  return [...fields.values()];
}

function initialValues(fields: InputField[]): Record<string, unknown> {
  return Object.fromEntries(
    fields.map((field) => {
      if (field.default !== undefined && field.default !== null) {
        return [field.name, structuredClone(field.default)];
      }
      if (field.type === "boolean") return [field.name, false];
      if (field.type === "object") return [field.name, {}];
      return [field.name, undefined];
    }),
  );
}

/** Linear node ids eligible for a debug breakpoint, in canvas order. */
function breakpointCandidates(nodes: FlowNode[]): { id: string; label: string }[] {
  return nodes
    .filter((node) => {
      const t = node.data.nodeType;
      return typeof t === "string" && !NON_BREAKPOINT_TYPES.has(t);
    })
    .map((node) => ({
      id: node.id,
      label: node.data.label || node.id,
    }));
}

interface Props {
  open: boolean;
  working: boolean;
  onClose: () => void;
  onRun: (
    inputs: Record<string, unknown>,
    debug?: DebugRunOptions | null,
  ) => Promise<boolean>;
}

export function RunDialog({ open, working, onClose, onRun }: Props) {
  const variables = useWorkflowStore((state) => state.variables);
  const nodes = useWorkflowStore((state) => state.nodes);
  const fields = useMemo(() => inputFields(variables, nodes), [nodes, variables]);
  const breakpoints = useMemo(() => breakpointCandidates(nodes), [nodes]);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [objectDrafts, setObjectDrafts] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [rawDraft, setRawDraft] = useState("{}");
  const [debugMode, setDebugMode] = useState(false);
  const [selectedBreakpoints, setSelectedBreakpoints] = useState<Set<string>>(
    new Set(),
  );
  const [singleStep, setSingleStep] = useState(false);
  const dialogRef = useDialogFocus<HTMLElement>({
    open,
    onClose,
    escapeEnabled: !working,
  });

  useEffect(() => {
    if (!open) return;
    const nextValues = initialValues(fields);
    setValues(nextValues);
    setObjectDrafts(
      Object.fromEntries(
        fields
          .filter((field) => field.type === "object")
          .map((field) => [field.name, JSON.stringify(nextValues[field.name] ?? {}, null, 2)]),
      ),
    );
    setErrors({});
    setRawDraft("{}");
    setDebugMode(false);
    setSelectedBreakpoints(new Set());
    setSingleStep(false);
  }, [fields, open]);

  const toggleBreakpoint = (id: string) => {
    setSelectedBreakpoints((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const buildDebug = (): DebugRunOptions | null => {
    if (!debugMode) return null;
    if (singleStep) return { single_step: true };
    return { breakpoints: [...selectedBreakpoints], single_step: false };
  };

  if (!open) return null;

  const setValue = (name: string, value: unknown) => {
    setValues((current) => ({ ...current, [name]: value }));
    setErrors((current) => ({ ...current, [name]: "" }));
  };

  const submit = async () => {
    if (fields.length === 0) {
      try {
        const parsed = JSON.parse(rawDraft) as unknown;
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
          throw new Error("输入必须是 JSON 对象");
        }
        setErrors({});
        if (await onRun(parsed as Record<string, unknown>, buildDebug())) onClose();
      } catch (error) {
        setErrors({ _raw: error instanceof Error ? error.message : String(error) });
      }
      return;
    }

    const nextErrors: Record<string, string> = {};
    for (const field of fields) {
      const value = values[field.name];
      if (
        field.required &&
        (value === undefined || value === null || (field.type === "string" && value === ""))
      ) {
        nextErrors[field.name] = "此字段为必填项";
      }
      if (field.type === "object") {
        try {
          const parsed = JSON.parse(objectDrafts[field.name] ?? "{}");
          if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
            throw new Error();
          }
          setValue(field.name, parsed);
        } catch {
          nextErrors[field.name] = "请输入有效的 JSON 对象";
        }
      }
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    const inputs = Object.fromEntries(
      Object.entries(values).filter(([, value]) => value !== undefined),
    );
    for (const field of fields.filter((candidate) => candidate.type === "object")) {
      inputs[field.name] = JSON.parse(objectDrafts[field.name] ?? "{}");
    }
    if (await onRun(inputs, buildDebug())) onClose();
  };

  return createPortal(
    <div className="fixed inset-0 z-60 flex items-center justify-center bg-void/80 p-3 backdrop-blur-xs sm:p-6">
      <section
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="run-dialog-title"
        className="glass flex max-h-[92vh] w-full max-w-xl flex-col overflow-hidden rounded-lg border border-line shadow-card"
      >
        <header className="flex min-h-14 items-center gap-3 border-b border-line px-4 sm:px-5">
          <span className="flex h-8 w-8 items-center justify-center rounded-md bg-pulse/10 text-pulse">
            <Play size={15} fill="currentColor" />
          </span>
          <div>
            <h2 id="run-dialog-title" className="text-sm font-semibold text-ice">
              运行工作流
            </h2>
            <p className="font-mono text-[9px] uppercase text-ghost/60">
              {fields.length} inputs
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={working}
            className="ml-auto flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice disabled:opacity-40"
            title="关闭"
          >
            <X size={15} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-auto px-4 py-5 sm:px-5">
          {fields.length === 0 ? (
            <label className="flex flex-col gap-2">
              <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-ghost">
                输入 JSON
              </span>
              <div className="relative">
                <Braces size={13} className="absolute right-3 top-3 text-ghost/40" />
                <textarea
                  data-dialog-initial-focus
                  className={cn(
                    "min-h-44 w-full resize-y rounded-md border bg-void/70 px-3 py-2.5 pr-8 font-mono text-[11px] leading-5 text-ice outline-hidden",
                    errors._raw ? "border-bad/60" : "border-line focus:border-pulse/60",
                  )}
                  value={rawDraft}
                  onChange={(event) => setRawDraft(event.target.value)}
                  spellCheck={false}
                />
              </div>
              {errors._raw && <span className="text-[10px] text-bad">{errors._raw}</span>}
            </label>
          ) : (
            <div className="space-y-4">
              {fields.map((field, index) => (
                <InputControl
                  key={field.name}
                  field={field}
                  initialFocus={index === 0}
                  value={values[field.name]}
                  objectDraft={objectDrafts[field.name]}
                  error={errors[field.name]}
                  onChange={(value) => setValue(field.name, value)}
                  onObjectDraftChange={(draft) => {
                    setObjectDrafts((current) => ({ ...current, [field.name]: draft }));
                    setErrors((current) => ({ ...current, [field.name]: "" }));
                  }}
                />
              ))}
            </div>
          )}
        </div>

        {breakpoints.length > 0 && (
          <DebugOptions
            debugMode={debugMode}
            singleStep={singleStep}
            breakpoints={breakpoints}
            selected={selectedBreakpoints}
            onToggleMode={() => setDebugMode((v) => !v)}
            onToggleStep={() => setSingleStep((v) => !v)}
            onToggleBreakpoint={toggleBreakpoint}
          />
        )}

        <footer className="flex items-center justify-end gap-2 border-t border-line px-4 py-3 sm:px-5">
          <button
            type="button"
            onClick={onClose}
            disabled={working}
            className="h-9 rounded-md border border-line px-4 text-xs text-ice transition hover:bg-line disabled:opacity-40"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void submit()}
            disabled={working}
            className="flex h-9 items-center gap-2 rounded-md bg-pulse px-4 text-xs font-semibold text-void transition hover:brightness-110 disabled:opacity-50"
          >
            {working ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
            {working ? "启动中" : "开始运行"}
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}

function InputControl(props: {
  field: InputField;
  value: unknown;
  objectDraft?: string;
  error?: string;
  initialFocus?: boolean;
  onChange: (value: unknown) => void;
  onObjectDraftChange: (value: string) => void;
}) {
  const { field } = props;
  return (
    <label className="flex flex-col gap-1.5">
      <span className="flex items-center gap-2 text-xs text-ice">
        {field.name}
        {field.required && <span className="text-warn">*</span>}
        <span className="font-mono text-[8px] uppercase text-ghost/50">{field.type}</span>
      </span>
      {field.type === "string" && (
        <input
          data-dialog-initial-focus={props.initialFocus || undefined}
          className={cn("field-input h-10", props.error && "border-bad/60")}
          value={typeof props.value === "string" ? props.value : ""}
          onChange={(event) => props.onChange(event.target.value)}
        />
      )}
      {field.type === "number" && (
        <input
          data-dialog-initial-focus={props.initialFocus || undefined}
          type="number"
          className={cn("field-input h-10 font-mono", props.error && "border-bad/60")}
          value={typeof props.value === "number" ? props.value : ""}
          onChange={(event) => {
            const parsed = Number(event.target.value);
            props.onChange(event.target.value && Number.isFinite(parsed) ? parsed : undefined);
          }}
        />
      )}
      {field.type === "boolean" && (
        <input
          data-dialog-initial-focus={props.initialFocus || undefined}
          type="checkbox"
          className="h-4 w-4 accent-cyan-400"
          checked={Boolean(props.value)}
          onChange={(event) => props.onChange(event.target.checked)}
        />
      )}
      {field.type === "object" && (
        <textarea
          data-dialog-initial-focus={props.initialFocus || undefined}
          className={cn(
            "min-h-28 resize-y rounded-md border bg-void/70 px-3 py-2 font-mono text-[10px] leading-5 text-ice outline-hidden",
            props.error ? "border-bad/60" : "border-line focus:border-pulse/60",
          )}
          value={props.objectDraft ?? "{}"}
          onChange={(event) => props.onObjectDraftChange(event.target.value)}
          spellCheck={false}
        />
      )}
      {props.error && <span className="text-[10px] text-bad">{props.error}</span>}
    </label>
  );
}

function DebugOptions(props: {
  debugMode: boolean;
  singleStep: boolean;
  breakpoints: { id: string; label: string }[];
  selected: Set<string>;
  onToggleMode: () => void;
  onToggleStep: () => void;
  onToggleBreakpoint: (id: string) => void;
}) {
  return (
    <div className="border-t border-line/60 px-4 py-3 sm:px-5">
      <button
        type="button"
        onClick={props.onToggleMode}
        className="flex w-full items-center gap-2 text-left"
      >
        <span
          className={cn(
            "flex h-5 w-5 items-center justify-center rounded-xs border",
            props.debugMode
              ? "border-volt/60 bg-volt/15 text-volt"
              : "border-line text-ghost/40",
          )}
        >
          <Bug size={12} />
        </span>
        <span className="text-xs font-medium text-ice">调试模式</span>
        <span className="font-mono text-[9px] uppercase text-ghost/50">
          breakpoints / single-step
        </span>
        <span
          className={cn(
            "ml-auto rounded-xs border px-1.5 py-0.5 font-mono text-[8px] uppercase",
            props.debugMode
              ? "border-volt/40 text-volt"
              : "border-line text-ghost/40",
          )}
        >
          {props.debugMode ? "on" : "off"}
        </span>
      </button>

      {props.debugMode && (
        <div className="mt-3 space-y-3 animate-fade-up">
          <label className="flex items-center gap-2 text-[11px] text-ice">
            <input
              type="checkbox"
              className="h-3.5 w-3.5 accent-volt"
              checked={props.singleStep}
              onChange={props.onToggleStep}
            />
            单步执行（每个线性节点都暂停）
          </label>
          {!props.singleStep && (
            <div className="flex flex-wrap gap-1.5">
              {props.breakpoints.map((bp) => {
                const active = props.selected.has(bp.id);
                return (
                  <button
                    key={bp.id}
                    type="button"
                    onClick={() => props.onToggleBreakpoint(bp.id)}
                    className={cn(
                      "rounded-xs border px-2 py-1 font-mono text-[10px] transition",
                      active
                        ? "border-volt/60 bg-volt/15 text-volt"
                        : "border-line text-ghost/60 hover:text-ice",
                    )}
                  >
                    {bp.label}
                  </button>
                );
              })}
              {props.breakpoints.length === 0 && (
                <span className="font-mono text-[10px] text-ghost/40">
                  无可用断点节点
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
