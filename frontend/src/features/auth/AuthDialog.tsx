import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Building2, KeyRound, Loader2, LogIn, UserRound } from "lucide-react";

import type { LoginCredentials } from "@/api/endpoints/auth";
import { useDialogFocus } from "@/components/useDialogFocus";
import { useT } from "@/features/i18n/i18n";

interface Props {
  open: boolean;
  working: boolean;
  error: string | null;
  oidcEnabled: boolean;
  onLogin: (credentials: LoginCredentials) => Promise<void>;
  onOIDCLogin: () => void;
}

export function AuthDialog({
  open,
  working,
  error,
  oidcEnabled,
  onLogin,
  onOIDCLogin,
}: Props) {
  const t = useT();
  const [method, setMethod] = useState<"password" | "token">("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useDialogFocus<HTMLElement>({ open, initialFocusRef: inputRef });

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-100 flex items-center justify-center bg-void/90 p-4 backdrop-blur-md">
      <section
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="auth-dialog-title"
        className="glass w-full max-w-sm rounded-lg border border-line shadow-card"
      >
        <header className="flex items-start gap-3 border-b border-line px-5 py-4">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-pulse/30 bg-pulse/10 text-pulse">
            <KeyRound size={17} />
          </span>
          <div>
            <h2 id="auth-dialog-title" className="text-sm font-semibold text-ice">
              {t("auth.dialogTitle")}
            </h2>
            <p className="mt-1 text-xs leading-5 text-ghost">{t("auth.dialogSubtitle")}</p>
          </div>
        </header>
        <form
          className="px-5 py-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (method === "token" && token) {
              void onLogin({ method: "token", token });
            }
            if (method === "password" && email && password) {
              void onLogin({ method: "password", email, password });
            }
          }}
        >
          <div className="grid h-9 grid-cols-2 rounded-md border border-line bg-void p-0.5">
            <button
              type="button"
              onClick={() => setMethod("password")}
              aria-pressed={method === "password"}
              className={`flex items-center justify-center gap-1.5 rounded-sm text-xs transition ${
                method === "password" ? "bg-ink text-ice" : "text-ghost hover:text-ice"
              }`}
            >
              <UserRound size={13} /> {t("auth.accountTab")}
            </button>
            <button
              type="button"
              onClick={() => setMethod("token")}
              aria-pressed={method === "token"}
              className={`flex items-center justify-center gap-1.5 rounded-sm text-xs transition ${
                method === "token" ? "bg-ink text-ice" : "text-ghost hover:text-ice"
              }`}
            >
              <KeyRound size={13} /> API Token
            </button>
          </div>

          <div className="mt-4 min-h-34">
            {method === "password" ? (
              <div className="space-y-3">
                <label className="flex flex-col gap-2">
                  <span className="font-mono text-[9px] uppercase text-ghost">{t("auth.emailLabel")}</span>
                  <input
                    ref={inputRef}
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    className="field-input h-10"
                    autoComplete="username"
                  />
                </label>
                <label className="flex flex-col gap-2">
                  <span className="font-mono text-[9px] uppercase text-ghost">{t("auth.passwordLabel")}</span>
                  <input
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    className="field-input h-10"
                    autoComplete="current-password"
                  />
                </label>
              </div>
            ) : (
              <label className="flex flex-col gap-2">
                <span className="font-mono text-[9px] uppercase text-ghost">{t("auth.tokenLabel")}</span>
                <input
                  ref={inputRef}
                  type="password"
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                  className="field-input h-10 font-mono"
                  autoComplete="off"
                />
              </label>
            )}
          </div>
          {error && (
            <p className="mt-3 border-l-2 border-bad px-3 text-xs text-bad">{error}</p>
          )}
          <button
            type="submit"
            disabled={
              working ||
              (method === "token" ? !token : !email || !password)
            }
            className="mt-4 flex h-10 w-full items-center justify-center gap-2 rounded-md bg-pulse text-xs font-semibold text-void transition hover:brightness-110 disabled:opacity-50"
          >
            {working ? <Loader2 size={14} className="animate-spin" /> : <LogIn size={14} />}
            {working ? t("auth.verifying") : t("auth.signIn")}
          </button>
          {oidcEnabled && (
            <button
              type="button"
              onClick={onOIDCLogin}
              disabled={working}
              className="mt-2 flex h-10 w-full items-center justify-center gap-2 rounded-md border border-line bg-ink/70 text-xs font-semibold text-ice transition hover:border-pulse/50 disabled:opacity-50"
            >
              <Building2 size={14} /> {t("auth.enterpriseAccount")}
            </button>
          )}
        </form>
      </section>
    </div>,
    document.body,
  );
}
