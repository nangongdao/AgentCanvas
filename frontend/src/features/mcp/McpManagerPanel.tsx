import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { CirclePlus, Network, RefreshCw, ServerCog, X } from "lucide-react";

import { ApiError } from "@/api/client";
import {
  createMcpServer,
  deleteMcpServer,
  bindMcpServerCatalog,
  healthMcpServer,
  listMcpCatalog,
  listMcpServers,
  updateMcpServer,
  type McpServerDTO,
  type McpCatalogEntryDTO,
  type McpToolDTO,
} from "@/api/endpoints/mcp";
import { useDialogFocus } from "@/components/useDialogFocus";
import {
  draftFromServer,
  draftToInput,
  EMPTY_MCP_DRAFT,
  McpServerEditor,
  type McpServerDraft,
  type McpWorking,
} from "@/features/mcp/McpServerEditor";
import { cn } from "@/utils/cn";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string"
      ? error.detail
      : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

export function McpManagerPanel() {
  const [open, setOpen] = useState(false);
  const [servers, setServers] = useState<McpServerDTO[]>([]);
  const [catalog, setCatalog] = useState<McpCatalogEntryDTO[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [catalogVersionId, setCatalogVersionId] = useState<string | null>(null);
  const [draft, setDraft] = useState<McpServerDraft>(EMPTY_MCP_DRAFT);
  const [tools, setTools] = useState<McpToolDTO[]>([]);
  const [loading, setLoading] = useState(false);
  const [working, setWorking] = useState<McpWorking>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const dialogRef = useDialogFocus<HTMLDivElement>({
    open,
    onClose: () => setOpen(false),
    escapeEnabled: working === null,
  });

  const selected = useMemo(
    () => servers.find((server) => server.id === selectedId),
    [servers, selectedId],
  );

  const choose = (server: McpServerDTO) => {
    setSelectedId(server.id);
    setDraft(draftFromServer(server));
    setCatalogVersionId(server.catalog_version_id ?? null);
    setTools(server.tools ?? []);
    setError(null);
    setMessage(null);
  };

  const load = async (preferredId?: string) => {
    setLoading(true);
    setError(null);
    try {
      const [rows, catalogRows] = await Promise.all([listMcpServers(), listMcpCatalog()]);
      setServers(rows);
      setCatalog(catalogRows);
      const next =
        rows.find((server) => server.id === preferredId) ??
        rows.find((server) => server.id === selectedId) ??
        rows[0];
      if (next) choose(next);
      else {
        setSelectedId(null);
        setCatalogVersionId(null);
        setDraft(EMPTY_MCP_DRAFT);
        setTools([]);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) void load();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const startNew = () => {
    setSelectedId(null);
    setCatalogVersionId(null);
    setDraft(EMPTY_MCP_DRAFT);
    setTools([]);
    setError(null);
    setMessage(null);
  };

  const save = async () => {
    setWorking("save");
    setError(null);
    setMessage(null);
    try {
      if (!draft.name.trim()) throw new Error("请输入服务名称");
      const body = draftToInput(draft);
      const row = selectedId
        ? await updateMcpServer(selectedId, body)
        : await createMcpServer(body);
      await load(row.id);
      setMessage(selectedId ? "配置已保存，连接缓存已重置" : "MCP 服务已创建");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setWorking(null);
    }
  };

  const probe = async () => {
    if (!selectedId) return;
    setWorking("test");
    setError(null);
    setMessage(null);
    try {
      const result = await healthMcpServer(selectedId);
      await load(selectedId);
      setTools(result.tools ?? []);
      const circuit = result.resilience?.state ?? "closed";
      if (result.state !== "healthy") throw new Error(result.error ?? "健康检查失败");
      setMessage(
        `健康检查通过，发现 ${result.tool_count ?? 0} 个工具，熔断状态 ${circuit}`,
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setWorking(null);
    }
  };

  const remove = async () => {
    if (!selectedId || !selected) return;
    if (!window.confirm(`删除 MCP 服务“${selected.name}”？`)) return;
    setWorking("delete");
    setError(null);
    try {
      await deleteMcpServer(selectedId);
      setSelectedId(null);
      await load();
      setMessage("服务已删除");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setWorking(null);
    }
  };

  const bindCatalog = async () => {
    if (!selectedId) return;
    setWorking("catalog");
    setError(null);
    setMessage(null);
    try {
      const row = await bindMcpServerCatalog(selectedId, catalogVersionId);
      await load(row.id);
      setMessage(catalogVersionId ? "Catalog 版本已绑定，连接缓存已重置" : "Catalog 绑定已解除");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setWorking(null);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 rounded-lg border border-line bg-ink/80 px-2.5 py-1.5 text-xs text-ice transition-all hover:border-pulse/40 hover:bg-pulse/10 hover:text-pulse active:scale-95"
        title="管理 MCP 服务"
      >
        <ServerCog size={13} /> MCP
      </button>

      {open &&
        createPortal(
          <div
            ref={dialogRef}
            tabIndex={-1}
            className="fixed inset-0 z-50 flex items-center justify-center bg-void/80 p-3 backdrop-blur-xs sm:p-6"
            role="dialog"
            aria-modal="true"
            aria-labelledby="mcp-manager-title"
          >
            <div className="glass flex h-[min(760px,92vh)] w-full max-w-6xl flex-col overflow-hidden rounded-lg border border-line shadow-card animate-fade-up">
              <header className="flex min-h-14 items-center gap-3 border-b border-line px-4 sm:px-5">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-pulse/30 bg-pulse/10 text-pulse">
                  <Network size={15} />
                </span>
                <div className="min-w-0">
                  <h2 id="mcp-manager-title" className="text-sm font-semibold text-ice">
                    MCP 服务中心
                  </h2>
                  <p className="truncate font-mono text-[9px] uppercase tracking-[0.2em] text-ghost/60">
                    registry · discovery · connection lifecycle
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void load(selectedId ?? undefined)}
                  disabled={loading}
                  className="ml-auto flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-pulse disabled:opacity-40"
                  title="刷新服务"
                >
                  <RefreshCw size={14} className={cn(loading && "animate-spin")} />
                </button>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  disabled={working !== null}
                  className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-ice"
                  title="关闭"
                >
                  <X size={15} />
                </button>
              </header>

              <div className="flex min-h-0 flex-1 flex-col md:flex-row">
                <ServerList
                  servers={servers}
                  selectedId={selectedId}
                  loading={loading}
                  onChoose={choose}
                  onNew={startNew}
                />
                <McpServerEditor
                  selectedId={selectedId}
                  draft={draft}
                  setDraft={setDraft}
                  tools={tools}
                  envSources={selected?.env_sources ?? {}}
                  headerSources={selected?.headers_sources ?? {}}
                  catalog={catalog}
                  catalogVersionId={catalogVersionId}
                  onCatalogVersionChange={setCatalogVersionId}
                  error={error}
                  message={message}
                  working={working}
                  onSave={() => void save()}
                  onProbe={() => void probe()}
                  onCatalogBind={() => void bindCatalog()}
                  onRemove={() => void remove()}
                />
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}

function ServerList(props: {
  servers: McpServerDTO[];
  selectedId: string | null;
  loading: boolean;
  onChoose: (server: McpServerDTO) => void;
  onNew: () => void;
}) {
  return (
    <aside className="flex max-h-52 w-full shrink-0 flex-col border-b border-line md:max-h-none md:w-72 md:border-b-0 md:border-r">
      <div className="flex items-center justify-between px-3 py-3">
        <span className="font-mono text-[9px] uppercase tracking-[0.24em] text-ghost">
          Servers · {props.servers.length}
        </span>
        <button
          type="button"
          onClick={props.onNew}
          className="flex h-7 w-7 items-center justify-center rounded-md text-ghost transition hover:bg-pulse/10 hover:text-pulse"
          title="添加服务"
        >
          <CirclePlus size={14} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-2 pb-3">
        {props.servers.map((server) => (
          <button
            key={server.id}
            type="button"
            onClick={() => props.onChoose(server)}
            className={cn(
              "mb-1 flex w-full items-center gap-2.5 rounded-md border px-3 py-2.5 text-left transition",
              props.selectedId === server.id
                ? "border-pulse/40 bg-pulse/10"
                : "border-transparent hover:border-line hover:bg-line/40",
            )}
          >
            <span
              className={cn(
                "h-2 w-2 shrink-0 rounded-full",
                server.connected
                  ? "bg-ok shadow-glow-ok"
                  : server.last_status?.startsWith("error")
                    ? "bg-bad"
                    : "bg-ghost/50",
              )}
            />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs font-medium text-ice">{server.name}</span>
              <span className="block truncate font-mono text-[9px] uppercase text-ghost/60">
                {server.transport} · {server.tools.length} tools
              </span>
            </span>
            {!server.enabled && <span className="font-mono text-[8px] text-warn">OFF</span>}
          </button>
        ))}
        {!props.loading && props.servers.length === 0 && (
          <p className="px-3 py-6 text-center text-[11px] text-ghost/50">暂无 MCP 服务</p>
        )}
      </div>
    </aside>
  );
}
