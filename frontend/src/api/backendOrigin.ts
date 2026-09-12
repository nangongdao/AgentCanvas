/**
 * Backend origin for the desktop shell (C9 §3.2, ADR 0003 amendment).
 *
 * The packaged shell injects `window.__AGENTCANVAS_BACKEND_ORIGIN__` before
 * any application script runs, so every API call — REST through
 * {@link apiFetch} and the three EventSource consumers — targets the sidecar
 * on its per-launch loopback port directly. The web deployment and `tauri
 * dev` leave the variable unset and keep using same-origin relative paths
 * (Vite proxy), which is why every helper here is a no-op outside desktop.
 */

declare global {
  interface Window {
    __AGENTCANVAS_BACKEND_ORIGIN__?: string;
  }
}

export const BACKEND_ORIGIN: string = (
  window.__AGENTCANVAS_BACKEND_ORIGIN__ ?? ""
).replace(/\/+$/, "");

/** Prefix a relative API path with the desktop sidecar origin when injected. */
export function backendUrl(path: string): string {
  if (!BACKEND_ORIGIN || /^(https?:)?\/\//i.test(path)) return path;
  return `${BACKEND_ORIGIN}${path.startsWith("/") ? path : `/${path}`}`;
}
