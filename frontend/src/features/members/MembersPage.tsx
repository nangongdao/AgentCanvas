import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { UserPlus, Users } from "lucide-react";
import { Link } from "react-router-dom";

import {
  createInvitation,
  listInvitations,
  listMembers,
  listMyOrganizations,
  removeMember,
  revokeInvitation,
  updateMemberRole,
  type Invitation,
  type IssuedInvitation,
  type MemberRole,
  type Membership,
  type OrganizationRef,
} from "@/api/endpoints/members";
import { useT, useI18nStore, type Translate, type TranslationKey } from "@/features/i18n/i18n";

const ROLE_ORDER: MemberRole[] = ["viewer", "editor", "admin"];

function roleLabel(role: MemberRole, t: Translate): string {
  return t(`members.role.${role}` as TranslationKey);
}

export function MembersPage() {
  const t = useT();
  const locale = useI18nStore((state) => (state.locale === "zh" ? "zh-CN" : "en-US"));
  const [organizations, setOrganizations] = useState<OrganizationRef[]>([]);
  const [organizationId, setOrganizationId] = useState<string>("");
  const [members, setMembers] = useState<Membership[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [issued, setIssued] = useState<IssuedInvitation | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<MemberRole>("viewer");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listMyOrganizations()
      .then((orgs) => {
        setOrganizations(orgs);
        setOrganizationId((current) => current || orgs[0]?.id || "");
      })
      .catch((err) => setError(err instanceof Error ? err.message : t("members.loadOrgFailed")))
      .finally(() => setLoading(false));
  }, [t]);

  const reload = useCallback(async () => {
    if (!organizationId) return;
    setError(null);
    try {
      const [memberRows, invitationRows] = await Promise.all([
        listMembers(organizationId),
        listInvitations(organizationId),
      ]);
      setMembers(memberRows);
      setInvitations(invitationRows);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("members.loadFailed"));
    }
  }, [organizationId, t]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const pendingInvitations = useMemo(
    () => invitations.filter((item) => item.accepted_at === null),
    [invitations],
  );

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!email.trim() || !organizationId) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await createInvitation(organizationId, {
        email: email.trim(),
        role,
      });
      setIssued(result);
      setEmail("");
      setNotice(
        result.delivery === "email"
          ? t("members.noticeEmail")
          : t("members.noticeManual"),
      );
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("members.inviteFailed"));
    } finally {
      setBusy(false);
    }
  };

  const changeRole = async (membership: Membership, next: MemberRole) => {
    if (next === membership.role) return;
    setBusy(true);
    setError(null);
    try {
      await updateMemberRole(organizationId, membership.user_id, next);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("members.roleFailed"));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (membership: Membership) => {
    setBusy(true);
    setError(null);
    try {
      await removeMember(organizationId, membership.user_id);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("members.removeFailed"));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (invitation: Invitation) => {
    setBusy(true);
    setError(null);
    try {
      await revokeInvitation(organizationId, invitation.id);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("members.revokeFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ambient-stage flex h-full w-full flex-col overflow-hidden text-ice">
      <header
        role="presentation"
        className="glass relative z-20 flex min-h-14 flex-wrap items-center gap-2 border-b border-line px-3 py-2 sm:px-5"
      >
        <span className="flex h-8 w-8 items-center justify-center text-pulse">
          <Users size={18} />
        </span>
        <div className="min-w-0">
          <h1 className="workspace-page-title">
            {t("members.title")}
          </h1>
          <p className="font-mono text-[9px] uppercase text-ghost/50">
            {t("members.eyebrow")}
          </p>
        </div>
        <label className="ml-auto flex items-center gap-2 text-xs text-ghost" htmlFor="members-org">
          {t("members.org")}
          <select
            id="members-org"
            value={organizationId}
            onChange={(event) => setOrganizationId(event.target.value)}
            className="max-w-[180px] rounded border border-line bg-void px-2 py-1 text-xs text-ice"
          >
            {organizations.map((org) => (
              <option key={org.id} value={org.id}>
                {org.name}
              </option>
            ))}
          </select>
        </label>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 p-3 sm:p-5">
          {error && (
            <p role="alert" className="font-mono text-[10px] text-bad">
              {error}
            </p>
          )}
          {loading ? (
            <p className="font-mono text-[10px] uppercase text-ghost">{t("members.loading")}</p>
          ) : organizations.length === 0 ? (
            <p className="font-mono text-[10px] text-ghost">
              {t("members.noOrg")}
            </p>
          ) : (
            <>
              <section aria-label={t("members.membersAria")} className="glass rounded-lg border border-line p-4">
                <h2 className="mb-3 font-display text-sm font-semibold">{t("members.membersHeading")}</h2>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[560px] text-left text-xs">
                    <thead>
                      <tr className="font-mono text-[9px] uppercase text-ghost">
                        <th className="py-1 pr-3">{t("members.col.user")}</th>
                        <th className="py-1 pr-3">{t("members.col.role")}</th>
                        <th className="py-1 pr-3">{t("members.col.joined")}</th>
                        <th className="py-1">{t("members.col.actions")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {members.map((membership) => (
                        <tr key={membership.id} className="border-t border-line/60">
                          <td className="py-1.5 pr-3 font-mono text-[10px] text-ghost">
                            {membership.user_id}
                          </td>
                          <td className="py-1.5 pr-3">
                            <label className="sr-only" htmlFor={`role-${membership.id}`}>
                              {t("members.col.role")}
                            </label>
                            <select
                              id={`role-${membership.id}`}
                              value={membership.role}
                              disabled={busy}
                              onChange={(event) =>
                                void changeRole(
                                  membership,
                                  event.target.value as MemberRole,
                                )
                              }
                              className="rounded border border-line bg-void px-1.5 py-0.5 text-[11px] text-ice"
                            >
                              {ROLE_ORDER.map((option) => (
                                <option key={option} value={option}>
                                  {roleLabel(option, t)}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td className="py-1.5 pr-3 font-mono text-[10px] text-ghost">
                            {membership.created_at
                              ? new Date(membership.created_at).toLocaleDateString(locale)
                              : "—"}
                          </td>
                          <td className="py-1.5">
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => void remove(membership)}
                              className="rounded border border-bad/40 px-2 py-0.5 text-[10px] text-bad transition hover:bg-bad/10 disabled:opacity-50"
                            >
                              {t("members.remove")}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <section aria-label={t("members.invitationsAria")} className="glass rounded-lg border border-line p-4">
                <div className="mb-3 flex items-center gap-2">
                  <UserPlus size={15} className="text-ok" />
                  <h2 className="font-display text-sm font-semibold">{t("members.invitationsHeading")}</h2>
                </div>
                <form onSubmit={submit} className="mb-3 flex flex-wrap gap-2">
                  <label className="sr-only" htmlFor="invite-email">
                    {t("members.inviteeEmail")}
                  </label>
                  <input
                    id="invite-email"
                    type="email"
                    required
                    value={email}
                    maxLength={255}
                    onChange={(event) => setEmail(event.target.value)}
                    placeholder="teammate@example.com"
                    className="min-w-0 flex-1 rounded border border-line bg-void px-2 py-1.5 text-xs text-ice placeholder:text-ghost/60 focus:outline-none focus:ring-1 focus:ring-pulse"
                  />
                  <label className="sr-only" htmlFor="invite-role">
                    {t("members.inviteRole")}
                  </label>
                  <select
                    id="invite-role"
                    value={role}
                    onChange={(event) => setRole(event.target.value as MemberRole)}
                    className="rounded border border-line bg-void px-2 py-1.5 text-xs text-ice"
                  >
                    {ROLE_ORDER.map((option) => (
                      <option key={option} value={option}>
                        {roleLabel(option, t)}
                      </option>
                    ))}
                  </select>
                  <button
                    type="submit"
                    disabled={busy || !email.trim()}
                    className="rounded border border-pulse/60 bg-pulse/10 px-3 py-1.5 text-xs text-pulse transition hover:bg-pulse/20 disabled:opacity-50"
                  >
                    {t("members.generate")}
                  </button>
                </form>
                {notice && issued && (
                  <div className="mb-3 rounded border border-line bg-ink p-2">
                    <p role="status" className="mb-1 font-mono text-[10px] text-ok">
                      {notice}
                    </p>
                    <p className="break-all font-mono text-[10px] text-ice">
                      {issued.accept_url}
                    </p>
                  </div>
                )}
                {pendingInvitations.length > 0 && (
                  <ul className="space-y-1.5">
                    {pendingInvitations.map((invitation) => (
                      <li
                        key={invitation.id}
                        className="flex flex-wrap items-center gap-2 border-t border-line/60 py-1.5 text-xs"
                      >
                        <span className="min-w-0 flex-1 truncate" title={invitation.email}>
                          {invitation.email}
                        </span>
                        <span className="rounded bg-line/60 px-1.5 py-0.5 text-[10px] text-ghost">
                          {roleLabel(invitation.role, t)}
                        </span>
                        <span className="font-mono text-[10px] text-ghost">
                          {t("members.expiresAt", {
                            date: new Date(invitation.expires_at).toLocaleDateString(locale),
                          })}
                        </span>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void revoke(invitation)}
                          className="rounded border border-bad/40 px-2 py-0.5 text-[10px] text-bad transition hover:bg-bad/10 disabled:opacity-50"
                        >
                          {t("members.revoke")}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="mt-3 font-mono text-[9px] uppercase text-ghost">
                  {t("members.singleUse")}{" "}
                  <Link to="/invitations/accept" className="underline">
                    /invitations/accept
                  </Link>
                </p>
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
