import { useState, type FormEvent } from "react";
import { Loader2, Save } from "lucide-react";

import { createWorkflowTemplate } from "@/api/endpoints/templates";

interface Props {
  workflowId: string;
  defaultName: string;
  onCreated: () => Promise<void>;
  onCancel: () => void;
  onError: (message: string) => void;
}

export function TemplateCreateForm(props: Props) {
  const [name, setName] = useState(`${props.defaultName} Template`);
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("general");
  const [tags, setTags] = useState("");
  const [saving, setSaving] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      await createWorkflowTemplate({
        name: name.trim(),
        description: description.trim(),
        category: category.trim() || "general",
        tags: tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
        workflow_id: props.workflowId,
      });
      await props.onCreated();
    } catch (error) {
      props.onError(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={(event) => void submit(event)} className="border-b border-line bg-void/45 p-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-[10px] text-ghost">
          名称
          <input
            required
            maxLength={200}
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="field-input mt-1"
          />
        </label>
        <label className="text-[10px] text-ghost">
          分类
          <input
            required
            maxLength={64}
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            className="field-input mt-1"
          />
        </label>
        <label className="text-[10px] text-ghost sm:col-span-2">
          标签
          <input
            value={tags}
            onChange={(event) => setTags(event.target.value)}
            className="field-input mt-1"
            placeholder="agent, review, team"
          />
        </label>
        <label className="text-[10px] text-ghost sm:col-span-2">
          描述
          <textarea
            maxLength={4000}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            className="field-input mt-1 min-h-18 resize-y"
          />
        </label>
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          onClick={props.onCancel}
          className="h-8 rounded-md border border-line px-3 text-xs text-ghost hover:text-ice"
        >
          取消
        </button>
        <button
          type="submit"
          disabled={saving || !name.trim()}
          className="flex h-8 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void disabled:opacity-40"
        >
          {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
          保存模板
        </button>
      </div>
    </form>
  );
}
