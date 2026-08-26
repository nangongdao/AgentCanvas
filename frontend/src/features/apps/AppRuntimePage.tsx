import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  Bot,
  KeyRound,
  Loader2,
  Send,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";

import {
  type AppRuntimeOut,
  ApiRuntimeError,
  createRuntimeSession,
  listRuntimeMessages,
  resolveRuntimeApp,
  runtimeCitationSourceUrl,
  sendRuntimeMessage,
  type ChatMessageDTO,
  type RuntimeFeedbackRating,
  upsertRuntimeFeedback,
} from "@/api/endpoints/app_runtime";
import type { InputFieldDef } from "@/api/endpoints/app_runtime";
import { CitationCards } from "@/features/citations/CitationCards";
import { cn } from "@/utils/cn";

type Phase = "resolving" | "ready" | "missing" | "unauthorized" | "error";

export function AppRuntimePage() {
  const { slug = "" } = useParams<{ slug: string }>();
  const [search] = useSearchParams();
  const token = search.get("t");
  const embed = search.get("embed") === "1";

  const [phase, setPhase] = useState<Phase>("resolving");
  const [app, setApp] = useState<AppRuntimeOut | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    resolveRuntimeApp(slug, token)
      .then((meta) => {
        if (cancelled) return;
        setApp(meta);
        setPhase("ready");
      })
      .catch((err: ApiRuntimeError | Error) => {
        if (cancelled) return;
        const status = err instanceof ApiRuntimeError ? err.status : 0;
        if (status === 404) setPhase("missing");
        else if (status === 401) setPhase("unauthorized");
        else {
          setPhase("error");
          setLoadError(err.message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [slug, token]);

  // Apply the app's theme color as a CSS custom property on the root element
  // so child components can reference it via var(--app-theme) without receiving
  // the raw value as a prop.
  useEffect(() => {
    if (app?.theme_color) {
      document.documentElement.style.setProperty("--app-theme", app.theme_color);
    }
    return () => {
      document.documentElement.style.removeProperty("--app-theme");
    };
  }, [app?.theme_color]);

  if (phase === "resolving") return <CenteredHint text="加载应用中…" spinner />;
  if (phase === "missing") return <NotFoundShell slug={slug} />;
  if (phase === "unauthorized") return <UnauthorizedShell />;
  if (phase === "error")
    return <ErrorShell message={loadError ?? "加载应用失败"} />;
  if (!app) return null;
  if (app.type === "completion")
    return <CompletionRuntime app={app} token={token} slug={slug} embed={embed} />;
  return <ChatbotRuntime app={app} token={token} slug={slug} embed={embed} />;
}

/* ------------------------------------------------------------------ */
/* Chatbot runtime                                                     */
/* ------------------------------------------------------------------ */

interface Turn {
  role: string;
  content: string;
  streaming?: boolean;
  cancelled?: boolean;
  /** Durable message id; assigned after replay or post-send reconcile. */
  id?: string;
  citations?: Record<string, unknown>[];
}

function sessionKey(slug: string): string {
  return `agentcanvas:runtime-session:${slug}`;
}

async function latestRuntimeAssistant(
  slug: string,
  token: string | null,
  sessionId: string,
  executionId: string | null,
): Promise<ChatMessageDTO | null> {
  for (let attempt = 0; attempt < 6; attempt += 1) {
    const rows = await listRuntimeMessages(slug, token, sessionId);
    const assistant = [...rows]
      .reverse()
      .find(
        (message) =>
          message.role === "assistant" &&
          (!executionId || message.execution_id === executionId),
      );
    if (assistant) return assistant;
    if (attempt < 5) {
      await new Promise((resolve) => window.setTimeout(resolve, 50 * (attempt + 1)));
    }
  }
  return null;
}

function ChatbotRuntime({
  app,
  token,
  slug,
  embed,
}: {
  app: AppRuntimeOut;
  token: string | null;
  slug: string;
  embed: boolean;
}) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [feedback, setFeedback] = useState<Record<string, RuntimeFeedbackRating>>({});
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const abortRef = useRef<(() => void) | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const replayedRef = useRef<string | null>(null);

  // Seed the welcome message as the initial system turn.
  useEffect(() => {
    if (app.welcome_message) {
      setTurns([{ role: "system", content: app.welcome_message }]);
    }
  }, [app.welcome_message]);

  // Bootstrap a session on first mount (persist id for reconnect).
  useEffect(() => {
    let cancelled = false;
    const stored = window.localStorage.getItem(sessionKey(slug));
    if (stored) {
      setSessionId(stored);
      return;
    }
    createRuntimeSession(slug, token, { title: app.name })
      .then((msg) => {
        if (cancelled) return;
        setSessionId(msg.session_id);
        window.localStorage.setItem(sessionKey(slug), msg.session_id);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "创建会话失败");
      });
    return () => {
      cancelled = true;
    };
  }, [slug, token, app.name]);

  // Replay transcript once per session (e.g. returning visitor). New sessions
  // have no persisted messages, so this just leaves the welcome turn in place.
  useEffect(() => {
    if (!sessionId || replayedRef.current === sessionId) return;
    replayedRef.current = sessionId;
    let cancelled = false;
    setLoadingHistory(true);
    listRuntimeMessages(slug, token, sessionId)
      .then((rows: ChatMessageDTO[]) => {
        if (cancelled) return;
        const history: Turn[] = rows.map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          citations: m.citations,
        }));
        // Replay carries the durable feedback rating per assistant message.
        const replayed: Record<string, RuntimeFeedbackRating> = {};
        for (const m of rows) {
          if (m.role === "assistant" && m.feedback_rating) {
            replayed[m.id] = m.feedback_rating;
          }
        }
        setFeedback(replayed);
        if (history.length > 0) {
          const seed = app.welcome_message
            ? [{ role: "system", content: app.welcome_message }]
            : [];
          setTurns([...seed, ...history]);
        }
      })
      .catch(() => {
        // History is best-effort; keep welcome message if present.
      })
      .finally(() => {
        if (!cancelled) setLoadingHistory(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, slug, token, app.welcome_message]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [turns]);

  const handleSend = useCallback(
    (override?: string) => {
      const text = (override ?? input).trim();
      if (!text || !sessionId || sending) return;
      setInput("");
      setTurns((prev) => [
        ...prev,
        { role: "user", content: text },
        { role: "assistant", content: "", streaming: true },
      ]);
      setSending(true);
      setError(null);
      let executionId: string | null = null;
      abortRef.current = sendRuntimeMessage(
        slug,
        token,
        sessionId,
        { message: text },
        {
          onExecutionId: (value) => {
            executionId = value;
          },
          onDelta: (delta) => {
            setTurns((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.role === "assistant") {
                next[next.length - 1] = { ...last, content: last.content + delta };
              }
              return next;
            });
          },
          onEvent: () => undefined,
          onDone: () => {
            setTurns((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.role === "assistant") {
                next[next.length - 1] = { ...last, streaming: false };
              }
              return next;
            });
            setSending(false);
            abortRef.current = null;
            // Reconcile against the execution id so a fast read cannot attach
            // the previous assistant message while this reply is committing.
            latestRuntimeAssistant(slug, token, sessionId, executionId)
              .then((lastAssistant) => {
                if (!lastAssistant) return;
                setTurns((prev) => {
                  const next = [...prev];
                  for (let i = next.length - 1; i >= 0; i -= 1) {
                    if (next[i].role === "assistant" && !next[i].id) {
                      next[i] = {
                        ...next[i],
                        id: lastAssistant.id,
                        content: lastAssistant.content,
                        citations: lastAssistant.citations,
                      };
                      break;
                    }
                  }
                  return next;
                });
              })
              .catch(() => {
                /* feedback stays hidden until the next history replay */
              });
          },
          onError: (err) => {
            setError(err.message);
            setSending(false);
            setTurns((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.role === "assistant") {
                // Preserve any partial streamed text; mark the turn so the
                // bubble renders a clear "stopped/failed" state instead of an
                // empty bubble that silently swallowed the user's input.
                next[next.length - 1] = {
                  ...last,
                  streaming: false,
                  content: last.content || "（无回复）",
                };
              }
              return next;
            });
            abortRef.current = null;
          },
        },
      );
    },
    [input, sessionId, sending, slug, token],
  );

  const handleStop = useCallback(() => {
    abortRef.current?.();
    abortRef.current = null;
    setSending(false);
    setTurns((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last && last.role === "assistant") {
        // Mark the in-flight turn as cancelled so the bubble shows the user
        // their input was interrupted (the backend may still complete the run
        // and persist the assistant reply, surfaced on next history replay).
        next[next.length - 1] = {
          ...last,
          streaming: false,
          cancelled: true,
          content: last.content || "（已停止）",
        };
      }
      return next;
    });
  }, []);

  const handleFeedback = useCallback(
    async (messageId: string, rating: RuntimeFeedbackRating) => {
      if (!sessionId) return;
      try {
        if (feedback[messageId] === rating) {
          // Toggle off: the public surface has no delete; submit the opposite
          // is wrong, so just clear the local marker (row remains for editors).
          setFeedback((prev) => {
            const next = { ...prev };
            delete next[messageId];
            return next;
          });
          return;
        }
        await upsertRuntimeFeedback(slug, token, sessionId, messageId, rating);
        setFeedback((prev) => ({ ...prev, [messageId]: rating }));
      } catch (err) {
        setError(err instanceof Error ? err.message : "反馈提交失败");
      }
    },
    [feedback, sessionId, slug, token],
  );

  return (
    <div className="flex h-[100dvh] w-full flex-col bg-void text-ice">
      {!embed && (
        <header
          className="glass flex min-h-12 items-center gap-2 border-b border-line px-3 py-2 sm:px-4"
          style={app.theme_color ? { borderColor: `${app.theme_color}33` } : undefined}
        >
          <span
            className="flex h-7 w-7 items-center justify-center rounded-full border bg-volt/10 text-volt"
            style={app.theme_color ? { borderColor: app.theme_color, color: app.theme_color } : undefined}
          >
            <Bot size={13} />
          </span>
          <div className="min-w-0 flex-1">
            <h1 className="truncate font-display text-sm font-semibold text-ice">{app.name}</h1>
            <p className="font-mono text-[9px] uppercase tracking-widest text-ghost/50">
              {app.visibility === "link" ? "link · token-protected" : "public"}
            </p>
          </div>
          {app.requires_token && (
            <span className="flex items-center gap-1 rounded-md border border-warn/40 bg-warn/10 px-2 py-0.5 text-[10px] text-warn">
              <KeyRound size={11} /> 受保护
            </span>
          )}
        </header>
      )}

      {error && (
        <div className="flex min-h-9 items-center gap-2 border-b border-bad/30 bg-bad/10 px-3 text-xs text-bad">
          <AlertTriangle size={12} className="shrink-0" />
          <span className="min-w-0 flex-1 truncate">{error}</span>
          <button
            type="button"
            onClick={() => setError(null)}
            className="h-7 rounded-md px-2 font-mono text-[9px] uppercase hover:bg-bad/10"
          >
            dismiss
          </button>
        </div>
      )}

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto px-3 py-4 sm:px-4">
        <div className="mx-auto max-w-3xl space-y-4">
          {loadingHistory && turns.length === 0 ? (
            <div className="flex items-center gap-2 text-ghost/60">
              <Loader2 size={14} className="animate-spin" />
              <span className="text-xs">加载历史…</span>
            </div>
          ) : turns.length === 0 ? (
            <div className="flex flex-1 items-center justify-center py-12 text-center">
              <p className="text-sm text-ghost/50">开始对话吧。</p>
            </div>
          ) : (
            turns.map((turn, i) => (
              <div key={turn.id ?? `turn-${i}`} className="space-y-1">
                <RuntimeBubble
                  turn={turn}
                  slug={slug}
                  token={token}
                  sessionId={sessionId}
                />
                {!turn.streaming && !turn.cancelled && turn.id && turn.role === "assistant" && (
                  <div className="flex items-center gap-1 pl-10">
                    <button
                      type="button"
                      onClick={() => void handleFeedback(turn.id as string, "positive")}
                      className={cn(
                        "flex h-6 w-6 items-center justify-center rounded-md transition",
                        feedback[turn.id] === "positive"
                          ? "bg-volt/20 text-volt"
                          : "text-ghost/40 hover:bg-line hover:text-volt",
                      )}
                      title="这个回答有帮助"
                      aria-label="这个回答有帮助"
                    >
                      <ThumbsUp size={12} />
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleFeedback(turn.id as string, "negative")}
                      className={cn(
                        "flex h-6 w-6 items-center justify-center rounded-md transition",
                        feedback[turn.id] === "negative"
                          ? "bg-bad/20 text-bad"
                          : "text-ghost/40 hover:bg-line hover:text-bad",
                      )}
                      title="这个回答没有帮助"
                      aria-label="这个回答没有帮助"
                    >
                      <ThumbsDown size={12} />
                    </button>
                  </div>
                )}
              </div>
            ))
          )}
          {!sending && turns.length <= 2 && app.suggested_questions.length > 0 && (
            <div className="flex flex-wrap gap-2 pt-2">
              {app.suggested_questions.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => handleSend(q)}
                  className="flex items-center gap-1.5 rounded-full border border-line bg-ink/60 px-3 py-1.5 text-xs text-ghost transition hover:border-pulse/40 hover:bg-pulse/5 hover:text-ice"
                >
                  <Sparkles size={11} className="text-pulse" />
                  {q}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-line bg-ink/40 px-3 py-3 sm:px-4">
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void handleSend();
              }
            }}
            rows={1}
            placeholder="输入消息，Enter 发送"
            className="field-input min-h-[40px] max-h-40 flex-1 resize-none"
            disabled={sending || !sessionId || loadingHistory}
          />
          {sending ? (
            <button
              type="button"
              onClick={handleStop}
              className="flex h-10 items-center gap-1.5 rounded-md border border-warn/40 bg-warn/10 px-3 text-xs font-medium text-warn transition hover:bg-warn/20"
            >
              <X size={14} /> 停止
            </button>
          ) : (
            <button
              type="button"
              onClick={() => handleSend()}
              disabled={!input.trim() || !sessionId || loadingHistory}
              className="flex h-10 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void transition hover:brightness-110 disabled:opacity-40"
              style={app.theme_color ? { backgroundColor: app.theme_color } : undefined}
            >
              <Send size={14} /> 发送
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Completion runtime                                                  */
/* ------------------------------------------------------------------ */

function CompletionRuntime({
  app,
  token,
  slug,
  embed,
}: {
  app: AppRuntimeOut;
  token: string | null;
  slug: string;
  embed: boolean;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<string>("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [assistantMessage, setAssistantMessage] = useState<ChatMessageDTO | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<(() => void) | null>(null);

  // Seed default values from the input form definition.
  useEffect(() => {
    const seeded: Record<string, string> = {};
    for (const field of app.input_form) {
      if (field.default !== undefined && field.default !== null) {
        seeded[field.name] = String(field.default);
      }
    }
    setValues(seeded);
  }, [app.input_form]);

  const handleSubmit = useCallback(async () => {
    const requiredMissing = app.input_form.filter(
      (f) => f.required && !values[f.name]?.trim(),
    );
    if (requiredMissing.length > 0) {
      setError(`请填写必填项：${requiredMissing.map((f) => f.name).join("、")}`);
      return;
    }
    // The completion form doubles as a single-turn chat: the primary input
    // (first field) is the user message, the rest become workflow inputs.
    const [primary, ...rest] = app.input_form;
    const messageField = primary ?? rest[0];
    const message = messageField ? values[messageField.name] ?? "" : "";
    const inputs: Record<string, unknown> = {};
    for (const f of app.input_form) {
      if (f.name in values) inputs[f.name] = values[f.name];
    }
    setError(null);
    setResult("");
    setAssistantMessage(null);
    setSessionId(null);
    setStreaming(true);
    try {
      // Completion runs are single-turn but still require a session bound to
      // the app's workflow — create one inline, then stream the reply.
      const created = await createRuntimeSession(slug, token, {
        title: app.name,
        inputs,
      });
      setSessionId(created.session_id);
      let executionId: string | null = null;
      abortRef.current = sendRuntimeMessage(
        slug,
        token,
        created.session_id,
        { message: message || "completion", inputs },
        {
          onExecutionId: (value) => {
            executionId = value;
          },
          onDelta: (delta) => setResult((prev) => prev + delta),
          onEvent: () => undefined,
          onDone: () => {
            abortRef.current = null;
            void latestRuntimeAssistant(
              slug,
              token,
              created.session_id,
              executionId,
            )
              .then((persisted) => {
                if (!persisted) {
                  setError("结果尚未完成持久化，请重试");
                  return;
                }
                setResult(persisted.content);
                setAssistantMessage(persisted);
              })
              .catch((err) => {
                setError(err instanceof Error ? err.message : "结果回载失败");
              })
              .finally(() => setStreaming(false));
          },
          onError: (err) => {
            setError(err.message);
            setStreaming(false);
            abortRef.current = null;
          },
        },
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建会话失败");
      setStreaming(false);
    }
  }, [app.input_form, app.name, values, slug, token]);

  const handleStop = useCallback(() => {
    abortRef.current?.();
    abortRef.current = null;
    setStreaming(false);
  }, []);

  return (
    <div className="flex min-h-[100dvh] w-full flex-col bg-void text-ice">
      {!embed && (
        <header
          className="glass flex min-h-12 items-center gap-2 border-b border-line px-3 py-2 sm:px-4"
          style={app.theme_color ? { borderColor: `${app.theme_color}33` } : undefined}
        >
          <span
            className="flex h-7 w-7 items-center justify-center rounded-full border bg-volt/10 text-volt"
            style={app.theme_color ? { borderColor: app.theme_color, color: app.theme_color } : undefined}
          >
            <Sparkles size={13} />
          </span>
          <div className="min-w-0 flex-1">
            <h1 className="truncate font-display text-sm font-semibold text-ice">{app.name}</h1>
            <p className="font-mono text-[9px] uppercase tracking-widest text-ghost/50">completion</p>
          </div>
        </header>
      )}

      {app.welcome_message && (
        <div className="border-b border-line bg-ink/40 px-3 py-2 text-xs text-ghost sm:px-4">
          {app.welcome_message}
        </div>
      )}

      {error && (
        <div className="flex min-h-9 items-center gap-2 border-b border-bad/30 bg-bad/10 px-3 text-xs text-bad">
          <AlertTriangle size={12} className="shrink-0" />
          <span className="min-w-0 flex-1 truncate">{error}</span>
          <button
            type="button"
            onClick={() => setError(null)}
            className="h-7 rounded-md px-2 font-mono text-[9px] uppercase hover:bg-bad/10"
          >
            dismiss
          </button>
        </div>
      )}

      <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-4 px-3 py-4 sm:px-4">
        <div className="glass space-y-3 rounded-xl border border-line p-4">
          {app.input_form.map((field: InputFieldDef) => (
            <label key={field.name} className="block">
              <span className="mb-1 flex items-center gap-1 text-xs text-ghost">
                {field.name}
                {field.required && <span className="text-bad">*</span>}
              </span>
              <input
                type={field.type === "number" ? "number" : "text"}
                value={values[field.name] ?? ""}
                onChange={(e) =>
                  setValues((prev) => ({ ...prev, [field.name]: e.target.value }))
                }
                placeholder={field.default ? String(field.default) : `输入 ${field.name}`}
                className="field-input w-full"
                disabled={streaming}
              />
            </label>
          ))}
          <div className="flex justify-end">
            {streaming ? (
              <button
                type="button"
                onClick={handleStop}
                className="flex h-9 items-center gap-1.5 rounded-md border border-warn/40 bg-warn/10 px-3 text-xs font-medium text-warn transition hover:bg-warn/20"
              >
                <X size={13} /> 停止
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void handleSubmit()}
                disabled={streaming}
                className="flex h-9 items-center gap-1.5 rounded-md bg-pulse px-4 text-xs font-semibold text-void transition hover:brightness-110 disabled:opacity-40"
                style={app.theme_color ? { backgroundColor: app.theme_color } : undefined}
              >
                <Send size={13} /> 提交
              </button>
            )}
          </div>
        </div>

        {(result || streaming) && (
          <div className="glass flex-1 rounded-xl border border-line p-4">
            <div className="mb-2 flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-widest text-ghost/60">
              <Bot size={11} className="text-volt" /> result
            </div>
            <div className="whitespace-pre-wrap text-[13px] leading-relaxed text-ice/90">
              {result}
              {streaming && (
                <span className="ml-0.5 inline-block h-3 w-1.5 animate-pulse bg-pulse align-middle" />
              )}
            </div>
            {assistantMessage && sessionId && (
              <CitationCards
                citations={assistantMessage.citations}
                sourceTarget="_blank"
                sourceHref={(citation) =>
                  runtimeCitationSourceUrl(
                    slug,
                    token,
                    sessionId,
                    assistantMessage.id,
                    citation.id,
                  )
                }
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Shared UI                                                            */
/* ------------------------------------------------------------------ */

function RuntimeBubble({
  turn,
  slug,
  token,
  sessionId,
}: {
  turn: Turn;
  slug: string;
  token: string | null;
  sessionId: string | null;
}) {
  const isUser = turn.role === "user";
  const isSystem = turn.role === "system";
  if (isSystem) {
    return (
      <div className="mx-auto max-w-[80%] rounded-lg border border-line bg-ink/40 px-3 py-2 text-center text-xs text-ghost/70">
        {turn.content}
      </div>
    );
  }
  return (
    <div className={cn("flex gap-3 animate-fade-up", isUser && "flex-row-reverse")}>
      <span
        className={cn(
          "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border",
          isUser
            ? "border-pulse/40 bg-pulse/10 text-pulse"
            : turn.cancelled
              ? "border-warn/40 bg-warn/10 text-warn"
              : "border-volt/40 bg-volt/10 text-volt",
        )}
      >
        {isUser ? <Send size={11} /> : <Bot size={13} />}
      </span>
      <div className="min-w-0 max-w-[80%]">
        <div
          className={cn(
            "whitespace-pre-wrap rounded-lg border px-3.5 py-2.5 text-[13px] leading-relaxed",
            isUser
              ? "border-pulse/30 bg-pulse/5 text-ice"
              : turn.cancelled
                ? "border-warn/30 bg-warn/5 text-warn"
                : "border-line bg-ink/60 text-ice/90",
          )}
        >
          {turn.content || (turn.streaming ? "…" : "")}
          {turn.streaming && (
            <span className="ml-0.5 inline-block h-3 w-1.5 animate-pulse bg-pulse align-middle" />
          )}
        </div>
        {!isUser && !turn.streaming && turn.id && sessionId && (
          <CitationCards
            citations={turn.citations}
            sourceTarget="_blank"
            sourceHref={(citation) =>
              runtimeCitationSourceUrl(
                slug,
                token,
                sessionId,
                turn.id as string,
                citation.id,
              )
            }
          />
        )}
      </div>
    </div>
  );
}

function CenteredHint({ text, spinner }: { text: string; spinner?: boolean }) {
  return (
    <div className="flex h-[100dvh] w-full items-center justify-center bg-void text-ghost/60">
      {spinner && <Loader2 size={16} className="mr-2 animate-spin" />}
      <span className="text-sm">{text}</span>
    </div>
  );
}

function NotFoundShell({ slug }: { slug: string }) {
  return (
    <Shell title="应用不存在">
      <p className="text-sm text-ghost/70">
        没有找到名为 <code className="font-mono text-ice">{slug}</code> 的公开应用，
        或该应用未对公众开放。
      </p>
    </Shell>
  );
}

function UnauthorizedShell() {
  return (
    <Shell title="访问令牌无效">
      <p className="text-sm text-ghost/70">
        这是一个受保护的应用，需要有效的访问令牌。请确认链接是否完整、
        或联系应用发布者重新获取令牌。
      </p>
    </Shell>
  );
}

function ErrorShell({ message }: { message: string }) {
  return (
    <Shell title="加载失败">
      <p className="text-sm text-bad">{message}</p>
    </Shell>
  );
}

function Shell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex h-[100dvh] w-full items-center justify-center bg-void px-4">
      <div className="glass w-full max-w-md rounded-xl border border-line p-6 text-center">
        <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full border border-line bg-ink/60 text-ghost">
          <AlertTriangle size={18} />
        </div>
        <h1 className="mb-2 font-display text-base font-semibold text-ice">{title}</h1>
        {children}
      </div>
    </div>
  );
}
