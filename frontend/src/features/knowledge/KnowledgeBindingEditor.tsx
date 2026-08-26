import { useEffect, useMemo, useState } from "react";
import { Database, ExternalLink, Loader2 } from "lucide-react";
import { Link } from "react-router-dom";

import {
  listKnowledgeBases,
  type KnowledgeBaseDTO,
} from "@/api/endpoints/knowledge";
import { useWorkflowStore } from "@/stores/workflowStore";
import type { NodeType } from "@/types/dsl";

interface Props {
  nodeType: NodeType;
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}

export function KnowledgeBindingEditor(props: Props) {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseDTO[]>([]);
  const [loading, setLoading] = useState(false);
  const nodes = useWorkflowStore((state) => state.nodes);
  const ragNodes = useMemo(
    () => nodes.filter((node) => node.data.nodeType === "rag"),
    [nodes],
  );
  const contextNodes = useMemo(
    () =>
      Array.isArray(props.config.context_nodes)
        ? props.config.context_nodes.filter(
            (value): value is string => typeof value === "string",
          )
        : [],
    [props.config.context_nodes],
  );

  useEffect(() => {
    if (props.nodeType !== "rag") return;
    let active = true;
    setLoading(true);
    void listKnowledgeBases({ limit: 200 })
      .then((page) => {
        if (active) setKnowledgeBases(page.items);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [props.nodeType]);

  if (props.nodeType === "rag") {
    const selected = knowledgeBases.find((row) => row.id === props.config.kb_id);
    return (
      <section className="border-b border-line/70 pb-4">
        <div className="mb-2 flex items-center gap-2">
          <Database size={12} className="text-ok" />
          <span className="font-mono text-[9px] uppercase text-ghost">Knowledge Base</span>
          <Link
            to="/knowledge"
            className="ml-auto flex h-7 w-7 items-center justify-center rounded-md text-ghost transition hover:bg-ok/10 hover:text-ok"
            title="打开知识库"
          >
            <ExternalLink size={12} />
          </Link>
        </div>
        {loading ? (
          <div className="flex h-9 items-center gap-2 text-xs text-ghost">
            <Loader2 size={12} className="animate-spin" /> loading
          </div>
        ) : (
          <select
            className="field-input h-9"
            value={typeof props.config.kb_id === "string" ? props.config.kb_id : ""}
            onChange={(event) =>
              props.onChange({ ...props.config, kb_id: event.target.value })
            }
          >
            <option value="">选择知识库</option>
            {knowledgeBases.map((row) => (
              <option key={row.id} value={row.id}>
                {row.name} / {row.document_count} docs
              </option>
            ))}
          </select>
        )}
        {selected && (
          <p className="mt-1.5 truncate font-mono text-[9px] text-ghost/45">
            {selected.embedding_model_id} / {selected.chunk_size}:{selected.chunk_overlap}
          </p>
        )}
      </section>
    );
  }

  if (props.nodeType !== "agent") return null;

  return (
    <section className="border-b border-line/70 pb-4">
      <div className="mb-2 flex items-center gap-2">
        <Database size={12} className="text-ok" />
        <span className="font-mono text-[9px] uppercase text-ghost">RAG Context</span>
        <span className="ml-auto font-mono text-[9px] text-ghost/45">
          {contextNodes.length}/{ragNodes.length}
        </span>
      </div>
      <div className="space-y-1">
        {ragNodes.map((node) => {
          const checked = contextNodes.includes(node.id);
          return (
            <label
              key={node.id}
              className="flex min-h-8 cursor-pointer items-center gap-2 rounded-md px-2 text-xs text-ghost transition hover:bg-line/50 hover:text-ice"
            >
              <input
                type="checkbox"
                checked={checked}
                className="h-3.5 w-3.5 accent-emerald-400"
                onChange={(event) =>
                  props.onChange({
                    ...props.config,
                    context_nodes: event.target.checked
                      ? [...contextNodes, node.id]
                      : contextNodes.filter((id) => id !== node.id),
                  })
                }
              />
              <span className="min-w-0 flex-1 truncate">{node.data.label}</span>
              <span className="font-mono text-[9px] text-ghost/40">{node.id}</span>
            </label>
          );
        })}
        {ragNodes.length === 0 && (
          <p className="py-2 text-[10px] text-ghost/45">当前画布无 RAG 节点</p>
        )}
      </div>
    </section>
  );
}
