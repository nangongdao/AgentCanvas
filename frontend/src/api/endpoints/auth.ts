import { apiGet, apiSend, mutateSession } from "@/api/client";

export type AuthRole = "viewer" | "editor" | "admin";

export interface MetaDTO {
  app: string;
  version: string;
  memory_backend: string;
  vector_backend: string;
  database: string;
  environment: string;
  auth_enabled: boolean;
  oidc_enabled: boolean;
  sandbox_backend: string;
  sandbox_degraded: boolean;
}

export interface AuthSessionDTO {
  authenticated: boolean;
  role: AuthRole;
  auth_enabled: boolean;
  language?: "zh" | "en" | null;
}

export function getMeta() {
  return apiGet<MetaDTO>("/api/meta");
}

export function getAuthSession() {
  return apiGet<AuthSessionDTO>("/api/auth/me");
}

export function updateLanguagePreference(language: "zh" | "en") {
  return apiSend<AuthSessionDTO>("/api/auth/me", "PATCH", { language });
}

export type LoginCredentials =
  | { method: "token"; token: string }
  | { method: "password"; email: string; password: string };

export function login(credentials: LoginCredentials) {
  const body =
    credentials.method === "token"
      ? { token: credentials.token }
      : { email: credentials.email, password: credentials.password };
  return mutateSession(() =>
    apiSend<AuthSessionDTO>("/api/auth/login", "POST", body),
  );
}

export function logout() {
  return mutateSession(() => apiSend<void>("/api/auth/logout", "POST"));
}
