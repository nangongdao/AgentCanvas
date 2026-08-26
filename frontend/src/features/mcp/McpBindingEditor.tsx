import { useEffect, useMemo, useState } from "react";
import { Cable, Loader2, RefreshCw, Unplug, Wrench } from "lucide-react";

import {
  listMcpServers,
  listMcpTools,
  type McpServerDTO,
  type McpToolDTO,
} from "@/api/endpoints/mcp";
import type { NodeType } from "@/types/dsl";
import { cn } from "@/utils/cn";

interface Props {
  nodeType: NodeType;
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}

interface ToolRef {
  server_id: string;
  tool_name: string;
}

function toolRefs(value: unknown): ToolRef[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is ToolRef =>
      Boolean(
        item &&
          typeof item === "object" &&
          typeof (item as ToolRef).server_id === "string" &&
          typeof (item as ToolRef).tool_name === "string",
      ),
  );
}

export function McpBindingEditor({ nodeType, config, onChange }: Props) {
  const refs = useMemo(() => toolRefs(config.tools), [config.tools]);
  const configuredServer =
    nodeType === "tool" && typeof config.server_id === "string"
      ? config.server_id
      : refs[0]?.server_id;
  const [servers, setServers] = useState<McpServerDTO[]>([]);
  const [serverId, setServerId] = useState(configuredServer ?? "");
  const [tools, setTools] = useState<McpToolDTO[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void listMcpServers()
      .then((rows) => {
        if (!active) return;
        const enabled = rows.filter((row) => row.enabled);
        setServers(enabled);
        setServerId((current) => current || configuredServer || enabled[0]?.id || "");
      })
      .catch((err: unknown) => {
        if (active) setError(String(err));
      });
    return () => {
      active = false;
    };
  }, [configuredServer]);

  const loadTools = async (id: string, refresh = false) => {
    if (!id) {
      setTools([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setTools(await listMcpTools(id, refresh));
    } catch (err) {
      setTools([]);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // Only MCP-binding node types need the tool inventory; fetching for every
    // selected node also hit the admin-scoped tools endpoint as a spurious
    // 403 for editors selecting plain start/end nodes (C5-11).
    if (nodeType !== "tool" && nodeType !== "agent") return;
    void loadTools(serverId);
  }, [nodeType, serverId]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedServer = servers.find((server) => server.id === serverId);

  if (nodeType !== "tool" && nodeType !== "agent") return null;

  return (
    <section className="border-y border-line/70 py-3">
      <div className="mb-2.5 flex items-center gap-2">
        <Cable size={12} className="text-pulse" />
        <span className="font-mono text-[9px] uppercase tracking-[0.25em] text-ghost">
          MCP Binding
        </span>
        <button
          type="button"
          onClick={() => void loadTools(serverId, true)}
          disabled={!serverId || loading}
          className="ml-auto flex h-7 w-7 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-pulse disabled:opacity-40"
          title="刷新工具列表"
        >
          <RefreshCw size={12} className={cn(loading && "animate-spin")} />
        </button>
      </div>

      <label className="flex flex-col gap-1.5">
        <span className="text-[10px] text-ghost/70">服务</span>
        <select
          value={serverId}
          onChange={(event) => {
            const next = event.target.value;
            setServerId(next);
            if (nodeType === "tool") {
              onChange({ ...config, server_id: next, tool_name: "" });
            }
          }}
          className="rounded-lg border border-line bg-void/80 px-2.5 py-2 text-xs text-ice outline-hidden transition focus:border-pulse/60"
        >
          <option value="">选择 MCP 服务</option>
          {servers.map((server) => (
            <option key={server.id} value={server.id}>
              {server.name} · {server.transport}
            </option>
          ))}
        </select>
      </label>

      {selectedServer && (
        <div className="mt-1.5 flex items-center gap-1.5 font-mono text-[9px] text-ghost/60">
          <span
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              selectedServer.connected ? "bg-ok" : "bg-ghost/50",
            )}
          />
          {selectedServer.connected ? "connected" : selectedServer.last_status || "idle"}
        </div>
      )}

      {nodeType === "tool" ? (
        <label className="mt-3 flex flex-col gap-1.5">
          <span className="text-[10px] text-ghost/70">工具</span>
          <select
            value={typeof config.tool_name === "string" ? config.tool_name : ""}
            onChange={(event) => onChange({ ...config, tool_name: event.target.value })}
            disabled={!serverId || loading}
            className="rounded-lg border border-line bg-void/80 px-2.5 py-2 text-xs text-ice outline-hidden transition focus:border-pulse/60 disabled:opacity-50"
          >
            <option value="">选择工具</option>
            {tools.map((tool) => (
              <option key={tool.name} value={tool.name}>
                {tool.name}
              </option>
            ))}
          </select>
          {tools.find((tool) => tool.name === config.tool_name)?.description && (
            <span className="text-[10px] leading-relaxed text-ghost/60">
              {tools.find((tool) => tool.name === config.tool_name)?.description}
            </span>
          )}
        </label>
      ) : (
        <div className="mt-3">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[10px] text-ghost/70">Agent 可调用工具</span>
            <span className="font-mono text-[9px] text-ghost/50">{refs.length} bound</span>
          </div>
          <div className="max-h-36 space-y-1 overflow-auto">
            {tools.map((tool) => {
              const checked = refs.some(
                (ref) => ref.server_id === serverId && ref.tool_name === tool.name,
              );
              return (
                <label
                  key={tool.name}
                  className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 transition hover:bg-line/50"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => {
                      const next = checked
                        ? refs.filter(
                            (ref) =>
                              !(ref.server_id === serverId && ref.tool_name === tool.name),
                          )
                        : [...refs, { server_id: serverId, tool_name: tool.name }];
                      onChange({ ...config, agent_mode: "react", tools: next });
                    }}
                    className="mt-0.5 accent-cyan-400"
                  />
                  <span className="min-w-0">
                    <span className="flex items-center gap-1.5 font-mono text-[10px] text-ice/90">
                      <Wrench size={10} className="text-volt" /> {tool.name}
                    </span>
                    <span className="line-clamp-2 text-[9px] leading-relaxed text-ghost/60">
                      {tool.description || "No description"}
                    </span>
                  </span>
                </label>
              );
            })}
            {!loading && serverId && tools.length === 0 && (
              <p className="flex items-center gap-1.5 px-2 py-2 text-[10px] text-ghost/50">
                <Unplug size={11} /> 未发现工具
              </p>
            )}
            {loading && (
              <p className="flex items-center gap-1.5 px-2 py-2 text-[10px] text-ghost/50">
                <Loader2 size={11} className="animate-spin" /> 正在发现工具
              </p>
            )}
          </div>
        </div>
      )}

      {error && <p className="mt-2 text-[10px] leading-relaxed text-bad">{error}</p>}
    </section>
  );
}
