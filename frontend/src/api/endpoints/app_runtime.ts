/** Public application runtime API endpoints (C3-2). */

import { backendUrl } from "@/api/backendOrigin";

export interface InputFieldDef {
  name: string;
  type: string;
  required: boolean;
  default: unknown;
}

export type AppType = "chatbot" | "completion" | "api";
export type AppVisibility = "project" | "link" | "public";
export type AppStatus = "active" | "disabled";

export interface AppRuntimeOut {
  slug: string;
  name: string;
  icon: string | null;
  type: AppType;
  welcome_message: string | null;
  suggested_questions: string[];
  input_form: InputFieldDef[];
  visibility: AppVisibility;
  status: AppStatus;
  requires_token: boolean;
  theme_color: string | null;
  embed_enabled: boolean;
}

export interface AppRuntimeSessionCreate {
  inputs?: Record<string, unknown>;
  title?: string;
}

export interface AppRuntimeSend {
  message: string;
  inputs?: Record<string, unknown>;
}

export interface ChatMessageDTO {
  id: string;
  session_id: string;
  role: string;
  content: string;
  node_ref?: string | null;
  execution_id?: string | null;
  citations: Record<string, unknown>[];
  feedback_rating?: RuntimeFeedbackRating | null;
  created_at?: string;
}

export type RuntimeFeedbackRating = "positive" | "negative";

export interface RuntimeFeedbackDTO {
  id: string;
  message_id: string;
  session_id: string;
  rating: RuntimeFeedbackRating;
  comment: string;
  promoted_dataset_version_id: string | null;
  promoted_at: string | null;
}

/** End-user 👍/👎 on a runtime assistant reply (C3-4, durable store). */
export async function upsertRuntimeFeedback(
  slug: string,
  token: string | null,
  sessionId: string,
  messageId: string,
  rating: RuntimeFeedbackRating,
  comment = "",
): Promise<RuntimeFeedbackDTO> {
  const res = await fetch(
    backendUrl(`/api/apps/p/${slug}/sessions/${sessionId}/messages/${messageId}/feedback${tokenQuery(token)}`),
    {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ rating, comment }),
    },
  );
  if (!res.ok) throw new ApiRuntimeError(res.status, await detailOf(res));
  return (await res.json()) as RuntimeFeedbackDTO;
}

/**
 * Build the optional ``?t=<token>`` query suffix for link-protected apps.
 * Public apps pass no token; the query string collapses to empty.
 */
function tokenQuery(token: string | null): string {
  if (!token) return "";
  return `?t=${encodeURIComponent(token)}`;
}

/** Message-scoped source URL for a citation exposed by a public app. */
export function runtimeCitationSourceUrl(
  slug: string,
  token: string | null,
  sessionId: string,
  messageId: string,
  citationId: string,
): string {
  const segments = [slug, sessionId, messageId, citationId].map(encodeURIComponent);
  return (
    `/api/apps/p/${segments[0]}/sessions/${segments[1]}/messages/${segments[2]}` +
    `/citations/${segments[3]}/source${tokenQuery(token)}`
  );
}

/** Resolve a runtime app's public metadata. 401/404 surface as errors. */
export async function resolveRuntimeApp(
  slug: string,
  token: string | null,
): Promise<AppRuntimeOut> {
  const res = await fetch(backendUrl(`/api/apps/p/${slug}${tokenQuery(token)}`), {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) throw new ApiRuntimeError(res.status, await detailOf(res));
  return (await res.json()) as AppRuntimeOut;
}

/** Create a runtime chat session bound to the app's published workflow. */
export async function createRuntimeSession(
  slug: string,
  token: string | null,
  body: AppRuntimeSessionCreate,
): Promise<ChatMessageDTO> {
  const res = await fetch(backendUrl(`/api/apps/p/${slug}/sessions${tokenQuery(token)}`), {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiRuntimeError(res.status, await detailOf(res));
  return (await res.json()) as ChatMessageDTO;
}

/** Replay a runtime session's transcript (for reconnect/history). */
export async function listRuntimeMessages(
  slug: string,
  token: string | null,
  sessionId: string,
): Promise<ChatMessageDTO[]> {
  const res = await fetch(
    backendUrl(`/api/apps/p/${slug}/sessions/${sessionId}/messages${tokenQuery(token)}`),
    { credentials: "include", headers: { Accept: "application/json" } },
  );
  if (!res.ok) throw new ApiRuntimeError(res.status, await detailOf(res));
  return (await res.json()) as ChatMessageDTO[];
}

export interface RuntimeStreamHandlers {
  onExecutionId?: (executionId: string) => void;
  onDelta: (text: string) => void;
  onEvent: (event: Record<string, unknown>) => void;
  onDone: () => void;
  onError: (err: Error) => void;
}

/**
 * Stream a runtime turn via SSE. Returns an abort function.
 *
 * Unlike the platform chat stream, this does NOT trigger the platform auth
 * redirect on 401 — a link-app token failure surfaces as a plain error so the
 * standalone page can show "invalid access token" instead of forcing a login.
 */
export function sendRuntimeMessage(
  slug: string,
  token: string | null,
  sessionId: string,
  body: AppRuntimeSend,
  handlers: RuntimeStreamHandlers,
): () => void {
  const controller = new AbortController();
  void (async () => {
    try {
      const res = await fetch(
        backendUrl(`/api/apps/p/${slug}/sessions/${sessionId}/send${tokenQuery(token)}`),
        {
          method: "POST",
          credentials: "include",
          headers: {
            Accept: "text/event-stream",
            "Content-Type": "application/json",
          },
          body: JSON.stringify(body),
          signal: controller.signal,
        },
      );
      if (!res.ok || !res.body) {
        throw new ApiRuntimeError(res.status, await detailOf(res));
      }
      const executionId = res.headers.get("X-Execution-ID");
      if (executionId) handlers.onExecutionId?.(executionId);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          const data = line.slice(5).trim();
          if (!data) continue;
          try {
            const event = JSON.parse(data) as Record<string, unknown>;
            handlers.onEvent(event);
            const payload = (event.payload as Record<string, unknown>) ?? {};
            if (
              event.event_type === "node_streaming" &&
              payload.kind === "text" &&
              typeof payload.delta === "string"
            ) {
              handlers.onDelta(payload.delta);
            }
            if (event.event_type === "workflow_failed") {
              const message =
                (typeof payload.error === "string" && payload.error) ||
                (typeof payload.message === "string" && payload.message) ||
                "工作流执行失败";
              handlers.onError(new Error(message));
              return;
            }
            if (
              event.event_type === "workflow_finished" ||
              event.event_type === "workflow_cancelled"
            ) {
              handlers.onDone();
              return;
            }
          } catch {
            // keepalive comment or non-JSON line
          }
        }
      }
      handlers.onDone();
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      handlers.onError(err instanceof Error ? err : new Error("stream failed"));
    }
  })();
  return () => controller.abort();
}

async function detailOf(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return typeof body.detail === "string" ? body.detail : JSON.stringify(body);
  } catch {
    return res.statusText;
  }
}

/** Error that does NOT trip the platform auth-required redirect. */
export class ApiRuntimeError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail || `HTTP ${status}`);
    this.status = status;
  }
}
