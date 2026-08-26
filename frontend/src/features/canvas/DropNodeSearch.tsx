import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import {
  Code2,
  Database,
  FlagTriangleRight,
  GitBranch,
  Globe,
  Layers,
  Puzzle,
  Repeat2,
  Search,
  Split,
  UserCheck,
  Wrench,
  Workflow,
  type LucideIcon,
} from "lucide-react";

import { listNodeTypes, type JsonSchema, type NodeTypeDTO } from "@/api/endpoints/meta";
import type { NodeType } from "@/types/dsl";
import { NODE_META } from "@/types/dsl";
import { useWorkflowStore } from "@/stores/workflowStore";
import { cn } from "@/utils/cn";

type PaletteItem = {
  type: NodeType;
  icon: LucideIcon;
  label: string;
  color: string;
  description: string;
  config?: Record<string, unknown>;
};

const BUILTIN_PALETTE: PaletteItem[] = [
  { type: "agent", icon: Layers, label: NODE_META.agent.label, color: NODE_META.agent.color, description: NODE_META.agent.description },
  { type: "tool", icon: Wrench, label: NODE_META.tool.label, color: NODE_META.tool.color, description: NODE_META.tool.description },
  { type: "condition", icon: GitBranch, label: NODE_META.condition.label, color: NODE_META.condition.color, description: NODE_META.condition.description },
  { type: "switch", icon: Split, label: NODE_META.switch.label, color: NODE_META.switch.color, description: NODE_META.switch.description },
  { type: "rag", icon: Database, label: NODE_META.rag.label, color: NODE_META.rag.color, description: NODE_META.rag.description },
  { type: "human", icon: UserCheck, label: NODE_META.human.label, color: NODE_META.human.color, description: NODE_META.human.description },
  { type: "iteration", icon: Repeat2, label: NODE_META.iteration.label, color: NODE_META.iteration.color, description: NODE_META.iteration.description },
  { type: "http", icon: Globe, label: NODE_META.http.label, color: NODE_META.http.color, description: NODE_META.http.description },
  { type: "code", icon: Code2, label: NODE_META.code.label, color: NODE_META.code.color, description: NODE_META.code.description },
  { type: "subworkflow", icon: Workflow, label: NODE_META.subworkflow.label, color: NODE_META.subworkflow.color, description: NODE_META.subworkflow.description },
  { type: "end", icon: FlagTriangleRight, label: NODE_META.end.label, color: NODE_META.end.color, description: NODE_META.end.description },
];

const ICON_BY_TYPE: Record<string, LucideIcon> = {
  agent: Layers,
  tool: Wrench,
  condition: GitBranch,
  switch: Split,
  rag: Database,
  human: UserCheck,
  iteration: Repeat2,
  http: Globe,
  code: Code2,
  subworkflow: Workflow,
  end: FlagTriangleRight,
};

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

function pluginItem(row: NodeTypeDTO): PaletteItem {
  const hints = row.plugin?.ui_hints;
  return {
    type: row.type,
    icon: Puzzle,
    label: row.label,
    color: hints?.color ?? "#a78bfa",
    description: row.description ?? "外部插件节点",
    config: schemaDefaults(row.config_schema),
  };
}

interface Props {
  /** Screen (clientX/Y) coordinates where the connection drag ended. */
  clientPosition: { x: number; y: number };
  /** Node id the connection was dragged from, if any (null when dragging
   * from an unanchored source, which is not currently produced). */
  sourceNodeId: string | null;
  screenToFlowPosition: (client: { x: number; y: number }) => { x: number; y: number };
  onClose: () => void;
}

/**
 * C5-7: when the user drags a connection from a node's source handle and
 * releases on empty canvas, this palette opens at the drop point. Picking a
 * node type inserts it at the drop position and wires the dragged source into
 * it in one motion, so connecting to a not-yet-created node is a single
 * gesture instead of add-node-then-drag-again.
 */
export function DropNodeSearch({
  clientPosition,
  sourceNodeId,
  screenToFlowPosition,
  onClose,
}: Props) {
  const addNode = useWorkflowStore((state) => state.addNode);
  const onConnect = useWorkflowStore((state) => state.onConnect);
  const [query, setQuery] = useState("");
  const [plugins, setPlugins] = useState<PaletteItem[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    let active = true;
    void listNodeTypes()
      .then((rows) => {
        if (active) setPlugins(rows.filter((row) => row.plugin).map(pluginItem));
      })
      .catch(() => {
        // Drop-to-create still works with built-ins when metadata is unavailable.
      });
    return () => {
      active = false;
    };
  }, []);

  const palette = useMemo(() => [...BUILTIN_PALETTE, ...plugins], [plugins]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return palette;
    return palette.filter(
      (item) =>
        item.label.toLowerCase().includes(q) ||
        item.type.toLowerCase().includes(q) ||
        item.description.toLowerCase().includes(q),
    );
  }, [palette, query]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  const commit = useCallback(
    (item: PaletteItem) => {
      const flowPosition = screenToFlowPosition(clientPosition);
      // Offset so the dropped node's left handle sits roughly where the
      // pointer released, not its top-left corner.
      const position = { x: flowPosition.x - 84, y: flowPosition.y - 36 };
      const sourceType = sourceNodeId
        ? useWorkflowStore.getState().nodes.find((node) => node.id === sourceNodeId)?.data.nodeType ?? null
        : null;
      // ``end`` has no outbound source handle (cannot be a connection source)
      // and ``start`` accepts no inbound edge (cannot be a target). Inserting an
      // ``end`` as the drop target is valid — the source wires into it.
      const wireable =
        sourceNodeId !== null && sourceType !== "end" && item.type !== "start";
      addNode(item.type, position, item.config, item.label);
      if (!wireable || !sourceNodeId) {
        onClose();
        return;
      }
      // addNode generates a fresh id we cannot predict; read it back from the
      // store as the sole selected node immediately after insertion.
      const state = useWorkflowStore.getState();
      const newId = state.selectedNodeId;
      if (newId) {
        onConnect({
          source: sourceNodeId,
          target: newId,
          sourceHandle: null,
          targetHandle: null,
        });
      }
      onClose();
    },
    [addNode, clientPosition, onConnect, onClose, screenToFlowPosition, sourceNodeId],
  );

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => Math.min(index + 1, filtered.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const item = filtered[activeIndex];
      if (item) commit(item);
    } else if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  };

  useEffect(() => {
    const item = filtered[activeIndex];
    if (!item || !listRef.current) return;
    const el = listRef.current.querySelector<HTMLElement>(`[data-index="${activeIndex}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, filtered]);

  return (
    <div
      className="fixed z-50 w-72"
      style={{ left: clientPosition.x, top: clientPosition.y }}
      role="dialog"
      aria-label="插入节点并连接"
    >
      <div className="glass overflow-hidden rounded-xl border border-line shadow-card">
        <div className="flex items-center gap-2 border-b border-line/70 px-3 py-2">
          <Search size={13} className="text-pulse" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="搜索要插入的节点…"
            aria-label="节点搜索"
            className="min-w-0 flex-1 bg-transparent text-xs text-ice outline-hidden placeholder:text-ghost/40"
          />
          <kbd className="font-mono text-[9px] text-ghost/50">↑↓ Enter Esc</kbd>
        </div>
        <ul ref={listRef} className="max-h-64 overflow-auto py-1">
          {filtered.map((item, index) => {
            const Icon = ICON_BY_TYPE[item.type] ?? Puzzle;
            return (
              <li key={item.type}>
                <button
                  type="button"
                  data-index={index}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => commit(item)}
                  className={cn(
                    "flex w-full items-center gap-2.5 px-3 py-2 text-left transition",
                    index === activeIndex ? "bg-pulse/10" : "hover:bg-line/40",
                  )}
                >
                  <span
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line bg-void/60"
                    style={{ color: item.color }}
                  >
                    <Icon size={13} strokeWidth={1.8} />
                  </span>
                  <span className="flex min-w-0 flex-col">
                    <span className="truncate text-[12px] font-medium text-ice">{item.label}</span>
                    <span className="truncate text-[10px] text-ghost/65">{item.description}</span>
                  </span>
                </button>
              </li>
            );
          })}
          {filtered.length === 0 && (
            <li className="px-3 py-6 text-center text-[10px] text-ghost/55">无匹配节点</li>
          )}
        </ul>
      </div>
    </div>
  );
}
