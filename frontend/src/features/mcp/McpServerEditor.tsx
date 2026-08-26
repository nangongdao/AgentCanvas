import type { Dispatch, SetStateAction } from "react";
import {
  Cable,
  Check,
  Loader2,
  PlugZap,
  Save,
  Trash2,
  Wrench,
} from "lucide-react";

import type {
  McpCatalogEntryDTO,
  McpServerDTO,
  McpServerInput,
  McpToolDTO,
  McpTransport,
} from "@/api/endpoints/mcp";
import { cn } from "@/utils/cn";

export interface McpServerDraft {
  name: string;
  transport: McpTransport;
  command: string;
  argsText: string;
  envText: string;
  url: string;
  headersText: string;
  enabled: boolean;
}

export type McpWorking = "save" | "test" | "delete" | "catalog" | null;

export const EMPTY_MCP_DRAFT: McpServerDraft = {
  name: "",
  transport: "stdio",
  command: "",
  argsText: "[]",
  envText: "{}",
  url: "",
  headersText: "{}",
  enabled: true,
};

const TRANSPORTS: Array<{ value: McpTransport; label: string }> = [
  { value: "stdio", label: "STDIO" },
  { value: "sse", label: "SSE" },
  { value: "streamable_http", label: "HTTP" },
];

export function draftFromServer(server: McpServerDTO): McpServerDraft {
  return {
    name: server.name,
    transport: server.transport,
    command: server.command ?? "",
    argsText: JSON.stringify(server.args ?? [], null, 2),
    envText: JSON.stringify(server.env ?? {}, null, 2),
    url: server.url ?? "",
    headersText: JSON.stringify(server.headers ?? {}, null, 2),
    enabled: server.enabled,
  };
}

function parseStringArray(value: string, label: string): string[] {
  const parsed: unknown = JSON.parse(value);
  if (!Array.isArray(parsed) || !parsed.every((item) => typeof item === "string")) {
    throw new Error(`${label} 必须是字符串数组`);
  }
  return parsed;
}

function parseStringMap(value: string, label: string): Record<string, string> {
  const parsed: unknown = JSON.parse(value);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error(`${label} 必须是 JSON 对象`);
  }
  const entries = Object.entries(parsed as Record<string, unknown>);
  if (!entries.every(([, item]) => typeof item === "string")) {
    throw new Error(`${label} 的值必须全部是字符串`);
  }
  return Object.fromEntries(entries) as Record<string, string>;
}

export function draftToInput(draft: McpServerDraft): McpServerInput {
  return {
    name: draft.name.trim(),
    transport: draft.transport,
    command: draft.transport === "stdio" ? draft.command.trim() : null,
    args: draft.transport === "stdio" ? parseStringArray(draft.argsText, "Args") : [],
    env: draft.transport === "stdio" ? parseStringMap(draft.envText, "Env") : {},
    url: draft.transport === "stdio" ? null : draft.url.trim(),
    headers:
      draft.transport === "stdio" ? {} : parseStringMap(draft.headersText, "Headers"),
    enabled: draft.enabled,
  };
}

interface Props {
  selectedId: string | null;
  draft: McpServerDraft;
  setDraft: Dispatch<SetStateAction<McpServerDraft>>;
  tools: McpToolDTO[];
  envSources: Record<string, string>;
  headerSources: Record<string, string>;
  catalog: McpCatalogEntryDTO[];
  catalogVersionId: string | null;
  onCatalogVersionChange: (versionId: string | null) => void;
  error: string | null;
  message: string | null;
  working: McpWorking;
  onSave: () => void;
  onProbe: () => void;
  onCatalogBind: () => void;
  onRemove: () => void;
}

export function McpServerEditor({
  selectedId,
  draft,
  setDraft,
  tools,
  envSources,
  headerSources,
  catalog,
  catalogVersionId,
  onCatalogVersionChange,
  error,
  message,
  working,
  onSave,
  onProbe,
  onCatalogBind,
  onRemove,
}: Props) {
  const update = (patch: Partial<McpServerDraft>) =>
    setDraft((current) => ({ ...current, ...patch }));
  const approvedVersions = catalog.flatMap((entry) =>
    entry.versions
      .filter((version) => version.status === "approved")
      .map((version) => ({ entry, version })),
  );

  return (
    <main className="min-h-0 min-w-0 flex-1 overflow-auto p-4 sm:p-5">
      <div className="mx-auto grid max-w-4xl gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(260px,0.82fr)]">
        <section className="min-w-0">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-ice">
                {selectedId ? "连接配置" : "新建服务"}
              </h3>
              {selectedId && (
                <p className="mt-0.5 font-mono text-[9px] text-ghost/50">{selectedId}</p>
              )}
            </div>
            <label className="flex cursor-pointer items-center gap-2 text-[11px] text-ghost">
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(event) => update({ enabled: event.target.checked })}
                className="accent-cyan-400"
              />
              启用
            </label>
          </div>

          <div className="space-y-4">
            <Field label="Name">
              <input
                value={draft.name}
                onChange={(event) => update({ name: event.target.value })}
                className="field-input"
                placeholder="服务名称"
              />
            </Field>
            <Field label="Transport">
              <div className="grid grid-cols-3 overflow-hidden rounded-lg border border-line bg-void/70 p-0.5">
                {TRANSPORTS.map((transport) => (
                  <button
                    key={transport.value}
                    type="button"
                    onClick={() => update({ transport: transport.value })}
                    className={cn(
                      "rounded-md px-2 py-1.5 font-mono text-[9px] transition",
                      draft.transport === transport.value
                        ? "bg-pulse text-void"
                        : "text-ghost hover:text-ice",
                    )}
                  >
                    {transport.label}
                  </button>
                ))}
              </div>
            </Field>
            {draft.transport === "stdio" ? (
              <>
                <Field label="Command">
                  <input
                    value={draft.command}
                    onChange={(event) => update({ command: event.target.value })}
                    className="field-input font-mono text-[11px]"
                    placeholder="python / npx / executable path"
                  />
                </Field>
                <JsonField
                  label="Args JSON"
                  value={draft.argsText}
                  onChange={(argsText) => update({ argsText })}
                />
                <JsonField
                  label="Env JSON / Secret References"
                  value={draft.envText}
                  onChange={(envText) => update({ envText })}
                />
                <SecretSourceHint sources={envSources} />
              </>
            ) : (
              <>
                <Field label="URL">
                  <input
                    value={draft.url}
                    onChange={(event) => update({ url: event.target.value })}
                    className="field-input font-mono text-[11px]"
                    placeholder="https://example.com/mcp"
                  />
                </Field>
                <JsonField
                  label="Headers JSON / Secret References"
                  value={draft.headersText}
                  onChange={(headersText) => update({ headersText })}
                />
                <SecretSourceHint sources={headerSources} />
              </>
            )}
          </div>

          <section aria-label="MCP Catalog" className="mt-5 border-y border-line/70 py-3">
            <div className="mb-2 flex items-center gap-2">
              <Cable size={13} className="text-pulse" />
              <h4 className="font-mono text-[10px] uppercase tracking-[0.2em] text-ghost/70">
                Approved MCP Catalog
              </h4>
              <span className="ml-auto font-mono text-[9px] text-ghost/45">
                {approvedVersions.length} versions
              </span>
            </div>
            <div className="flex flex-wrap items-end gap-2">
              <label className="min-w-0 flex-1">
                <span className="mb-1 block font-mono text-[9px] uppercase tracking-widest text-ghost/60">
                  Binding
                </span>
                <select
                  value={catalogVersionId ?? ""}
                  onChange={(event) =>
                    onCatalogVersionChange(event.target.value || null)
                  }
                  className="field-input font-mono text-[10px]"
                  aria-label="Catalog version"
                  disabled={working !== null}
                >
                  <option value="">未绑定 approved 版本</option>
                  {approvedVersions.map(({ entry, version }) => (
                    <option key={version.id} value={version.id}>
                      {entry.name} · v{version.version} · {version.manifest.transport}
                    </option>
                  ))}
                </select>
              </label>
              <ActionButton
                onClick={onCatalogBind}
                disabled={!selectedId || working !== null}
                busy={working === "catalog"}
                icon={<Cable size={13} />}
                label={catalogVersionId ? "应用绑定" : "解除绑定"}
              />
            </div>
            <p className="mt-2 font-mono text-[9px] leading-relaxed text-ghost/50">
              绑定会校验 transport、命令、脚本目录或远端主机权限。
            </p>
          </section>

          {error && (
            <p className="mt-4 border-l-2 border-bad pl-3 text-[11px] leading-relaxed text-bad">
              {error}
            </p>
          )}
          {message && (
            <p className="mt-4 flex items-center gap-2 text-[11px] text-ok">
              <Check size={12} /> {message}
            </p>
          )}
          <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line/70 pt-4">
            <ActionButton
              onClick={onSave}
              disabled={working !== null}
              busy={working === "save"}
              icon={<Save size={13} />}
              label="保存"
              primary
            />
            <ActionButton
              onClick={onProbe}
              disabled={!selectedId || working !== null || !draft.enabled}
              busy={working === "test"}
              icon={<PlugZap size={13} />}
              label="测试连接"
            />
            {selectedId && (
              <button
                type="button"
                onClick={onRemove}
                disabled={working !== null}
                className="ml-auto flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-bad/10 hover:text-bad disabled:opacity-40"
                title="删除服务"
              >
                {working === "delete" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Trash2 size={13} />
                )}
              </button>
            )}
          </div>
        </section>

        <ToolList selectedId={selectedId} tools={tools} />
      </div>
    </main>
  );
}

function ToolList({ selectedId, tools }: { selectedId: string | null; tools: McpToolDTO[] }) {
  return (
    <section className="min-w-0 border-t border-line pt-5 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
      <div className="mb-3 flex items-center gap-2">
        <Wrench size={13} className="text-volt" />
        <h3 className="font-mono text-[9px] uppercase tracking-[0.24em] text-ghost">
          Discovered Tools · {tools.length}
        </h3>
      </div>
      <div className="space-y-1.5">
        {tools.map((tool) => (
          <div key={tool.name} className="border-b border-line/60 px-1 py-2.5 last:border-0">
            <div className="flex items-center gap-2">
              <Cable size={11} className="shrink-0 text-pulse" />
              <span className="break-all font-mono text-[11px] text-ice">{tool.name}</span>
            </div>
            <p className="mt-1 text-[10px] leading-relaxed text-ghost/65">
              {tool.description || "No description provided"}
            </p>
            <p className="mt-1.5 wrap-break-word font-mono text-[9px] text-ghost/40">
              {Object.keys(
                (tool.input_schema.properties as Record<string, unknown>) ?? {},
              ).join(" · ") || "no arguments"}
            </p>
          </div>
        ))}
        {tools.length === 0 && (
          <p className="py-10 text-center text-[11px] text-ghost/45">
            {selectedId
              ? "尚未缓存工具，运行连接测试开始发现"
              : "保存服务后可测试连接并发现工具"}
          </p>
        )}
      </div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="font-mono text-[9px] uppercase tracking-[0.22em] text-ghost">
        {label}
      </span>
      {children}
    </label>
  );
}

function JsonField(props: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <Field label={props.label}>
      <textarea
        value={props.value}
        onChange={(event) => props.onChange(event.target.value)}
        spellCheck={false}
        className="min-h-20 resize-y rounded-lg border border-line bg-void/70 px-3 py-2 font-mono text-[10px] leading-relaxed text-ice outline-hidden transition focus:border-pulse/60"
      />
    </Field>
  );
}

function SecretSourceHint({ sources }: { sources: Record<string, string> }) {
  const entries = Object.entries(sources);
  if (entries.length === 0) return null;
  return (
    <div className="-mt-2 flex flex-wrap gap-1.5" aria-label="Secret sources">
      {entries.map(([key, source]) => (
        <span
          key={key}
          className="rounded border border-ok/25 bg-ok/5 px-1.5 py-0.5 font-mono text-[9px] text-ok/80"
        >
          {key} · {source}
        </span>
      ))}
    </div>
  );
}

function ActionButton(props: {
  onClick: () => void;
  disabled: boolean;
  busy: boolean;
  icon: React.ReactNode;
  label: string;
  primary?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      disabled={props.disabled}
      className={cn(
        "flex items-center gap-1.5 rounded-lg px-3.5 py-2 text-xs transition disabled:opacity-40",
        props.primary
          ? "bg-pulse font-semibold text-void hover:brightness-110"
          : "border border-line bg-ink text-ice hover:border-ok/40 hover:text-ok",
      )}
    >
      {props.busy ? <Loader2 size={13} className="animate-spin" /> : props.icon}
      {props.label}
    </button>
  );
}
