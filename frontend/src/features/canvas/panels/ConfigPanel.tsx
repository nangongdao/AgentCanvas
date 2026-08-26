import { memo, useEffect, useMemo, useState } from "react";
import {
  Braces,
  ChevronDown,
  Loader2,
  PanelRightClose,
  PanelRightOpen,
  Settings2,
} from "lucide-react";

import { listNodeTypes, type JsonSchema } from "@/api/endpoints/meta";
import { McpBindingEditor } from "@/features/mcp/McpBindingEditor";
import { KnowledgeBindingEditor } from "@/features/knowledge/KnowledgeBindingEditor";
import { DataFlowPanel } from "@/features/canvas/panels/DataFlowPanel";
import { NodeDryRunPanel } from "@/features/canvas/panels/NodeDryRunPanel";
import { SchemaForm } from "@/features/canvas/panels/SchemaForm";
import { IterationConfigEditor } from "@/features/canvas/panels/IterationConfigEditor";
import { useWorkflowStore } from "@/stores/workflowStore";
import { NODE_META, type NodeType } from "@/types/dsl";
import { cn } from "@/utils/cn";

const HIDDEN_SCHEMA_FIELDS: Partial<Record<NodeType, string[]>> = {
  agent: ["tools", "context_nodes"],
  tool: ["server_id", "tool_name"],
  rag: ["kb_id"],
  iteration: [
    "items",
    "item_variable",
    "index_variable",
    "batch_size",
    "concurrency_limit",
    "failure_strategy",
    "recursion_limit",
    "subgraph",
  ],
};

function ConfigPanelComponent({ ensureWorkflowId }: { ensureWorkflowId?: () => Promise<string | null> }) {
  const selectedNodeId = useWorkflowStore((state) => state.selectedNodeId);
  const selectedEdgeId = useWorkflowStore((state) => state.selectedEdgeId);
  const selectedEdge = useWorkflowStore(
    (state) => state.edges.find((candidate) => candidate.id === selectedEdgeId),
  );
  const nodeData = useWorkflowStore(
    (state) => state.nodes.find((candidate) => candidate.id === selectedNodeId)?.data,
  );
  const updateNodeLabel = useWorkflowStore((state) => state.updateNodeLabel);
  const updateNodeConfig = useWorkflowStore((state) => state.updateNodeConfig);
  const updateEdgeLabel = useWorkflowStore((state) => state.updateEdgeLabel);
  const [collapsed, setCollapsed] = useState(() =>
    window.matchMedia("(max-width: 1100px)").matches,
  );
  const [schemas, setSchemas] = useState<Partial<Record<NodeType, JsonSchema>>>({});
  const [schemaLoading, setSchemaLoading] = useState(true);
  const [schemaError, setSchemaError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [jsonOk, setJsonOk] = useState(true);

  useEffect(() => {
    let active = true;
    void listNodeTypes()
      .then((rows) => {
        if (!active) return;
        setSchemas(
          Object.fromEntries(
            rows.map((row) => [row.type, row.config_schema]),
          ) as Partial<Record<NodeType, JsonSchema>>,
        );
        setSchemaError(null);
      })
      .catch((error: unknown) => {
        if (active) setSchemaError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (active) setSchemaLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!nodeData) return;
    setDraft(JSON.stringify(nodeData.config, null, 2));
    setJsonOk(true);
  }, [selectedNodeId]); // eslint-disable-line react-hooks/exhaustive-deps

  const schema = nodeData ? schemas[nodeData.nodeType] : undefined;
  const nodeMeta = nodeData
    ? NODE_META[nodeData.nodeType] ?? {
        label: nodeData.nodeType,
        color: "#a78bfa",
        description: "外部插件节点",
      }
    : undefined;
  const hiddenKeys = useMemo(
    () => (nodeData ? HIDDEN_SCHEMA_FIELDS[nodeData.nodeType] ?? [] : []),
    [nodeData?.nodeType],
  );

  const applyConfig = (config: Record<string, unknown>) => {
    if (!selectedNodeId) return;
    setDraft(JSON.stringify(config, null, 2));
    setJsonOk(true);
    updateNodeConfig(selectedNodeId, config);
  };

  if (collapsed) {
    return (
      <aside className="glass z-10 flex w-12 flex-col items-center border-l border-line py-3">
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice"
          title="展开配置面板"
        >
          <PanelRightOpen size={16} />
        </button>
      </aside>
    );
  }

  return (
    <aside className="glass z-10 flex w-80 flex-col border-l border-line">
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <div className="flex items-center gap-2">
          <Settings2 size={14} className="text-ghost" />
          <h2 className="font-mono text-[9px] uppercase tracking-[0.3em] text-ghost">
            Inspector
          </h2>
        </div>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          className="flex h-7 w-7 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice"
          title="收起"
        >
          <PanelRightClose size={14} />
        </button>
      </div>

      {!nodeData || !selectedNodeId ? (
        selectedEdge && selectedEdgeId ? (
          <div className="min-h-0 flex-1 overflow-auto animate-fade-up">
            <div className="flex items-center gap-2 border-b border-line/70 px-4 py-3">
              <span className="h-2 w-2 rounded-full bg-pulse" />
              <span className="font-mono text-[10px] uppercase tracking-widest text-ghost">EDGE LABEL</span>
              <span className="ml-auto font-mono text-[10px] text-ghost/50">{selectedEdgeId}</span>
            </div>
            <div className="space-y-4 p-4">
              <label className="flex flex-col gap-1.5">
                <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-ghost">边标签</span>
                <input
                  aria-label="边标签"
                  className="field-input h-9 text-sm"
                  value={typeof selectedEdge.label === "string" ? selectedEdge.label : ""}
                  onChange={(event) => updateEdgeLabel(selectedEdgeId, event.target.value)}
                  placeholder="例如：成功 / 失败 / 默认"
                />
              </label>
              <p className="border-l-2 border-pulse/50 px-3 text-xs leading-5 text-ghost/70">
                标签会随工作流 DSL 保存，并出现在版本差异中。
              </p>
            </div>
          </div>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-md border border-dashed border-line text-ghost/50">
              <Braces size={18} strokeWidth={1.5} />
            </span>
            <p className="text-xs leading-relaxed text-ghost/70">
              选中画布中的节点或连线
              <br />
              即可编辑
            </p>
          </div>
        )
      ) : (
        <div className="min-h-0 flex-1 overflow-auto animate-fade-up">
          <div className="flex items-center gap-2 border-b border-line/70 px-4 py-3">
            <span
              className="h-2 w-2 rounded-full"
              style={{ backgroundColor: nodeMeta?.color }}
            />
            <span className="font-mono text-[10px] uppercase tracking-widest text-ghost">
              {nodeData.nodeType}
            </span>
            <span className="ml-auto font-mono text-[10px] text-ghost/50">
              {selectedNodeId}
            </span>
          </div>

          <div className="space-y-5 p-4">
            <label className="flex flex-col gap-1.5">
              <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-ghost">
                显示名称
              </span>
              <input
                className="field-input h-9 text-sm"
                value={nodeData.label}
                onChange={(event) => updateNodeLabel(selectedNodeId, event.target.value)}
              />
            </label>

            <McpBindingEditor
              nodeType={nodeData.nodeType}
              config={nodeData.config}
              onChange={applyConfig}
            />

            <KnowledgeBindingEditor
              nodeType={nodeData.nodeType}
              config={nodeData.config}
              onChange={applyConfig}
            />

            {nodeData.nodeType === "iteration" && (
              <IterationConfigEditor config={nodeData.config} onChange={applyConfig} />
            )}

            {schemaLoading ? (
              <div className="flex items-center gap-2 py-6 text-xs text-ghost">
                <Loader2 size={14} className="animate-spin text-pulse" /> 加载配置结构
              </div>
            ) : schema ? (
              <SchemaForm
                schema={schema}
                value={nodeData.config}
                onChange={applyConfig}
                hiddenKeys={hiddenKeys}
              />
            ) : (
              <p className="border-l-2 border-warn px-3 text-xs text-warn">
                {schemaError ?? "此节点没有可用的配置结构"}
              </p>
            )}

            {ensureWorkflowId && (
              <NodeDryRunPanel ensureWorkflowId={ensureWorkflowId} />
            )}

            <DataFlowPanel
              nodeType={nodeData.nodeType}
              config={nodeData.config}
              nodeId={selectedNodeId}
            />

            <details className="group border-t border-line pt-3">
              <summary className="flex cursor-pointer list-none items-center gap-2 font-mono text-[9px] uppercase tracking-[0.2em] text-ghost transition hover:text-ice">
                <ChevronDown
                  size={12}
                  className="transition group-open:rotate-180"
                />
                高级配置 JSON
                <span
                  className={cn(
                    "ml-auto rounded-xs px-1.5 py-0.5 text-[8px] normal-case tracking-normal",
                    jsonOk ? "bg-ok/10 text-ok" : "bg-bad/10 text-bad",
                  )}
                >
                  {jsonOk ? "valid" : "invalid"}
                </span>
              </summary>
              <textarea
                className={cn(
                  "mt-3 min-h-72 w-full resize-y rounded-md border bg-void/70 px-3 py-2.5",
                  "font-mono text-[10px] leading-5 text-ice/90 outline-hidden transition",
                  jsonOk
                    ? "border-line focus:border-pulse/60"
                    : "border-bad/50 focus:border-bad",
                )}
                spellCheck={false}
                value={draft}
                onChange={(event) => {
                  setDraft(event.target.value);
                  try {
                    const parsed = JSON.parse(event.target.value) as Record<
                      string,
                      unknown
                    >;
                    setJsonOk(true);
                    updateNodeConfig(selectedNodeId, parsed);
                  } catch {
                    setJsonOk(false);
                  }
                }}
              />
            </details>
          </div>
        </div>
      )}
    </aside>
  );
}

export const ConfigPanel = memo(ConfigPanelComponent);
