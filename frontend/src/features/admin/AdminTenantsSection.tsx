import { useCallback, useEffect, useState } from "react";
import { Building2, Users } from "lucide-react";

import { useT } from "@/features/i18n/i18n";
import {
  cancelOrgDeletion,
  listAdminOrganizations,
  listAdminUsers,
  purgeOrgNow,
  requestOrgDeletion,
  setOrganizationStatus,
  setUserStatus,
  type AdminOrganization,
  type AdminUser,
  type OrgDeletionStatus,
} from "@/api/endpoints/adminConsole";

export function AdminTenantsSection() {
  const t = useT();
  const [organizations, setOrganizations] = useState<AdminOrganization[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [orgRows, userRows] = await Promise.all([
        listAdminOrganizations(),
        listAdminUsers(),
      ]);
      setOrganizations(orgRows);
      setUsers(userRows);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载组织/用户失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleOrganization = async (org: AdminOrganization) => {
    setBusy(`org:${org.id}`);
    setError(null);
    try {
      const next = org.status === "active" ? "disabled" : "active";
      await setOrganizationStatus(org.id, next);
      setOrganizations((rows) =>
        rows.map((row) => (row.id === org.id ? { ...row, status: next } : row)),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新组织状态失败");
    } finally {
      setBusy(null);
    }
  };

  const toggleUser = async (user: AdminUser) => {
    setBusy(`user:${user.id}`);
    setError(null);
    try {
      const next = user.status === "active" ? "disabled" : "active";
      await setUserStatus(user.id, next);
      setUsers((rows) =>
        rows.map((row) => (row.id === user.id ? { ...row, status: next } : row)),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新用户状态失败");
    } finally {
      setBusy(null);
    }
  };

  const deletionLabel = (status: OrgDeletionStatus): string =>
    t(`platform.deletion.${status}`);

  const requestDeletion = async (org: AdminOrganization) => {
    setBusy(`del:${org.id}`);
    setError(null);
    try {
      const next = await requestOrgDeletion(org.id);
      setOrganizations((rows) =>
        rows.map((row) =>
          row.id === org.id ? { ...row, deletion: next } : row,
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "请求删除失败");
    } finally {
      setBusy(null);
    }
  };

  const cancelDeletion = async (org: AdminOrganization) => {
    setBusy(`del:${org.id}`);
    setError(null);
    try {
      const next = await cancelOrgDeletion(org.id);
      setOrganizations((rows) =>
        rows.map((row) =>
          row.id === org.id ? { ...row, deletion: next } : row,
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "取消删除失败");
    } finally {
      setBusy(null);
    }
  };

  const purgeNow = async (org: AdminOrganization) => {
    if (!window.confirm(t("platform.deletion.purgeConfirm"))) return;
    setBusy(`purge:${org.id}`);
    setError(null);
    try {
      await purgeOrgNow(org.id);
      setOrganizations((rows) => rows.filter((row) => row.id !== org.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "清除组织失败");
    } finally {
      setBusy(null);
    }
  };

  return (
    <section
      aria-label="组织与用户"
      className="glass rounded-lg border border-line p-4"
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Building2 size={15} className="text-pulse" />
        <h2 className="font-display text-sm font-semibold">组织</h2>
        <span className="ml-auto font-mono text-[9px] uppercase text-ghost">
          {organizations.length} orgs · {users.length} users
        </span>
      </div>
      {error && (
        <p role="alert" className="mb-2 font-mono text-[10px] text-bad">
          {error}
        </p>
      )}
      {loading ? (
        <p className="font-mono text-[10px] uppercase text-ghost">loading…</p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-xs">
              <thead>
                <tr className="font-mono text-[9px] uppercase text-ghost">
                  <th className="py-1 pr-3">名称</th>
                  <th className="py-1 pr-3">状态</th>
                  <th className="py-1 pr-3">删除</th>
                  <th className="py-1 pr-3">套餐</th>
                  <th className="py-1 pr-3">项目</th>
                  <th className="py-1 pr-3">成员</th>
                  <th className="py-1">操作</th>
                </tr>
              </thead>
              <tbody>
                {organizations.map((org) => (
                  <tr key={org.id} className="border-t border-line/60">
                    <td className="py-1.5 pr-3">{org.name}</td>
                    <td className="py-1.5 pr-3">
                      <span
                        className={
                          org.status === "active"
                            ? "rounded bg-ok/10 px-1.5 py-0.5 text-[10px] text-ok"
                            : "rounded bg-bad/10 px-1.5 py-0.5 text-[10px] text-bad"
                        }
                      >
                        {org.status === "active" ? "活跃" : "已停用"}
                      </span>
                    </td>
                    <td className="py-1.5 pr-3">
                      {org.deletion.status === "none" ? (
                        <span className="font-mono text-[10px] text-ghost">—</span>
                      ) : (
                        <span className="font-mono text-[10px] text-warn">
                          {deletionLabel(org.deletion.status)}
                          {org.deletion.purge_due_at
                            ? ` · ${new Date(org.deletion.purge_due_at).toLocaleString()}`
                            : ""}
                        </span>
                      )}
                    </td>
                    <td className="py-1.5 pr-3 text-ghost">
                      {org.plan_slug ?? "—"}
                    </td>
                    <td className="py-1.5 pr-3">{org.project_count}</td>
                    <td className="py-1.5 pr-3">{org.member_count}</td>
                    <td className="py-1.5">
                      <div className="flex flex-wrap gap-1">
                        <button
                          type="button"
                          disabled={busy === `org:${org.id}`}
                          onClick={() => void toggleOrganization(org)}
                          className="rounded border border-line px-2 py-0.5 text-[10px] text-ghost transition hover:bg-line hover:text-ice disabled:opacity-50"
                        >
                          {org.status === "active" ? "停用" : "启用"}
                        </button>
                        {org.deletion.status === "none" && (
                          <button
                            type="button"
                            disabled={busy === `del:${org.id}`}
                            onClick={() => void requestDeletion(org)}
                            className="rounded border border-warn/40 px-2 py-0.5 text-[10px] text-warn transition hover:bg-warn/10 disabled:opacity-50"
                          >
                            {t("platform.deletion.requestAction")}
                          </button>
                        )}
                        {(org.deletion.status === "requested" ||
                          org.deletion.status === "purging") && (
                          <>
                            <button
                              type="button"
                              disabled={busy === `del:${org.id}`}
                              onClick={() => void cancelDeletion(org)}
                              className="rounded border border-line px-2 py-0.5 text-[10px] text-ghost transition hover:bg-line hover:text-ice disabled:opacity-50"
                            >
                              {t("platform.deletion.cancelAction")}
                            </button>
                            <button
                              type="button"
                              disabled={busy === `purge:${org.id}`}
                              onClick={() => void purgeNow(org)}
                              className="rounded border border-bad/40 px-2 py-0.5 text-[10px] text-bad transition hover:bg-bad/10 disabled:opacity-50"
                            >
                              {t("platform.deletion.purgeAction")}
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mb-2 mt-5 flex items-center gap-2">
            <Users size={15} className="text-pulse" />
            <h3 className="font-display text-sm font-semibold">用户</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-xs">
              <thead>
                <tr className="font-mono text-[9px] uppercase text-ghost">
                  <th className="py-1 pr-3">邮箱</th>
                  <th className="py-1 pr-3">角色</th>
                  <th className="py-1 pr-3">状态</th>
                  <th className="py-1 pr-3">最近登录</th>
                  <th className="py-1">操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.id} className="border-t border-line/60">
                    <td className="py-1.5 pr-3">{user.email}</td>
                    <td className="py-1.5 pr-3 text-ghost">{user.role}</td>
                    <td className="py-1.5 pr-3">
                      <span
                        className={
                          user.status === "active"
                            ? "rounded bg-ok/10 px-1.5 py-0.5 text-[10px] text-ok"
                            : "rounded bg-bad/10 px-1.5 py-0.5 text-[10px] text-bad"
                        }
                      >
                        {user.status === "active" ? "活跃" : "已停用"}
                      </span>
                    </td>
                    <td className="py-1.5 pr-3 font-mono text-[10px] text-ghost">
                      {user.last_login_at
                        ? new Date(user.last_login_at).toLocaleString()
                        : "—"}
                    </td>
                    <td className="py-1.5">
                      <button
                        type="button"
                        disabled={busy === `user:${user.id}`}
                        onClick={() => void toggleUser(user)}
                        className="rounded border border-line px-2 py-0.5 text-[10px] text-ghost transition hover:bg-line hover:text-ice disabled:opacity-50"
                      >
                        {user.status === "active" ? "停用" : "启用"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
