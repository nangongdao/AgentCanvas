/** Thin fetch wrapper that talks to the Vite-proxied backend. */

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

export const AUTH_REQUIRED_EVENT = "agentcanvas:auth-required";

let refreshPromise: Promise<boolean> | null = null;
let sessionOperationTail: Promise<void> = Promise.resolve();
let sessionGeneration = 0;
const REFRESH_LOCK_NAME = "agentcanvas-session-refresh";

function canRefresh(path: string): boolean {
  return path === "/api/auth/me" || !path.startsWith("/api/auth/");
}

async function performRefresh(): Promise<boolean> {
  return fetch("/api/auth/refresh", {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json" },
  })
    .then((response) => response.ok)
    .catch(() => false);
}

async function sessionAlreadyRefreshed(): Promise<boolean> {
  return fetch("/api/auth/me", {
    credentials: "include",
    headers: { Accept: "application/json" },
  })
    .then((response) => response.ok)
    .catch(() => false);
}

async function withSessionLock<T>(operation: () => Promise<T>): Promise<T> {
  if (typeof navigator === "undefined" || navigator.locks === undefined) {
    return operation();
  }
  return navigator.locks.request(REFRESH_LOCK_NAME, operation);
}

function serializeSessionOperation<T>(operation: () => Promise<T>): Promise<T> {
  const result = sessionOperationTail.then(operation);
  sessionOperationTail = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

async function refreshAcrossTabs(): Promise<boolean> {
  return withSessionLock(async () => {
    if (await sessionAlreadyRefreshed()) return true;
    return performRefresh();
  });
}

async function refreshSession(expectedGeneration: number): Promise<boolean> {
  if (refreshPromise === null) {
    refreshPromise = serializeSessionOperation(async () => {
      if (expectedGeneration !== sessionGeneration) return true;
      return refreshAcrossTabs();
    }).finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

export function mutateSession<T>(operation: () => Promise<T>): Promise<T> {
  return serializeSessionOperation(async () => {
    const result = await withSessionLock(operation);
    sessionGeneration += 1;
    return result;
  });
}

export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const requestUrl =
    input instanceof Request
      ? input.url
      : new URL(String(input), window.location.origin).toString();
  const path = new URL(requestUrl).pathname;
  const requestGeneration = sessionGeneration;
  const request = new Request(input instanceof Request ? input : requestUrl, {
    ...init,
    credentials: "include",
  });
  const send = () => fetch(request.clone());
  const response = await send();
  if (response.status !== 401 || !canRefresh(path)) {
    return response;
  }
  if (requestGeneration !== sessionGeneration) return send();
  if (!(await refreshSession(requestGeneration))) {
    if (requestGeneration !== sessionGeneration) return send();
    return response;
  }
  return send();
}

async function parseError(res: Response): Promise<ApiError> {
  try {
    const body = await res.json();
    return new ApiError(res.status, body.detail ?? body);
  } catch {
    return new ApiError(res.status, res.statusText);
  }
}

async function responseError(res: Response): Promise<ApiError> {
  const error = await parseError(res);
  if (res.status === 401 && typeof window !== "undefined") {
    window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
  }
  return error;
}

export async function apiGet<T>(
  path: string,
  options: { signal?: AbortSignal } = {},
): Promise<T> {
  const res = await apiFetch(path, {
    credentials: "include",
    headers: { Accept: "application/json" },
    signal: options.signal,
  });
  if (!res.ok) throw await responseError(res);
  return res.json() as Promise<T>;
}

export async function apiSend<T>(
  path: string,
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  body?: unknown,
): Promise<T> {
  const res = await apiFetch(path, {
    method,
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw await responseError(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const res = await apiFetch(path, {
    method: "POST",
    credentials: "include",
    headers: { Accept: "application/json" },
    body: form,
  });
  if (!res.ok) throw await responseError(res);
  return res.json() as Promise<T>;
}

/** Filename advertised by a `Content-Disposition: attachment` response. */
function attachmentFilename(res: Response, fallback: string): string {
  const header = res.headers.get("Content-Disposition") ?? "";
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header);
  const raw = match?.[1]?.trim();
  if (!raw) return fallback;
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

/** Fetch a credentialed endpoint and hand its body to the browser as a file
 * download. The response is buffered so an error status still surfaces as an
 * `ApiError` instead of a downloaded error page, and the session-refresh
 * retry in `apiFetch` keeps working — a bare `<a href>` would bypass both.
 * Returns the saved filename (server-advertised when available). */
export async function apiDownload(path: string, fallbackName: string): Promise<string> {
  const res = await apiFetch(path, {
    credentials: "include",
    headers: { Accept: "*/*" },
  });
  if (!res.ok) throw await responseError(res);
  const filename = attachmentFilename(res, fallbackName);
  const url = URL.createObjectURL(await res.blob());
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
  return filename;
}
