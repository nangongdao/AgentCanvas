/** Chat API endpoints (P5). */

import { apiFetch, apiGet, apiSend } from "@/api/client";
import {
  type PageQuery,
  type PageResult,
  withQuery,
} from "@/api/pagination";

export interface ChatSessionDTO {
  id: string;
  title: string;
  workflow_id?: string | null;
  model_config_id?: string | null;
  inputs: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export interface ChatMessageDTO {
  id: string;
  session_id: string;
  role: string;
  content: string;
  node_ref?: string | null;
  execution_id?: string | null;
  citations: Record<string, unknown>[];
  /** Durable end-user rating on assistant messages (C3-4). */
  feedback_rating?: ChatFeedbackRating | null;
  created_at?: string;
}

export interface ChatSessionCreate {
  title?: string;
  workflow_id?: string | null;
  model_config_id?: string | null;
  inputs?: Record<string, unknown>;
}

export function listChatSessions(workflowId?: string, query: PageQuery = {}) {
  return apiGet<PageResult<ChatSessionDTO>>(
    withQuery("/api/chat/sessions", { ...query, workflow_id: workflowId }),
  );
}

export function createChatSession(body: ChatSessionCreate) {
  return apiSend<ChatSessionDTO>("/api/chat/sessions", "POST", body);
}

export function getChatSession(id: string) {
  return apiGet<ChatSessionDTO>(`/api/chat/sessions/${id}`);
}

export function deleteChatSession(id: string) {
  return apiSend<void>(`/api/chat/sessions/${id}`, "DELETE");
}

export function listChatMessages(sessionId: string, query: PageQuery = {}) {
  return apiGet<PageResult<ChatMessageDTO>>(
    withQuery(`/api/chat/sessions/${sessionId}/messages`, query),
  );
}

// ---- C3-4: chat deepening ----

export type ChatFeedbackRating = "positive" | "negative";

export interface ChatFeedbackDTO {
  id: string;
  message_id: string;
  session_id: string;
  rating: ChatFeedbackRating;
  comment: string;
  promoted_dataset_version_id: string | null;
  promoted_at: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface ChatPromoteResult {
  dataset_id: string;
  dataset_version_id: string;
  case_id: string;
  feedback_id: string;
}

export interface ChatSessionVariableDTO {
  name: string;
  value: unknown;
  updated_at?: string | null;
}

export function upsertChatFeedback(
  sessionId: string,
  messageId: string,
  rating: ChatFeedbackRating,
  comment = "",
) {
  return apiSend<ChatFeedbackDTO>(
    `/api/chat/sessions/${sessionId}/messages/${messageId}/feedback`,
    "POST",
    { rating, comment },
  );
}

export function getChatFeedback(sessionId: string, messageId: string) {
  return apiGet<ChatFeedbackDTO>(
    `/api/chat/sessions/${sessionId}/messages/${messageId}/feedback`,
  );
}

export function deleteChatFeedback(sessionId: string, messageId: string) {
  return apiSend<void>(
    `/api/chat/sessions/${sessionId}/messages/${messageId}/feedback`,
    "DELETE",
  );
}

export function promoteChatMessage(
  sessionId: string,
  messageId: string,
  body: { dataset_name?: string; dataset_id?: string; case_name?: string; expected?: unknown; change_summary?: string } = {},
) {
  return apiSend<ChatPromoteResult>(
    `/api/chat/sessions/${sessionId}/messages/${messageId}/promote`,
    "POST",
    body,
  );
}

export function listChatVariables(sessionId: string) {
  return apiGet<{ variables: Record<string, unknown> }>(
    `/api/chat/sessions/${sessionId}/variables`,
  );
}

export function upsertChatVariable(sessionId: string, name: string, value: unknown) {
  return apiSend<ChatSessionVariableDTO>(
    `/api/chat/sessions/${sessionId}/variables/${encodeURIComponent(name)}`,
    "PUT",
    { name, value },
  );
}

export function deleteChatVariable(sessionId: string, name: string) {
  return apiSend<void>(
    `/api/chat/sessions/${sessionId}/variables/${encodeURIComponent(name)}`,
    "DELETE",
  );
}

/** Download-link URL for session export (cookie-authenticated same-origin GET). */
export function chatExportUrl(sessionId: string, format: "json" | "markdown") {
  return `/api/chat/sessions/${sessionId}/export?format=${format}`;
}

export interface ChatStreamHandlers {
  onExecutionId?: (executionId: string) => void;
  onDelta: (text: string) => void;
  onEvent: (event: Record<string, unknown>) => void;
  onDone: () => void;
  onError: (err: Error) => void;
}

/** Stream a chat turn via SSE. Returns a function to abort the stream. */
export function sendChatMessage(
  sessionId: string,
  message: string,
  handlers: ChatStreamHandlers,
): () => void {
  return streamChatTurn(
    `/api/chat/sessions/${sessionId}/send`,
    { message },
    handlers,
  );
}

/** Regenerate the assistant reply preceding a user turn (C3-4). */
export function regenerateChatMessage(
  sessionId: string,
  messageId: string,
  handlers: ChatStreamHandlers,
): () => void {
  return streamChatTurn(
    `/api/chat/sessions/${sessionId}/regenerate?message_id=${encodeURIComponent(messageId)}`,
    {},
    handlers,
  );
}

/** Edit a user message, truncate later turns, and re-run the workflow (C3-4). */
export function editChatMessage(
  sessionId: string,
  messageId: string,
  message: string,
  handlers: ChatStreamHandlers,
): () => void {
  return streamChatTurn(
    `/api/chat/sessions/${sessionId}/messages/${messageId}/edit`,
    { message },
    handlers,
  );
}

function streamChatTurn(
  url: string,
  body: Record<string, unknown>,
  handlers: ChatStreamHandlers,
): () => void {
  const controller = new AbortController();
  void (async () => {
    try {
      const res = await apiFetch(url, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!res.ok || !res.body) {
        throw new Error(`chat send failed: HTTP ${res.status}`);
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
            const event = JSON.parse(data);
            handlers.onEvent(event);
            const payload = (event.payload as Record<string, unknown>) ?? {};
            if (
              event.event_type === "node_streaming" &&
              payload.kind === "text" &&
              typeof payload.delta === "string"
            ) {
              handlers.onDelta(payload.delta);
            }
            if (
              event.event_type === "workflow_finished" ||
              event.event_type === "workflow_failed" ||
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
