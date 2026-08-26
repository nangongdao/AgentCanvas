import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bot,
  Download,
  Loader2,
  MessageSquarePlus,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Send,
  Settings2,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  Variable,
  X,
} from "lucide-react";

import {
  type ChatFeedbackRating,
  type ChatMessageDTO,
  type ChatSessionDTO,
  chatExportUrl,
  createChatSession,
  deleteChatFeedback,
  deleteChatSession,
  deleteChatVariable,
  editChatMessage,
  listChatMessages,
  listChatSessions,
  listChatVariables,
  promoteChatMessage,
  regenerateChatMessage,
  sendChatMessage,
  upsertChatFeedback,
  upsertChatVariable,
} from "@/api/endpoints/chat";
import { listWorkflows, type WorkflowDTO } from "@/api/endpoints/workflows";
import { cancelExecution } from "@/api/endpoints/workflows";
import { useAuth } from "@/features/auth/AuthProvider";
import { TurnBubble, WorkflowPicker, type Turn } from "@/features/chat/ChatComponents";
import { cn } from "@/utils/cn";

interface StreamControls {
  setExecution: (id: string) => void;
}

export function ChatPage() {
  const { can } = useAuth();
  const canEdit = can("editor");
  const [sessions, setSessions] = useState<ChatSessionDTO[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowDTO[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Turn[]>([]);
  const [feedback, setFeedback] = useState<Record<string, ChatFeedbackRating>>({});
  const [promoted, setPromoted] = useState<Record<string, boolean>>({});
  const [variables, setVariables] = useState<Record<string, unknown>>({});
  const [showVariables, setShowVariables] = useState(false);
  const [input, setInput] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingText, setEditingText] = useState("");
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showWorkflowPicker, setShowWorkflowPicker] = useState(false);
  const abortRef = useRef<(() => void) | null>(null);
  const executionRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const reloadSessions = useCallback(async () => {
    setLoadingSessions(true);
    try {
      const [sessionsPage, workflowsPage] = await Promise.all([
        listChatSessions(),
        listWorkflows({ limit: 200 }),
      ]);
      setSessions(sessionsPage.items);
      setWorkflows(workflowsPage.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载会话失败");
    } finally {
      setLoadingSessions(false);
    }
  }, []);

  useEffect(() => {
    void reloadSessions();
  }, [reloadSessions]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  const loadVariables = useCallback(async (sessionId: string) => {
    try {
      const snapshot = await listChatVariables(sessionId);
      setVariables(snapshot.variables);
    } catch {
      setVariables({});
    }
  }, []);

  const loadMessages = useCallback(async (sessionId: string) => {
    setLoadingMessages(true);
    try {
      const msgs = await listChatMessages(sessionId, { limit: 200, order: "asc" });
      const turns = msgs.items.map((m: ChatMessageDTO) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        citations: m.citations,
      }));
      setMessages(turns);
      // The list response carries the durable feedback rating per message.
      const ratings: Record<string, ChatFeedbackRating> = {};
      for (const m of msgs.items) {
        if (m.role === "assistant" && m.feedback_rating) {
          ratings[m.id] = m.feedback_rating;
        }
      }
      setFeedback(ratings);
    } catch {
      setMessages([]);
    } finally {
      setLoadingMessages(false);
    }
  }, []);

  useEffect(() => {
    if (activeId) {
      void loadMessages(activeId);
      void loadVariables(activeId);
    } else {
      setMessages([]);
      setVariables({});
      setFeedback({});
    }
  }, [activeId, loadMessages, loadVariables]);

  const handleNewSession = async (workflowId: string) => {
    const wf = workflows.find((w) => w.id === workflowId);
    try {
      const session = await createChatSession({
        title: wf ? wf.name : "新对话",
        workflow_id: workflowId,
      });
      setShowWorkflowPicker(false);
      await reloadSessions();
      setActiveId(session.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建会话失败");
    }
  };

  const handleDeleteSession = async (id: string) => {
    if (!window.confirm("删除该会话及其消息？")) return;
    try {
      await deleteChatSession(id);
      if (activeId === id) setActiveId(null);
      await reloadSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    }
  };

  const runStream = (start: (controls: StreamControls) => () => void) => {
    setSending(true);
    abortRef.current = start({
      setExecution: (id) => {
        executionRef.current = id;
      },
    });
  };

  const finishStream = (err?: Error) => {
    if (err) setError(err.message);
    setSending(false);
    abortRef.current = null;
    executionRef.current = null;
    if (activeId) void loadMessages(activeId);
  };

  const appendAssistantStreaming = () => {
    setMessages((prev) => [...prev, { role: "assistant", content: "", streaming: true }]);
  };

  const onDelta = (delta: string) => {
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last && last.role === "assistant") {
        next[next.length - 1] = { ...last, content: last.content + delta };
      }
      return next;
    });
  };

  const handleSend = () => {
    if (!input.trim() || !activeId || sending) return;
    const text = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    appendAssistantStreaming();
    runStream(({ setExecution }) =>
      sendChatMessage(activeId, text, {
        onExecutionId: setExecution,
        onDelta,
        onEvent: () => undefined,
        onDone: () => finishStream(),
        onError: finishStream,
      }),
    );
  };

  const handleRegenerate = (messageId: string) => {
    if (!activeId || sending) return;
    // Drop the target assistant reply; the stream appends the new one and a
    // full reload resyncs the truncation from the server.
    setMessages((prev) => {
      const index = prev.findIndex((m) => m.id === messageId);
      return index === -1 ? prev : prev.slice(0, index);
    });
    appendAssistantStreaming();
    runStream(({ setExecution }) =>
      regenerateChatMessage(activeId, messageId, {
        onExecutionId: setExecution,
        onDelta,
        onEvent: () => undefined,
        onDone: () => finishStream(),
        onError: finishStream,
      }),
    );
  };

  const handleEditSubmit = () => {
    if (!activeId || !editingId || !editingText.trim() || sending) return;
    const messageId = editingId;
    const text = editingText.trim();
    setEditingId(null);
    setEditingText("");
    setMessages((prev) => {
      const index = prev.findIndex((m) => m.id === messageId);
      return index === -1 ? prev : prev.slice(0, index);
    });
    setMessages((prev) => [...prev, { role: "user", content: text, id: undefined }]);
    appendAssistantStreaming();
    runStream(({ setExecution }) =>
      editChatMessage(activeId, messageId, text, {
        onExecutionId: setExecution,
        onDelta,
        onEvent: () => undefined,
        onDone: () => finishStream(),
        onError: finishStream,
      }),
    );
  };

  const handleStop = () => {
    const executionId = executionRef.current;
    if (executionId) {
      void cancelExecution(executionId).catch((err) => {
        setError(err instanceof Error ? err.message : "停止执行失败");
      });
    }
    abortRef.current?.();
    abortRef.current = null;
    executionRef.current = null;
    setSending(false);
    if (activeId) void loadMessages(activeId);
  };

  const handleFeedback = async (messageId: string, rating: ChatFeedbackRating) => {
    if (!activeId) return;
    try {
      const current = feedback[messageId];
      if (current === rating) {
        await deleteChatFeedback(activeId, messageId);
        setFeedback((prev) => {
          const nextMap = { ...prev };
          delete nextMap[messageId];
          return nextMap;
        });
      } else {
        await upsertChatFeedback(activeId, messageId, rating);
        setFeedback((prev) => ({ ...prev, [messageId]: rating }));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "反馈失败");
    }
  };

  const handlePromote = async (messageId: string) => {
    if (!activeId) return;
    const name = window.prompt("评测数据集名称（留空则追加到既有数据集不存在时新建默认）", "来自聊天反馈");
    if (name === null) return;
    try {
      const result = await promoteChatMessage(activeId, messageId, {
        dataset_name: name || undefined,
      });
      setPromoted((prev) => ({ ...prev, [messageId]: true }));
      setNotice(`已加入评测数据集（用例 ${result.case_id.slice(0, 12)}…）`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加入评测数据集失败");
    }
  };

  const handleVariableSave = async (name: string, rawValue: string) => {
    if (!activeId || !name.trim()) return;
    let value: unknown = rawValue;
    try {
      value = JSON.parse(rawValue);
    } catch {
      /* keep as plain string */
    }
    try {
      await upsertChatVariable(activeId, name.trim(), value);
      await loadVariables(activeId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存变量失败");
    }
  };

  const handleVariableDelete = async (name: string) => {
    if (!activeId) return;
    try {
      await deleteChatVariable(activeId, name);
      await loadVariables(activeId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除变量失败");
    }
  };

  const active = sessions.find((s) => s.id === activeId) ?? null;
  const lastAssistantId = [...messages].reverse().find((m) => m.role === "assistant")?.id;

  return (
    <div className="ambient-stage flex h-full w-full flex-col bg-void text-ice">
      <header role="presentation" className="glass relative z-20 flex min-h-14 flex-wrap items-center gap-2 border-b border-line px-3 py-2 sm:px-5">
        <span className="flex h-8 w-8 items-center justify-center text-pulse">
          <MessageSquarePlus size={18} />
        </span>
        <div className="min-w-0">
          <h1 className="font-display text-sm font-semibold text-ice sm:text-base">
            AgentCanvas Chat
          </h1>
          <p className="font-mono text-[9px] uppercase text-ghost/50">
            session / memory / streaming / feedback
          </p>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          {active && (
            <>
              <button
                type="button"
                onClick={() => setShowVariables((v) => !v)}
                className={cn(
                  "flex h-8 w-8 items-center justify-center rounded-md transition hover:bg-line",
                  showVariables ? "text-pulse" : "text-ghost hover:text-pulse",
                )}
                title="会话变量"
              >
                <Variable size={14} />
              </button>
              <a
                href={chatExportUrl(active.id, "markdown")}
                className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-volt"
                title="导出 Markdown"
              >
                <Download size={14} />
              </a>
              <a
                href={chatExportUrl(active.id, "json")}
                className="flex h-8 items-center justify-center rounded-md px-1.5 font-mono text-[10px] uppercase text-ghost transition hover:bg-line hover:text-volt"
                title="导出 JSON"
              >
                JSON
              </a>
            </>
          )}
          <button
            type="button"
            onClick={() => void reloadSessions()}
            disabled={loadingSessions}
            className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-pulse disabled:opacity-40"
            title="刷新"
          >
            <RefreshCw size={14} className={loadingSessions ? "animate-spin" : undefined} />
          </button>
          <Link
            to="/settings/models"
            className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-line hover:text-volt"
            title="模型管理"
          >
            <Settings2 size={14} />
          </Link>
          {canEdit && (
            <button
              type="button"
              onClick={() => setShowWorkflowPicker(true)}
              className="flex h-8 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void transition hover:brightness-110"
            >
              <Plus size={13} />
              <span className="hidden sm:inline">新会话</span>
            </button>
          )}
        </div>
      </header>

      {error && (
        <div className="relative z-10 flex min-h-9 items-center gap-2 border-b border-bad/30 bg-bad/10 px-4 text-xs text-bad">
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
      {notice && (
        <div className="relative z-10 flex min-h-9 items-center gap-2 border-b border-volt/30 bg-volt/10 px-4 text-xs text-volt">
          <span className="min-w-0 flex-1 truncate">{notice}</span>
          <button
            type="button"
            onClick={() => setNotice(null)}
            className="h-7 rounded-md px-2 font-mono text-[9px] uppercase hover:bg-volt/10"
          >
            dismiss
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* Sessions sidebar */}
        <aside className="glass hidden w-64 shrink-0 flex-col border-r border-line sm:flex">
          <div className="px-4 py-3">
            <span className="font-mono text-[9px] uppercase tracking-[0.25em] text-ghost/60">
              Sessions / {sessions.length}
            </span>
          </div>
          <div className="flex-1 overflow-auto px-2 pb-3">
            {loadingSessions && sessions.length === 0 ? (
              <div className="flex items-center gap-2 px-2 text-ghost/60">
                <Loader2 size={12} className="animate-spin" />
                <span className="text-[11px]">加载中…</span>
              </div>
            ) : (
              sessions.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setActiveId(s.id)}
                  className={cn(
                    "group mb-1 flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-xs transition",
                    s.id === activeId
                      ? "bg-pulse/10 text-pulse"
                      : "text-ghost hover:bg-line/50 hover:text-ice",
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">{s.title}</span>
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleDeleteSession(s.id);
                    }}
                    className="hidden text-ghost/40 hover:text-bad group-hover:block"
                  >
                    <Trash2 size={12} />
                  </span>
                </button>
              ))
            )}
            {sessions.length === 0 && !loadingSessions && (
              <p className="px-3 text-[11px] text-ghost/50">暂无会话，点击「新会话」开始。</p>
            )}
          </div>
        </aside>

        {/* Chat panel */}
        <main className="flex min-w-0 flex-1 flex-col">
          {!active ? (
            <div className="flex flex-1 items-center justify-center text-center">
              <div>
                <Bot size={40} className="mx-auto mb-3 text-ghost/30" />
                <p className="text-sm text-ghost/60">选择左侧会话，或点击「新会话」开始对话。</p>
              </div>
            </div>
          ) : (
            <>
              <div className="border-b border-line px-4 py-2">
                <span className="font-mono text-[10px] uppercase tracking-widest text-ghost/60">
                  {active.title} · {active.workflow_id?.slice(0, 8) ?? "no-workflow"}
                </span>
              </div>
              <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto px-4 py-4">
                <div className="mx-auto max-w-3xl space-y-4">
                  {loadingMessages ? (
                    <div className="flex items-center gap-2 text-ghost/60">
                      <Loader2 size={14} className="animate-spin" />
                      <span className="text-xs">加载消息…</span>
                    </div>
                  ) : (
                    messages.map((turn, i) => {
                      const isUser = turn.role === "user";
                      const mid = turn.id;
                      const rating = mid ? feedback[mid] : undefined;
                      return (
                        <div key={mid ?? `stream-${i}`} className="space-y-1">
                          {editingId === mid ? (
                            <div className="flex flex-col gap-2 rounded-lg border border-pulse/40 bg-pulse/5 p-3">
                              <textarea
                                value={editingText}
                                onChange={(e) => setEditingText(e.target.value)}
                                rows={2}
                                className="field-input text-[13px]"
                                autoFocus
                              />
                              <div className="flex justify-end gap-2">
                                <button
                                  type="button"
                                  onClick={() => setEditingId(null)}
                                  className="rounded-md px-2 py-1 text-[11px] text-ghost hover:bg-line"
                                >
                                  取消
                                </button>
                                <button
                                  type="button"
                                  onClick={handleEditSubmit}
                                  disabled={!editingText.trim()}
                                  className="rounded-md bg-pulse px-2.5 py-1 text-[11px] font-semibold text-void disabled:opacity-40"
                                >
                                  重新发送
                                </button>
                              </div>
                            </div>
                          ) : (
                            <TurnBubble turn={turn} />
                          )}
                          {!turn.streaming && mid && isUser && canEdit && !sending && (
                            <div className="flex justify-end">
                              <button
                                type="button"
                                onClick={() => {
                                  setEditingId(mid);
                                  setEditingText(turn.content);
                                }}
                                className="flex items-center gap-1 rounded-md px-1.5 py-0.5 font-mono text-[9px] uppercase text-ghost/50 transition hover:bg-line hover:text-pulse"
                                title="编辑并重新发送"
                              >
                                <Pencil size={10} /> 编辑重发
                              </button>
                            </div>
                          )}
                          {!turn.streaming && mid && !isUser && (
                            <div className="flex items-center gap-1">
                              <button
                                type="button"
                                onClick={() => void handleFeedback(mid, "positive")}
                                className={cn(
                                  "flex h-6 w-6 items-center justify-center rounded-md transition",
                                  rating === "positive"
                                    ? "bg-volt/20 text-volt"
                                    : "text-ghost/40 hover:bg-line hover:text-volt",
                                )}
                                title="有帮助"
                              >
                                <ThumbsUp size={12} />
                              </button>
                              <button
                                type="button"
                                onClick={() => void handleFeedback(mid, "negative")}
                                className={cn(
                                  "flex h-6 w-6 items-center justify-center rounded-md transition",
                                  rating === "negative"
                                    ? "bg-bad/20 text-bad"
                                    : "text-ghost/40 hover:bg-line hover:text-bad",
                                )}
                                title="无帮助"
                              >
                                <ThumbsDown size={12} />
                              </button>
                              {rating === "negative" && canEdit && (
                                <button
                                  type="button"
                                  onClick={() => void handlePromote(mid)}
                                  className="rounded-md px-1.5 py-0.5 font-mono text-[9px] uppercase text-ghost/60 transition hover:bg-line hover:text-volt"
                                  title="把该轮加入评测数据集"
                                >
                                  {promoted[mid] ? "已入评测 ✓" : "加入评测"}
                                </button>
                              )}
                              {mid === lastAssistantId && canEdit && !sending && (
                                <button
                                  type="button"
                                  onClick={() => handleRegenerate(mid)}
                                  className="flex items-center gap-1 rounded-md px-1.5 py-0.5 font-mono text-[9px] uppercase text-ghost/50 transition hover:bg-line hover:text-pulse"
                                  title="重新生成回复"
                                >
                                  <RotateCcw size={10} /> 重新生成
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
              <div className="border-t border-line px-4 py-3">
                <div className="mx-auto flex max-w-3xl items-end gap-2">
                  <textarea
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        handleSend();
                      }
                    }}
                    rows={1}
                    placeholder="输入消息，Enter 发送，Shift+Enter 换行"
                    className="field-input min-h-[40px] max-h-40 flex-1 resize-none"
                    disabled={sending}
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
                      onClick={handleSend}
                      disabled={!input.trim()}
                      className="flex h-10 items-center gap-1.5 rounded-md bg-pulse px-3 text-xs font-semibold text-void transition hover:brightness-110 disabled:opacity-40"
                    >
                      <Send size={14} /> 发送
                    </button>
                  )}
                </div>
              </div>
            </>
          )}
        </main>

        {/* Session variables panel (C3-4) */}
        {showVariables && active && (
          <aside
            className="glass hidden w-72 shrink-0 flex-col border-l border-line md:flex"
            aria-label="会话变量"
          >
            <div className="flex items-center justify-between px-4 py-3">
              <span className="font-mono text-[9px] uppercase tracking-[0.25em] text-ghost/60">
                Session Variables / {Object.keys(variables).length}
              </span>
              <button
                type="button"
                onClick={() => setShowVariables(false)}
                className="flex h-6 w-6 items-center justify-center rounded-md text-ghost/50 hover:bg-line hover:text-ice"
                title="收起"
              >
                <X size={12} />
              </button>
            </div>
            <VariablePanel
              variables={variables}
              canEdit={canEdit}
              onSave={handleVariableSave}
              onDelete={handleVariableDelete}
            />
          </aside>
        )}
      </div>

      {showWorkflowPicker && (
        <WorkflowPicker
          workflows={workflows}
          onClose={() => setShowWorkflowPicker(false)}
          onPick={(id) => void handleNewSession(id)}
        />
      )}
    </div>
  );
}

function VariablePanel({
  variables,
  canEdit,
  onSave,
  onDelete,
}: {
  variables: Record<string, unknown>;
  canEdit: boolean;
  onSave: (name: string, value: string) => void;
  onDelete: (name: string) => void;
}) {
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const names = Object.keys(variables).sort();
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto px-3 pb-3">
      {names.length === 0 && (
        <p className="px-1 text-[11px] text-ghost/50">
          暂无会话变量。工作流 agent 节点配置 session_writes 后会在每轮写入，也可在此手动添加。
        </p>
      )}
      {names.map((key) => (
        <div key={key} className="rounded-md border border-line bg-ink/60 p-2">
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="font-mono text-[11px] text-pulse">{key}</span>
            {canEdit && (
              <button
                type="button"
                onClick={() => onDelete(key)}
                className="text-ghost/40 transition hover:text-bad"
                title="删除变量"
              >
                <Trash2 size={11} />
              </button>
            )}
          </div>
          {canEdit ? (
            <textarea
              defaultValue={JSON.stringify(variables[key], null, 0)}
              onBlur={(e) => onSave(key, e.target.value)}
              rows={Math.min(4, Math.max(1, Math.ceil(JSON.stringify(variables[key]).length / 30)))}
              className="field-input w-full resize-none font-mono text-[10px]"
            />
          ) : (
            <pre className="max-h-24 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] text-ghost/80">
              {JSON.stringify(variables[key], null, 2)}
            </pre>
          )}
        </div>
      ))}
      {canEdit && (
        <div className="rounded-md border border-dashed border-line p-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="变量名（如 summary）"
            className="field-input mb-1.5 w-full font-mono text-[11px]"
          />
          <textarea
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder='JSON 值（如 "要点…" 或 42）'
            rows={2}
            className="field-input mb-1.5 w-full resize-none font-mono text-[10px]"
          />
          <button
            type="button"
            disabled={!name.trim()}
            onClick={() => {
              onSave(name.trim(), value);
              setName("");
              setValue("");
            }}
            className="w-full rounded-md bg-pulse/15 px-2 py-1 text-[11px] font-semibold text-pulse transition hover:bg-pulse/25 disabled:opacity-40"
          >
            添加变量
          </button>
        </div>
      )}
    </div>
  );
}
