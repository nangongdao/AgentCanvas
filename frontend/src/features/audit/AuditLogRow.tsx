import { Box, Building2, Fingerprint, FolderKanban } from "lucide-react";

import type { AuditLogDTO } from "@/api/endpoints/auditLogs";
import { cn } from "@/utils/cn";

const ACTION_LABEL: Record<string, string> = {
  "organization.created": "创建组织",
  "project.created": "创建项目",
  "project_quota.updated": "更新项目配额",
  "membership.added": "添加成员",
  "membership.role_changed": "变更成员角色",
  "membership.removed": "移除成员",
  "model.created": "创建模型",
  "model.updated": "更新模型",
  "model.deleted": "删除模型",
  "mcp_server.created": "创建 MCP",
  "mcp_server.updated": "更新 MCP",
  "mcp_server.deleted": "删除 MCP",
  "mcp_server.catalog_bound": "绑定 MCP Catalog",
  "mcp_server.catalog_unbound": "解除 MCP Catalog",
  "mcp_server.catalog_rollout": "升级 MCP Catalog",
  "mcp_catalog.created": "创建 MCP Catalog",
  "mcp_catalog.version_created": "登记 MCP 版本",
  "mcp_catalog.version_approved": "批准 MCP 版本",
  "mcp_catalog.version_revoked": "撤销 MCP 版本",
  "mcp_catalog.rollout": "执行 MCP rollout",
  "service_account.created": "创建服务账号",
  "service_account.updated": "更新服务账号",
  "service_account.deleted": "删除服务账号",
  "api_token.issued": "签发 API Token",
  "api_token.revoked": "吊销 API Token",
  "workflow.created": "创建工作流",
  "workflow.updated": "更新工作流",
  "workflow.merged": "合并工作流",
  "workflow.archived": "归档工作流",
  "workflow.imported": "导入工作流",
  "workflow.cloned": "克隆工作流",
  "workflow_version.published": "发布版本",
  "workflow_version.rolled_back": "回滚版本",
  "workflow_review.requested": "发起审阅",
  "workflow_review.decided": "完成审阅",
  "human_approval.submitted": "提交人工审批",
};

function actionTone(action: string): string {
  if (action.endsWith(".deleted") || action.endsWith(".revoked")) {
    return "border-bad/35 bg-bad/10 text-bad";
  }
  if (action.includes("approval") || action.includes("review")) {
    return "border-warn/35 bg-warn/10 text-warn";
  }
  if (action.endsWith(".created") || action.endsWith(".issued")) {
    return "border-ok/35 bg-ok/10 text-ok";
  }
  return "border-pulse/35 bg-pulse/10 text-pulse";
}

function detailValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (value === null) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function AuditLogRow({ entry }: { entry: AuditLogDTO }) {
  return (
    <article className="grid min-w-0 gap-3 border-b border-line/80 px-3 py-3 transition hover:bg-ink/55 sm:px-4 lg:grid-cols-[10.5rem_minmax(10rem,1fr)_minmax(12rem,1.35fr)_minmax(14rem,1.7fr)] lg:items-start">
      <div className="min-w-0">
        <time className="block font-mono text-[10px] text-ice/80">
          {new Date(entry.created_at).toLocaleString()}
        </time>
        <span className="mt-1 block truncate font-mono text-[9px] text-ghost/45" title={entry.id}>
          {entry.id}
        </span>
      </div>

      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-1.5">
          <Fingerprint size={12} className="shrink-0 text-volt" />
          <span className="truncate text-xs text-ice" title={entry.actor_subject}>
            {entry.actor_subject}
          </span>
        </div>
        <div className="mt-1 truncate font-mono text-[9px] text-ghost/55" title={entry.actor_key}>
          {entry.auth_method} / {entry.actor_key}
        </div>
      </div>

      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <span
            className={cn(
              "rounded-xs border px-1.5 py-0.5 font-mono text-[9px]",
              actionTone(entry.action),
            )}
            title={entry.action}
          >
            {ACTION_LABEL[entry.action] ?? entry.action}
          </span>
          <span className="font-mono text-[9px] text-ghost/50">{entry.resource_type}</span>
        </div>
        <div className="mt-1.5 flex min-w-0 items-center gap-1.5">
          <Box size={11} className="shrink-0 text-ghost/60" />
          <span className="truncate text-[11px] text-ice/85" title={entry.resource_name ?? entry.resource_id}>
            {entry.resource_name || entry.resource_id}
          </span>
          {entry.resource_name && (
            <span className="truncate font-mono text-[9px] text-ghost/40" title={entry.resource_id}>
              {entry.resource_id}
            </span>
          )}
        </div>
      </div>

      <div className="min-w-0 space-y-1.5">
        {(entry.organization_id || entry.project_id) && (
          <div className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 font-mono text-[9px] text-ghost/60">
            {entry.organization_id && (
              <span className="flex min-w-0 items-center gap-1" title={entry.organization_id}>
                <Building2 size={10} className="shrink-0 text-ok/70" />
                <span className="truncate">{entry.organization_id}</span>
              </span>
            )}
            {entry.project_id && (
              <span className="flex min-w-0 items-center gap-1" title={entry.project_id}>
                <FolderKanban size={10} className="shrink-0 text-pulse/70" />
                <span className="truncate">{entry.project_id}</span>
              </span>
            )}
          </div>
        )}
        {Object.keys(entry.details).length > 0 ? (
          <dl className="flex min-w-0 flex-wrap gap-1">
            {Object.entries(entry.details).map(([key, value]) => (
              <div
                key={key}
                className="flex max-w-full items-baseline gap-1 rounded-xs border border-line bg-void/45 px-1.5 py-0.5 font-mono text-[9px]"
              >
                <dt className="shrink-0 text-ghost/55">{key}</dt>
                <dd className="min-w-0 break-all text-ice/75">{detailValue(value)}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <span className="font-mono text-[9px] text-ghost/35">no details</span>
        )}
      </div>
    </article>
  );
}

export { ACTION_LABEL };
