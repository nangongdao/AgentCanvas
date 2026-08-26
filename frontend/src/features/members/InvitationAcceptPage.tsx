import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { MailCheck } from "lucide-react";

import { acceptInvitation } from "@/api/endpoints/members";

/**
 * Invitation landing page: `/invitations/accept?token=...`. Renders inside
 * the authenticated shell; unauthenticated visitors hit the auth dialog
 * first and return here after login to burn the invitation.
 */
export function InvitationAcceptPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") ?? "";
  const [state, setState] = useState<"pending" | "ok" | "error">("pending");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!token) {
      setState("error");
      setMessage("邀请链接缺少 token 参数。");
      return;
    }
    acceptInvitation(token)
      .then((invitation) => {
        setState("ok");
        setMessage(`已加入组织(角色:${invitation.role})。`);
      })
      .catch((err) => {
        setState("error");
        setMessage(err instanceof Error ? err.message : "接受邀请失败");
      });
  }, [token]);

  return (
    <div className="ambient-stage flex h-full w-full items-center justify-center bg-void p-6 text-ice">
      <div className="glass w-full max-w-md rounded-lg border border-line p-5">
        <div className="mb-3 flex items-center gap-2">
          <MailCheck size={18} className="text-pulse" />
          <h1 className="font-display text-sm font-semibold">组织邀请</h1>
        </div>
        {state === "pending" && (
          <p className="font-mono text-[10px] uppercase text-ghost">accepting…</p>
        )}
        {state === "ok" && (
          <>
            <p role="status" className="mb-3 text-xs text-ok">
              {message}
            </p>
            <button
              type="button"
              onClick={() => void navigate("/settings/members")}
              className="rounded border border-pulse/60 bg-pulse/10 px-3 py-1.5 text-xs text-pulse transition hover:bg-pulse/20"
            >
              查看成员与邀请
            </button>
          </>
        )}
        {state === "error" && (
          <p role="alert" className="text-xs text-bad">
            {message}
          </p>
        )}
      </div>
    </div>
  );
}
