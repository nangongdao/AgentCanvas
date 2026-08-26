import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { AUTH_REQUIRED_EVENT, ApiError } from "@/api/client";
import {
  getAuthSession,
  getMeta,
  login as loginRequest,
  logout as logoutRequest,
  type AuthRole,
  type LoginCredentials,
} from "@/api/endpoints/auth";
import { AuthDialog } from "@/features/auth/AuthDialog";
import { runSessionCleanups } from "@/features/auth/sessionCleanup";
import { useI18nStore, tNow } from "@/features/i18n/i18n";

const LEVEL: Record<AuthRole, number> = { viewer: 10, editor: 20, admin: 30 };

// The standalone app runtime (/apps/p/<slug>) serves unauthenticated end
// users; it must never probe the platform session or surface the login
// dialog over the public surface.
const PUBLIC_RUNTIME_PATH = /^\/apps\/p\//;

interface AuthContextValue {
  ready: boolean;
  authenticated: boolean;
  authEnabled: boolean;
  role: AuthRole;
  can: (required: AuthRole) => boolean;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue>({
  ready: false,
  authenticated: false,
  authEnabled: false,
  role: "viewer",
  can: () => false,
  logout: async () => undefined,
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [authEnabled, setAuthEnabled] = useState(false);
  const [role, setRole] = useState<AuthRole>("viewer");
  const [loginRequired, setLoginRequired] = useState(false);
  const [oidcEnabled, setOidcEnabled] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [loggingIn, setLoggingIn] = useState(false);

  useEffect(() => {
    if (PUBLIC_RUNTIME_PATH.test(window.location.pathname)) {
      // Public runtime page: skip the session probe entirely so an
      // unauthenticated visitor never sees the platform login dialog.
      setReady(true);
      return;
    }
    const requireLogin = () => setLoginRequired(true);
    window.addEventListener(AUTH_REQUIRED_EVENT, requireLogin);
    void getMeta()
      .then(async (meta) => {
        setAuthEnabled(meta.auth_enabled);
        setOidcEnabled(meta.oidc_enabled);
        if (!meta.auth_enabled) {
          setRole("admin");
          setAuthenticated(true);
          return;
        }
        const session = await getAuthSession();
        setRole(session.role);
        setAuthenticated(true);
        setLoginRequired(false);
        // C5-9: adopt the account's server-side language when the browser
        // has no explicit local choice (cross-device preference follow).
        useI18nStore.getState().hydrateFromSession(session.language);
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 401) {
          setAuthEnabled(true);
          setAuthenticated(false);
          setLoginRequired(true);
        }
      })
      .finally(() => setReady(true));
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, requireLogin);
  }, []);

  const login = useCallback(async (credentials: LoginCredentials) => {
    setLoggingIn(true);
    setLoginError(null);
    try {
      await loginRequest(credentials);
      window.location.reload();
    } catch (error) {
      setLoginError(
        error instanceof ApiError && typeof error.detail === "string"
          ? error.detail
          : tNow("auth.signInFailed"),
      );
    } finally {
      setLoggingIn(false);
    }
  }, []);

  const logout = useCallback(async () => {
    await runSessionCleanups();
    await logoutRequest();
    window.location.reload();
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      ready,
      authenticated,
      authEnabled,
      role,
      can: (required) =>
        ready && authenticated && LEVEL[role] >= LEVEL[required],
      logout,
    }),
    [authEnabled, authenticated, logout, ready, role],
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
      <AuthDialog
        open={authEnabled && loginRequired}
        working={loggingIn}
        error={loginError}
        oidcEnabled={oidcEnabled}
        onLogin={login}
        onOIDCLogin={() => window.location.assign("/api/auth/oidc/start")}
      />
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
