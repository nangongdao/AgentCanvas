import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import {
  Cable,
  Check,
  CircleAlert,
  FileDiff,
  Layers3,
  Loader2,
  Plus,
  RefreshCw,
  Rocket,
  ServerCog,
  ShieldCheck,
  X,
} from "lucide-react";

import type {
  McpCatalogRolloutPreviewDTO,
  McpCatalogVersionDTO,
  McpCatalogVersionDiffDTO,
  McpTransport,
} from "@/api/endpoints/mcp";
import { McpManagerPanel } from "@/features/mcp/McpManagerPanel";
import { type CatalogForm, useMcpCatalog } from "@/features/mcp/useMcpCatalog";
import { cn } from "@/utils/cn";

function statusLabel(status: McpCatalogVersionDTO["status"]): string {
  return {
    draft: "DRAFT",
    approved: "APPROVED",
    superseded: "SUPERSEDED",
    revoked: "REVOKED",
  }[status];
}

function statusTone(status: McpCatalogVersionDTO["status"]): string {
  return {
    draft: "border-ghost/30 bg-ghost/10 text-ghost",
    approved: "border-ok/40 bg-ok/10 text-ok",
    superseded: "border-warn/40 bg-warn/10 text-warn",
    revoked: "border-bad/40 bg-bad/10 text-bad",
  }[status];
}

export function McpCatalogPage() {
  const {
    ready,
    authenticated,
    canAdmin,
    entries,
    selectedEntryId,
    selectedVersionId,
    baseVersionId,
    diff,
    preview,
    selectedRolloutIds,
    form,
    creatingEntry,
    creatingVersion,
    loading,
    working,
    error,
    toast,
    selectedEntry,
    selectedVersion,
    approvedVersion,
    refresh,
    dismissError,
    openCreateEntry,
    openCreateVersion,
    closeForm,
    selectEntry,
    selectVersion,
    selectBaseVersion,
    updateForm,
    createEntry,
    addVersion,
    approve,
    revoke,
    calculateDiff,
    calculatePreview,
    rollout,
    toggleRollout,
  } = useMcpCatalog();

  if (ready && authenticated && !canAdmin) return <Navigate to="/" replace />;

  return (
    <div className="ambient-stage flex h-full w-full min-w-0 flex-col overflow-x-hidden text-ice">
      <header role="presentation" className="glass relative z-20 flex min-h-14 min-w-0 flex-wrap items-center gap-2 border-b border-line px-3 py-2 sm:px-5">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-pulse/30 bg-pulse/10 text-pulse">
          <Cable size={16} />
        </span>
        <div className="min-w-0">
          <h1 className="workspace-page-title">MCP Catalog Control</h1>
          <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-ghost/50">
            approved manifests / diff / controlled rollout
          </p>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <McpManagerPanel />
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-pulse disabled:opacity-40"
            title="刷新目录"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : undefined} />
          </button>
        </div>
      </header>

      {error && (
        <div className="relative z-10 flex min-h-9 items-center gap-2 border-b border-bad/30 bg-bad/10 px-4 text-xs text-bad">
          <CircleAlert size={13} />
          <span className="min-w-0 flex-1 truncate">{error}</span>
          <button type="button" onClick={dismissError} className="h-7 rounded-md px-2 font-mono text-[9px] uppercase hover:bg-bad/10">
            dismiss
          </button>
        </div>
      )}
      {toast && <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-md border border-ok/40 bg-ok/15 px-4 py-2 text-xs text-ok shadow-card animate-fade-up">{toast}</div>}

      <main className="min-h-0 flex-1 overflow-auto p-3 sm:p-5">
        <div className="mx-auto grid min-w-0 max-w-[1160px] gap-4 xl:grid-cols-[240px_minmax(0,1fr)]">
          <aside className="glass min-w-0 overflow-hidden rounded-lg border border-line/80">
            <div className="flex items-center gap-2 border-b border-line px-3 py-3">
              <Layers3 size={14} className="text-volt" />
              <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-ghost">Catalog entries</span>
              <button
                type="button"
                onClick={openCreateEntry}
                className="ml-auto flex h-7 w-7 items-center justify-center rounded-md text-ghost transition hover:bg-pulse/10 hover:text-pulse"
                title="新建 Catalog entry"
              >
                <Plus size={14} />
              </button>
            </div>
            <div className="max-h-72 overflow-auto p-2 xl:max-h-[calc(100vh-190px)]">
              {loading && entries.length === 0 ? (
                <div className="flex items-center justify-center gap-2 py-8 text-xs text-ghost/60"><Loader2 size={14} className="animate-spin" />加载中…</div>
              ) : entries.length === 0 ? (
                <p className="px-2 py-8 text-center text-xs text-ghost/50">暂无目录条目</p>
              ) : (
                entries.map((entry) => {
                  const approved = entry.versions.find((version) => version.status === "approved");
                  return (
                    <button
                      key={entry.id}
                      type="button"
                      onClick={() => selectEntry(entry)}
                      className={cn(
                        "mb-1 w-full rounded-md border px-3 py-2.5 text-left transition",
                        selectedEntryId === entry.id ? "border-pulse/40 bg-pulse/10" : "border-transparent hover:border-line hover:bg-line/40",
                      )}
                    >
                      <span className="block truncate text-xs font-medium text-ice">{entry.name}</span>
                      <span className="mt-1 block truncate font-mono text-[9px] text-ghost/55">{entry.id}</span>
                      <span className="mt-2 flex items-center gap-1.5 font-mono text-[9px] text-ghost/60">
                        <span className={cn("h-1.5 w-1.5 rounded-full", approved ? "bg-ok" : "bg-warn")} />
                        {approved ? `approved v${approved.version}` : "no approved version"}
                      </span>
                    </button>
                  );
                })
              )}
            </div>
          </aside>

          <section className="min-w-0 space-y-4">
            {selectedEntry ? (
              <>
                <div className="glass rounded-lg border border-line/80 p-4 sm:p-5">
                  <div className="flex flex-wrap items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <ShieldCheck size={16} className="text-ok" />
                        <h2 className="truncate font-display text-base font-semibold text-ice">{selectedEntry.name}</h2>
                      </div>
                      <p className="mt-1 break-all font-mono text-[9px] text-ghost/55">{selectedEntry.id} · {selectedEntry.source_url || "no public source URL"}</p>
                      <p className="mt-3 max-w-3xl text-xs leading-relaxed text-ghost/75">{selectedEntry.description || "暂无目录描述。"}</p>
                    </div>
                    <button
                      type="button"
                      onClick={openCreateVersion}
                      className="flex h-8 items-center gap-1.5 rounded-md border border-line bg-ink/80 px-3 text-xs text-ice transition hover:border-pulse/40 hover:text-pulse"
                    >
                      <Plus size={13} /> 新版本
                    </button>
                  </div>
                  <div className="mt-4 grid gap-2 sm:grid-cols-3">
                    <Metric label="VERSIONS" value={String(selectedEntry.versions.length)} />
                    <Metric label="APPROVED" value={approvedVersion ? `v${approvedVersion.version}` : "—"} tone={approvedVersion ? "ok" : "warn"} />
                    <Metric label="SOURCE" value={selectedEntry.source_url ? "linked" : "local ref"} />
                  </div>
                </div>

                <div className="glass min-w-0 rounded-lg border border-line/80">
                  <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
                    <ServerCog size={14} className="text-pulse" />
                    <h3 className="font-mono text-[10px] uppercase tracking-[0.2em] text-ghost">Version ledger</h3>
                    <span className="ml-auto font-mono text-[9px] text-ghost/45">immutable manifests</span>
                  </div>
                  <div className="divide-y divide-line/70">
                    {selectedEntry.versions.map((version) => (
                      <button
                        key={version.id}
                        type="button"
                        onClick={() => selectVersion(version.id)}
                        className={cn("grid w-full gap-2 px-4 py-3 text-left transition sm:grid-cols-[minmax(8rem,1fr)_8rem_7rem_auto] sm:items-center", selectedVersionId === version.id ? "bg-pulse/5" : "hover:bg-line/30")}
                      >
                        <span className="min-w-0">
                          <span className="block truncate font-mono text-xs text-ice">v{version.version}</span>
                          <span className="mt-1 block truncate text-[10px] text-ghost/55">{version.source_ref}</span>
                        </span>
                        <span className="font-mono text-[10px] uppercase text-ghost/60">{version.manifest.transport}</span>
                        <span className={cn("w-fit rounded border px-1.5 py-0.5 font-mono text-[9px]", statusTone(version.status))}>{statusLabel(version.status)}</span>
                        <span className="text-right font-mono text-[9px] text-ghost/45">{version.approved_by || "unreviewed"}</span>
                      </button>
                    ))}
                  </div>
                </div>

                {selectedVersion && (
                  <div className="grid min-w-0 gap-4 2xl:grid-cols-2">
                    <section className="glass min-w-0 rounded-lg border border-line/80 p-4">
                      <div className="mb-3 flex items-center gap-2">
                        <FileDiff size={14} className="text-volt" />
                        <h3 className="font-mono text-[10px] uppercase tracking-[0.2em] text-ghost">Review diff</h3>
                      </div>
                      <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
                        <SelectField label="Target version">
                          <select aria-label="Diff target version" value={selectedVersion.id} onChange={(event) => selectVersion(event.target.value)} className="field-input h-8 py-0 font-mono text-[10px]">
                            {selectedEntry.versions.map((version) => <option key={version.id} value={version.id}>v{version.version} · {statusLabel(version.status)}</option>)}
                          </select>
                        </SelectField>
                        <SelectField label="Compare against">
                          <select aria-label="Diff base version" value={baseVersionId ?? ""} onChange={(event) => selectBaseVersion(event.target.value || null)} className="field-input h-8 py-0 font-mono text-[10px]">
                            <option value="">previous version</option>
                            {selectedEntry.versions.filter((version) => version.id !== selectedVersion.id).map((version) => <option key={version.id} value={version.id}>v{version.version}</option>)}
                          </select>
                        </SelectField>
                        <button type="button" onClick={() => void calculateDiff()} disabled={working !== null} className="flex h-8 items-center justify-center gap-1.5 rounded-md border border-line bg-ink/80 px-3 text-xs text-ice transition hover:border-volt/40 hover:text-volt disabled:opacity-40">
                          {working === "diff" ? <Loader2 size={13} className="animate-spin" /> : <FileDiff size={13} />} 计算
                        </button>
                      </div>
                      {diff ? <DiffSummary diff={diff} /> : <p className="mt-4 text-[11px] leading-relaxed text-ghost/55">选择版本并计算差异，审批前重点检查 transport 与权限增量。</p>}
                      <div className="mt-4 flex flex-wrap gap-2 border-t border-line/70 pt-3">
                        {selectedVersion.status === "draft" && <ActionButton onClick={() => void approve()} busy={working === "approve"} icon={<Check size={13} />} label="批准版本" tone="ok" />}
                        {selectedVersion.status === "approved" && <ActionButton onClick={() => void revoke()} busy={working === "revoke"} icon={<X size={13} />} label="撤销版本" tone="bad" />}
                      </div>
                    </section>

                    <section className="glass min-w-0 rounded-lg border border-line/80 p-4">
                      <div className="mb-3 flex items-center gap-2">
                        <Rocket size={14} className="text-pulse" />
                        <h3 className="font-mono text-[10px] uppercase tracking-[0.2em] text-ghost">Controlled rollout</h3>
                      </div>
                      {selectedVersion.status !== "approved" ? (
                        <p className="text-[11px] leading-relaxed text-ghost/55">只有 approved 版本可以进入升级预检。审批后，系统只会升级绑定该 entry 且通过权限校验的服务。</p>
                      ) : (
                        <>
                          <button type="button" onClick={() => void calculatePreview()} disabled={working !== null} className="flex h-8 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void transition hover:brightness-110 disabled:opacity-40">
                            {working === "preview" ? <Loader2 size={13} className="animate-spin" /> : <ShieldCheck size={13} />} 运行升级预检
                          </button>
                          {preview && <RolloutPreview preview={preview} selectedIds={selectedRolloutIds} onToggle={toggleRollout} />}
                          {preview && preview.compatible_count > 0 && <button type="button" onClick={() => void rollout()} disabled={working !== null || selectedRolloutIds.length === 0} className="mt-3 flex h-8 items-center gap-1.5 rounded-md border border-ok/40 bg-ok/10 px-3 text-xs font-semibold text-ok transition hover:bg-ok/20 disabled:opacity-40">
                            {working === "rollout" ? <Loader2 size={13} className="animate-spin" /> : <Rocket size={13} />} 升级已选 {selectedRolloutIds.length} 项
                          </button>}
                        </>
                      )}
                    </section>
                  </div>
                )}
              </>
            ) : (
              <div className="glass flex min-h-80 flex-col items-center justify-center rounded-lg border border-dashed border-line text-center">
                <Cable size={28} className="text-pulse/60" />
                <h2 className="mt-3 text-sm font-semibold text-ice">选择一个 Catalog entry</h2>
                <p className="mt-1 max-w-sm text-xs leading-relaxed text-ghost/55">从左侧打开已登记的 MCP 来源，查看版本差异并执行受控升级。</p>
              </div>
            )}
          </section>
        </div>
      </main>

      {(creatingEntry || creatingVersion) && (
        <CatalogFormDialog
          mode={creatingEntry ? "entry" : "version"}
          form={form}
          working={working}
          onChange={updateForm}
          onClose={closeForm}
          onSubmit={() => void (creatingEntry ? createEntry() : addVersion())}
        />
      )}
    </div>
  );
}

function Metric({ label, value, tone = "default" }: { label: string; value: string; tone?: "default" | "ok" | "warn" }) {
  return <div className="rounded-md border border-line/70 bg-void/35 px-3 py-2"><span className="block font-mono text-[9px] tracking-[0.16em] text-ghost/50">{label}</span><span className={cn("mt-1 block font-mono text-xs", tone === "ok" ? "text-ok" : tone === "warn" ? "text-warn" : "text-ice")}>{value}</span></div>;
}

function SelectField({ label, children }: { label: string; children: ReactNode }) {
  return <label className="min-w-0"><span className="mb-1 block font-mono text-[9px] uppercase tracking-[0.16em] text-ghost/55">{label}</span>{children}</label>;
}

function ActionButton({ onClick, busy, icon, label, tone }: { onClick: () => void; busy: boolean; icon: ReactNode; label: string; tone: "ok" | "bad" }) {
  return <button type="button" onClick={onClick} disabled={busy} className={cn("flex h-8 items-center gap-1.5 rounded-md border px-3 text-xs transition disabled:opacity-40", tone === "ok" ? "border-ok/40 bg-ok/10 text-ok hover:bg-ok/20" : "border-bad/40 bg-bad/10 text-bad hover:bg-bad/20")}>{busy ? <Loader2 size={13} className="animate-spin" /> : icon}{label}</button>;
}

function DiffSummary({ diff }: { diff: McpCatalogVersionDiffDTO }) {
  const added = Object.values(diff.permission_added).flat();
  const removed = Object.values(diff.permission_removed).flat();
  return <div className="mt-4 space-y-3"><div className="flex flex-wrap gap-1.5">{diff.changed_fields.length === 0 ? <span className="font-mono text-[10px] text-ok">NO CHANGES</span> : diff.changed_fields.map((field) => <span key={field} className="rounded border border-volt/30 bg-volt/10 px-1.5 py-0.5 font-mono text-[9px] text-volt">{field}</span>)}</div><div className="grid gap-2 sm:grid-cols-2"><DeltaList label="Permission added" values={added} tone="ok" /><DeltaList label="Permission removed" values={removed} tone="bad" /></div></div>;
}

function DeltaList({ label, values, tone }: { label: string; values: string[]; tone: "ok" | "bad" }) {
  return <div className="rounded-md border border-line/70 bg-void/35 p-2.5"><span className={cn("font-mono text-[9px] uppercase tracking-[0.14em]", tone === "ok" ? "text-ok" : "text-bad")}>{label} · {values.length}</span><div className="mt-2 space-y-1">{values.length === 0 ? <span className="font-mono text-[9px] text-ghost/40">none</span> : values.map((value) => <div key={value} className="break-all font-mono text-[9px] text-ghost/75">{value}</div>)}</div></div>;
}

function RolloutPreview({ preview, selectedIds, onToggle }: { preview: McpCatalogRolloutPreviewDTO; selectedIds: string[]; onToggle: (serverId: string) => void }) {
  return <div className="mt-4 space-y-2"><div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[9px] text-ghost/60"><span className="text-ok">{preview.compatible_count} compatible</span><span className="text-bad">{preview.incompatible_count} blocked</span><span className="ml-auto">v{preview.version}</span></div><div className="max-h-44 space-y-1 overflow-auto">{preview.servers.length === 0 ? <p className="rounded border border-line/60 px-2 py-3 text-center text-[10px] text-ghost/45">没有绑定该 entry 的 MCP 服务</p> : preview.servers.map((server) => <label key={server.server_id} className={cn("flex items-start gap-2 rounded border px-2.5 py-2", server.compatible ? "border-line/70 bg-void/30" : "border-bad/20 bg-bad/5 opacity-75")}><input type="checkbox" checked={selectedIds.includes(server.server_id)} disabled={!server.compatible} onChange={() => onToggle(server.server_id)} className="mt-0.5 accent-cyan-400" /><span className="min-w-0 flex-1"><span className="block truncate text-[11px] text-ice">{server.name}</span><span className={cn("mt-0.5 block truncate font-mono text-[9px]", server.compatible ? "text-ghost/50" : "text-bad/80")}>{server.compatible ? `${server.current_version ? `current v${server.current_version}` : "unversioned"} · ready` : server.reason}</span></span><span className={cn("font-mono text-[9px] uppercase", server.compatible ? "text-ok" : "text-bad")}>{server.compatible ? "READY" : "BLOCKED"}</span></label>)}</div></div>;
}

function CatalogFormDialog({ mode, form, working, onChange, onClose, onSubmit }: { mode: "entry" | "version"; form: CatalogForm; working: string | null; onChange: (patch: Partial<CatalogForm>) => void; onClose: () => void; onSubmit: () => void }) {
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-void/80 p-3 backdrop-blur-xs sm:p-6"><div role="dialog" aria-modal="true" aria-labelledby="catalog-form-title" className="glass max-h-[92vh] w-full max-w-2xl overflow-auto rounded-lg border border-line p-4 shadow-card sm:p-6"><header className="flex items-start gap-3"><div className="flex h-8 w-8 items-center justify-center rounded-lg border border-pulse/30 bg-pulse/10 text-pulse"><Layers3 size={15} /></div><div className="min-w-0 flex-1"><h2 id="catalog-form-title" className="text-sm font-semibold text-ice">{mode === "entry" ? "新建 Catalog entry" : "登记新版本"}</h2><p className="mt-1 text-[11px] text-ghost/55">声明 transport 和最小权限；版本创建后必须经过审批才能绑定。</p></div><button type="button" onClick={onClose} disabled={working !== null} className="flex h-8 w-8 items-center justify-center rounded-md text-ghost hover:bg-line hover:text-ice"><X size={15} /></button></header><div className="mt-5 grid gap-3 sm:grid-cols-2"><FormField label="Catalog ID" hidden={mode !== "entry"}><input value={form.id} disabled={mode !== "entry"} onChange={(event) => onChange({ id: event.target.value })} placeholder="vendor.server" className="field-input font-mono text-[11px]" /></FormField><FormField label="名称" hidden={mode !== "entry"}><input value={form.name} disabled={mode !== "entry"} onChange={(event) => onChange({ name: event.target.value })} placeholder="Server display name" className="field-input" /></FormField><FormField label="版本"><input value={form.version} onChange={(event) => onChange({ version: event.target.value })} placeholder="1.0.0" className="field-input font-mono text-[11px]" /></FormField><FormField label="来源引用"><input value={form.sourceRef} onChange={(event) => onChange({ sourceRef: event.target.value })} placeholder="https://…/manifest.json" className="field-input font-mono text-[11px]" /></FormField><FormField label="来源 URL" hidden={mode !== "entry"}><input value={form.sourceUrl} disabled={mode !== "entry"} onChange={(event) => onChange({ sourceUrl: event.target.value })} placeholder="https://github.com/…" className="field-input font-mono text-[11px]" /></FormField><FormField label="Transport"><select value={form.transport} onChange={(event) => onChange({ transport: event.target.value as McpTransport })} className="field-input font-mono text-[11px]"><option value="stdio">stdio</option><option value="sse">sse</option><option value="streamable_http">streamable_http</option></select></FormField><FormField label="描述" hidden={mode !== "entry"}><textarea value={form.description} disabled={mode !== "entry"} onChange={(event) => onChange({ description: event.target.value })} className="field-input min-h-16 resize-y text-xs sm:col-span-2" /></FormField><PermissionField label="Network hosts" value={form.network} onChange={(network) => onChange({ network })} /><PermissionField label="Filesystem roots" value={form.filesystem} onChange={(filesystem) => onChange({ filesystem })} /><PermissionField label="Commands" value={form.commands} onChange={(commands) => onChange({ commands })} /></div><footer className="mt-5 flex justify-end gap-2 border-t border-line/70 pt-4"><button type="button" onClick={onClose} disabled={working !== null} className="h-8 rounded-md px-3 text-xs text-ghost hover:bg-line hover:text-ice">取消</button><button type="button" onClick={onSubmit} disabled={working !== null} className="flex h-8 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void hover:brightness-110 disabled:opacity-40">{working ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}{mode === "entry" ? "创建 entry" : "登记版本"}</button></footer></div></div>;
}

function FormField({ label, children, hidden = false }: { label: string; children: ReactNode; hidden?: boolean }) {
  if (hidden) return null;
  return <label className="flex min-w-0 flex-col gap-1.5"><span className="font-mono text-[9px] uppercase tracking-[0.16em] text-ghost/60">{label}</span>{children}</label>;
}

function PermissionField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <label className="flex min-w-0 flex-col gap-1.5 sm:col-span-2"><span className="font-mono text-[9px] uppercase tracking-[0.16em] text-ghost/60">{label} <span className="normal-case tracking-normal text-ghost/40">(one per line)</span></span><textarea value={value} onChange={(event) => onChange(event.target.value)} spellCheck={false} className="field-input min-h-16 resize-y font-mono text-[10px]" /></label>;
}
