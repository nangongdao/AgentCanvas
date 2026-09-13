import { ShieldCheck } from "lucide-react";
import { Navigate } from "react-router-dom";

import { useAuth } from "@/features/auth/AuthProvider";
import { AdminAnnouncementsSection } from "@/features/admin/AdminAnnouncementsSection";
import { AdminQueueSection } from "@/features/admin/AdminQueueSection";
import { AdminTenantsSection } from "@/features/admin/AdminTenantsSection";
import { AdminUsageSection } from "@/features/admin/AdminUsageSection";
import { AdminProvidersSection } from "@/features/admin/AdminProvidersSection";

export function PlatformAdminPage() {
  const { ready, can } = useAuth();
  const canAdmin = can("admin");

  if (ready && !canAdmin) return <Navigate to="/" replace />;

  return (
    <div className="ambient-stage flex h-full w-full flex-col overflow-hidden text-ice">
      <header
        role="presentation"
        className="glass relative z-20 flex min-h-14 flex-wrap items-center gap-2 border-b border-line px-3 py-2 sm:px-5"
      >
        <span className="flex h-8 w-8 items-center justify-center text-pulse">
          <ShieldCheck size={18} />
        </span>
        <div className="min-w-0">
          <h1 className="workspace-page-title">
            平台管理台
          </h1>
          <p className="font-mono text-[9px] uppercase text-ghost/50">
            organizations / users / usage / providers / queue / announcements
          </p>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 p-3 sm:p-5">
          {!ready ? (
            <p className="font-mono text-[10px] uppercase text-ghost">loading…</p>
          ) : (
            <>
              <AdminUsageSection />
              <AdminTenantsSection />
              <AdminProvidersSection />
              <AdminQueueSection />
              <AdminAnnouncementsSection />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
