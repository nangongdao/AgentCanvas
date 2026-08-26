import { useEffect, useState } from "react";
import {
  Bot,
  ChevronLeft,
  Code2,
  Database,
  FlagTriangleRight,
  GitBranch,
  Globe,
  Layers,
  Play,
  Puzzle,
  Repeat2,
  Split,
  UserCheck,
  Workflow,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import { listNodeTypes, type JsonSchema, type NodeTypeDTO } from "@/api/endpoints/meta";
import type { NodeType } from "@/types/dsl";
import { NODE_META } from "@/types/dsl";
import { cn } from "@/utils/cn";

type PaletteItem = {
  type: NodeType;
  icon: LucideIcon;
  index: string;
  label: string;
  color: string;
  description: string;
  config?: Record<string, unknown>;
};

const BUILTIN_PALETTE: PaletteItem[] = [
  { type: "start", icon: Play, index: "01" },
  { type: "agent", icon: Bot, index: "02" },
  { type: "tool", icon: Wrench, index: "03" },
  { type: "condition", icon: GitBranch, index: "04" },
  { type: "switch", icon: Split, index: "05" },
  { type: "rag", icon: Database, index: "06" },
  { type: "human", icon: UserCheck, index: "07" },
  { type: "iteration", icon: Repeat2, index: "08" },
  { type: "http", icon: Globe, index: "09" },
  { type: "code", icon: Code2, index: "10" },
  { type: "subworkflow", icon: Workflow, index: "11" },
  { type: "end", icon: FlagTriangleRight, index: "12" },
].map((item): PaletteItem => ({
  ...item,
  label: NODE_META[item.type].label,
  color: NODE_META[item.type].color,
  description: NODE_META[item.type].description,
}));

function schemaDefaults(schema: JsonSchema): Record<string, unknown> {
  const required = new Set(schema.required ?? []);
  const defaults: Record<string, unknown> = {};
  for (const [name, field] of Object.entries(schema.properties ?? {})) {
    if (field.default !== undefined) {
      defaults[name] = structuredClone(field.default);
      continue;
    }
    if (!required.has(name)) continue;
    const type = Array.isArray(field.type) ? field.type[0] : field.type;
    defaults[name] = type === "boolean" ? false : type === "array" ? [] : type === "object" ? {} : "";
  }
  return defaults;
}

function pluginPaletteItem(row: NodeTypeDTO, index: number): PaletteItem {
  const hints = row.plugin?.ui_hints;
  return {
    type: row.type,
    icon: Puzzle,
    index: `P${index + 1}`,
    label: row.label,
    color: hints?.color ?? "#a78bfa",
    description: row.description ?? "外部插件节点",
    config: schemaDefaults(row.config_schema),
  };
}

interface Props {
  onAdd: (type: NodeType, config?: Record<string, unknown>, label?: string) => void;
}

export function NodePalette({ onAdd }: Props) {
  const [collapsed, setCollapsed] = useState(() =>
    window.matchMedia("(max-width: 900px)").matches,
  );
  const [plugins, setPlugins] = useState<PaletteItem[]>([]);

  useEffect(() => {
    let active = true;
    void listNodeTypes()
      .then((rows) => {
        if (active) setPlugins(rows.filter((row) => row.plugin).map(pluginPaletteItem));
      })
      .catch(() => {
        // The canvas remains usable with built-ins when metadata discovery is unavailable.
      });
    return () => {
      active = false;
    };
  }, []);

  const palette = [...BUILTIN_PALETTE, ...plugins];

  if (collapsed) {
    return (
      <aside className="glass z-10 flex w-14 flex-col items-center gap-1 border-r border-line py-3">
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          className="mb-2 flex h-8 w-8 items-center justify-center rounded-lg text-ghost transition hover:bg-line hover:text-ice"
          title="展开节点面板"
        >
          <Layers size={16} />
        </button>
        {palette.map(({ type, icon: Icon, label, color, config }) => (
          <button
            key={type}
            type="button"
            onClick={() => onAdd(type, config, label)}
            title={label}
            className="flex h-9 w-9 items-center justify-center rounded-lg text-ghost transition-all hover:scale-110 hover:bg-line"
            style={{ color }}
          >
            <Icon size={15} strokeWidth={1.8} />
          </button>
        ))}
      </aside>
    );
  }

  return (
    <aside className="glass z-10 flex w-56 flex-col border-r border-line">
      <div className="flex items-center justify-between px-4 pb-1 pt-4">
        <div>
          <h2 className="font-mono text-[9px] uppercase tracking-[0.3em] text-ghost">
            Node Library
          </h2>
          <p className="mt-0.5 text-[11px] text-ghost/60">点击加入画布</p>
        </div>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          className="flex h-7 w-7 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice"
          title="收起"
        >
          <ChevronLeft size={14} />
        </button>
      </div>

      <div className="mt-2 flex flex-1 flex-col gap-1 overflow-auto px-3 pb-4">
        {palette.map(({ type, icon: Icon, index, label, color, description, config }, i) => {
          return (
            <button
              key={type}
              type="button"
              onClick={() => onAdd(type, config, label)}
              className={cn(
                "group relative flex items-center gap-3 overflow-hidden rounded-xl border border-transparent px-3 py-2.5",
                "text-left transition-all duration-300 animate-slide-in",
                "hover:border-line hover:bg-ink/80",
              )}
              style={{ animationDelay: `${i * 40}ms` }}
            >
              <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 font-mono text-2xl font-bold text-line/60 transition-colors group-hover:text-line">
                {index}
              </span>
              <span
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-line bg-void/70 transition-transform duration-300 group-hover:scale-110 group-hover:rotate-3"
                style={{ color }}
              >
                <Icon size={16} strokeWidth={1.8} />
              </span>
              <span className="relative flex flex-col">
                <span className="text-[13px] font-medium text-ice">{label}</span>
                <span className="text-[10px] text-ghost/70">{description}</span>
              </span>
            </button>
          );
        })}
      </div>

      <div className="border-t border-line px-4 py-3">
        <p className="font-mono text-[9px] uppercase tracking-[0.25em] text-ghost/50">
          MCP · LangGraph · SSE
          {plugins.length > 0 && <span className="ml-2 text-volt">· {plugins.length} plugin</span>}
        </p>
      </div>
    </aside>
  );
}
