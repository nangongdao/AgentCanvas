import { useState } from "react";
import { ArrowLeft, Loader2, Play } from "lucide-react";

import {
  instantiateWorkflowTemplate,
  type TemplateParameterDTO,
  type WorkflowTemplateDTO,
} from "@/api/endpoints/templates";

interface Props {
  template: WorkflowTemplateDTO;
  onBack: () => void;
  onCreated: (workflowId: string) => Promise<void>;
  onError: (message: string) => void;
}

function initialValue(parameter: TemplateParameterDTO): unknown {
  if (parameter.default !== undefined && parameter.default !== null) return parameter.default;
  return parameter.type === "boolean" ? false : "";
}

export function TemplateInstantiateView(props: Props) {
  const [name, setName] = useState(`${props.template.name} Copy`);
  const [values, setValues] = useState<Record<string, unknown>>(() =>
    Object.fromEntries(
      props.template.parameters.map((parameter) => [parameter.name, initialValue(parameter)]),
    ),
  );
  const [creating, setCreating] = useState(false);

  const create = async () => {
    setCreating(true);
    try {
      const workflow = await instantiateWorkflowTemplate(props.template.id, {
        name: name.trim(),
        parameters: values,
      });
      await props.onCreated(workflow.id);
    } catch (error) {
      props.onError(error instanceof Error ? error.message : String(error));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="flex items-center gap-3 border-b border-line px-4 py-3">
        <button
          type="button"
          onClick={props.onBack}
          className="flex h-8 w-8 items-center justify-center rounded-md text-ghost hover:bg-line hover:text-ice"
          title="返回模板库"
        >
          <ArrowLeft size={15} />
        </button>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-ice">{props.template.name}</p>
          <p className="font-mono text-[9px] text-ghost">
            {props.template.dsl.nodes.length} nodes · {props.template.category}
          </p>
        </div>
      </div>
      <div className="space-y-4 p-4">
        <label className="block text-[10px] text-ghost">
          工作流名称
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="field-input mt-1"
            maxLength={200}
          />
        </label>
        {props.template.parameters.map((parameter) => (
          <label key={parameter.name} className="block text-[10px] text-ghost">
            <span className="flex items-center gap-2">
              {parameter.label}
              {parameter.required && <span className="text-warn">required</span>}
            </span>
            {parameter.type === "boolean" ? (
              <input
                type="checkbox"
                checked={Boolean(values[parameter.name])}
                onChange={(event) =>
                  setValues((current) => ({
                    ...current,
                    [parameter.name]: event.target.checked,
                  }))
                }
                className="mt-2 h-4 w-4 accent-pulse"
              />
            ) : (
              <input
                type={parameter.type === "number" ? "number" : "text"}
                required={parameter.required}
                value={String(values[parameter.name] ?? "")}
                onChange={(event) =>
                  setValues((current) => ({
                    ...current,
                    [parameter.name]: event.target.value,
                  }))
                }
                className="field-input mt-1"
              />
            )}
            {parameter.description && (
              <span className="mt-1 block text-[10px] text-ghost/55">
                {parameter.description}
              </span>
            )}
          </label>
        ))}
        <button
          type="button"
          disabled={creating || !name.trim()}
          onClick={() => void create()}
          className="flex h-9 w-full items-center justify-center gap-2 rounded-md bg-pulse text-xs font-semibold text-void hover:brightness-110 disabled:opacity-40"
        >
          {creating ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
          创建工作流
        </button>
      </div>
    </div>
  );
}
