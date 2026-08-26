import { useCallback, useEffect, useRef, useState } from "react";
import {
  Boxes,
  Code2,
  Copy,
  ExternalLink,
  Loader2,
  Plus,
  Palette,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";

import {
  createApp,
  deleteApp,
  getApp,
  getAppUsage,
  listApps,
  rotateAppToken,
  updateApp,
  type AppCreate,
  type AppDTO,
  type AppIssueOut,
  type AppType,
  type AppUpdate,
  type AppUsageDTO,
  type AppVisibility,
} from "@/api/endpoints/apps";
import { listProjects, type ProjectDTO } from "@/api/endpoints/projectQuotas";
import { useAuth } from "@/features/auth/AuthProvider";

const TYPE_OPTIONS: { value: AppType; label: string }[] = [
  { value: "chatbot", label: "对话机器人" },
  { value: "completion", label: "补全" },
  { value: "api", label: "API" },
];

const VISIBILITY_OPTIONS: { value: AppVisibility; label: string }[] = [
  { value: "project", label: "项目内" },
  { value: "link", label: "链接访问" },
  { value: "public", label: "公开" },
];

function visibilityBadge(app: AppDTO): string {
  if (app.visibility === "public") return "text-pulse";
  if (app.visibility === "link") return "text-volt";
  return "text-ghost";
}

function copyText(value: string) {
  void navigator.clipboard.writeText(value);
}

export function AppsPage() {
  const { ready, can } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [projects, setProjects] = useState<ProjectDTO[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [apps, setApps] = useState<AppDTO[]>([]);
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [embedApp, setEmbedApp] = useState<AppDTO | null>(null);
  const [usageApp, setUsageApp] = useState<AppDTO | null>(null);
  const canEdit = can("editor");

  useEffect(() => {
    if (!ready) return;
    let active = true;
    setLoadingProjects(true);
    void listProjects()
      .then((rows) => {
        if (!active) return;
        setProjects(rows);
        const requested = searchParams.get("project_id");
        const next = rows.some((row) => row.id === requested)
          ? requested!
          : (rows[0]?.id ?? "");
        setSelectedId(next);
        if (next !== requested) {
          setSearchParams(next ? { project_id: next } : {}, { replace: true });
        }
      })
      .catch((cause: unknown) => {
        if (active)
          setError(cause instanceof Error ? cause.message : "加载项目失败");
      })
      .finally(() => {
        if (active) setLoadingProjects(false);
      });
    return () => {
      active = false;
    };
  }, [ready, searchParams, setSearchParams]);

  const loadApps = useCallback(async (projectId: string, targetAppId?: string | null) => {
    if (!projectId) {
      setApps([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const page = await listApps(projectId, { limit: 100 });
      const next = [...page.items];
      if (targetAppId && !next.some((app) => app.id === targetAppId)) {
        const target = await getApp(targetAppId);
        if (target.project_id !== projectId) {
          throw new Error("目标应用不属于当前项目");
        }
        next.unshift(target);
      }
      setApps(next);
    } catch (cause) {
      setApps([]);
      setError(cause instanceof Error ? cause.message : "加载应用失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadApps(selectedId, searchParams.get("app_id"));
  }, [selectedId, loadApps, searchParams]);

  const notify = (message: string) => {
    setToast(message);
    setTimeout(() => setToast(null), 3500);
  };

  const handleCreate = useCallback(
    async (body: AppCreate) => {
      try {
        const issued: AppIssueOut = await createApp(body);
        notify(
          issued.token
            ? `应用已创建;访问令牌:${issued.token.slice(0, 12)}…(前缀)`
            : "应用已创建",
        );
        await loadApps(body.project_id);
      } catch (cause) {
        notify(cause instanceof Error ? cause.message : "创建应用失败");
      }
    },
    [loadApps],
  );

  const handleRotate = useCallback(
    async (app: AppDTO) => {
      setBusyId(app.id);
      try {
        const issued = await rotateAppToken(app.id);
        notify(
          issued.token
            ? `令牌已轮换:${issued.token.slice(0, 12)}…(前缀)`
            : "令牌已轮换",
        );
        await loadApps(app.project_id);
      } catch (cause) {
        notify(cause instanceof Error ? cause.message : "轮换令牌失败");
      } finally {
        setBusyId(null);
      }
    },
    [loadApps],
  );

  const handleDelete = useCallback(
    async (app: AppDTO) => {
      if (!window.confirm(`确认删除应用「${app.name}」?`)) return;
      setBusyId(app.id);
      try {
        await deleteApp(app.id);
        notify("应用已删除");
        await loadApps(app.project_id);
      } catch (cause) {
        notify(cause instanceof Error ? cause.message : "删除应用失败");
      } finally {
        setBusyId(null);
      }
    },
    [loadApps],
  );

  const handleUpdate = useCallback(
    async (app: AppDTO, body: AppUpdate) => {
      setBusyId(app.id);
      try {
        await updateApp(app.id, body);
        notify("应用已更新");
        await loadApps(app.project_id);
      } catch (cause) {
        notify(cause instanceof Error ? cause.message : "更新应用失败");
      } finally {
        setBusyId(null);
      }
    },
    [loadApps],
  );

  return (
    <div className="flex h-full w-full min-w-0 flex-col bg-void text-ice">
      <header role="presentation" className="flex h-14 items-center gap-3 border-b border-line px-4">
        <Boxes size={18} className="text-pulse" />
        <h1 className="text-sm font-semibold">应用发布</h1>
        <span className="ml-auto" />
      </header>

      <div className="flex items-center gap-2 border-b border-line px-4 py-2 text-xs">
        <label htmlFor="project-select" className="text-ghost">
          项目
        </label>
        {loadingProjects ? (
          <Loader2 size={13} className="animate-spin text-ghost" />
        ) : (
          <select
            id="project-select"
            value={selectedId}
            onChange={(event) => {
              const next = event.target.value;
              setSelectedId(next);
              setSearchParams(next ? { project_id: next } : {}, {
                replace: true,
              });
            }}
            className="h-8 rounded-md border border-line bg-ink px-2 text-xs text-ice"
          >
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        )}
        {canEdit && selectedId && (
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="ml-auto flex h-8 items-center gap-1.5 rounded-md border border-pulse/40 bg-pulse/5 px-2.5 text-xs text-pulse transition hover:bg-pulse/10"
          >
            <Plus size={13} />
            <span>新建应用</span>
          </button>
        )}
      </div>

      <main className="flex-1 overflow-auto p-4">
        {error && (
          <p className="mb-3 rounded-md border border-bad/40 bg-bad/5 px-3 py-2 text-xs text-bad">
            {error}
          </p>
        )}
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-ghost">
            <Loader2 size={13} className="animate-spin" />
            <span>加载应用…</span>
          </div>
        ) : apps.length === 0 ? (
          <p className="text-xs text-ghost">
            该项目暂无应用。{canEdit ? "点击「新建应用」发布一个工作流版本。" : ""}
          </p>
        ) : (
          <ul className="space-y-2">
            {apps.map((app) => (
              <AppRow
                key={app.id}
                app={app}
                focused={searchParams.get("app_id") === app.id}
                canEdit={canEdit}
                busy={busyId === app.id}
                onRotate={handleRotate}
                onDelete={handleDelete}
                onEmbed={() => setEmbedApp(app)}
                onUsage={() => setUsageApp(app)}
              />
            ))}
          </ul>
        )}
      </main>

      {toast && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 rounded-md border border-line bg-ink px-3 py-2 text-xs text-ice shadow-lg">
          {toast}
        </div>
      )}

      {creating && selectedId && (
        <CreateAppDialog
          projectId={selectedId}
          onClose={() => setCreating(false)}
          onCreate={handleCreate}
        />
      )}

      {embedApp && (
        <EmbedConfigDialog
          app={embedApp}
          onClose={() => setEmbedApp(null)}
          onSave={handleUpdate}
        />
      )}

      {usageApp && (
        <UsageDialog app={usageApp} onClose={() => setUsageApp(null)} />
      )}
    </div>
  );
}

interface AppRowProps {
  app: AppDTO;
  focused: boolean;
  canEdit: boolean;
  busy: boolean;
  onRotate: (app: AppDTO) => void;
  onDelete: (app: AppDTO) => void;
  onEmbed: () => void;
  onUsage: () => void;
}

function AppRow({
  app,
  focused,
  canEdit,
  busy,
  onRotate,
  onDelete,
  onEmbed,
  onUsage,
}: AppRowProps) {
  const rowRef = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (!focused) return;
    const frame = window.requestAnimationFrame(() => {
      rowRef.current?.scrollIntoView({ block: "center" });
      rowRef.current?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [focused]);

  const typeLabel =
    TYPE_OPTIONS.find((option) => option.value === app.type)?.label ?? app.type;
  const visibilityLabel =
    VISIBILITY_OPTIONS.find((option) => option.value === app.visibility)?.label ??
    app.visibility;
  return (
    <li
      ref={rowRef}
      tabIndex={-1}
      data-app-id={app.id}
      className={`flex items-center gap-3 rounded-md border bg-ink/60 px-3 py-2.5 outline-hidden transition ${
        focused ? "border-pulse/60 bg-pulse/5" : "border-line"
      }`}
    >
      <div className="flex h-9 w-9 items-center justify-center rounded-md bg-pulse/10 text-pulse">
        <Boxes size={16} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-ice">
            {app.name}
          </span>
          <span className="text-[10px] text-ghost">{typeLabel}</span>
          <span className={`text-[10px] ${visibilityBadge(app)}`}>
            {visibilityLabel}
          </span>
          {app.status === "disabled" && (
            <span className="text-[10px] text-warn">已停用</span>
          )}
        </div>
        <div className="mt-0.5 truncate text-[11px] text-ghost">
          slug:{app.slug}
          {app.published_version_number !== null
            ? ` · v${app.published_version_number}`
            : " · 未绑定版本"}
          {app.has_public_access && app.token_prefix
            ? ` · token:${app.token_prefix}…`
            : ""}
        </div>
      </div>
      {app.has_public_access && app.published_version_id && (
        <Link
          to={`/apps/p/${app.slug}`}
          target="_blank"
          rel="noopener noreferrer"
          title="打开公开运行页"
          className="flex h-8 items-center gap-1 rounded-md border border-line px-2 text-[11px] text-ice transition hover:border-volt/40 hover:text-volt"
        >
          <ExternalLink size={12} />
          <span className="hidden xl:inline">运行页</span>
        </Link>
      )}
      <button
        type="button"
        onClick={onUsage}
        title="应用用量"
        className="flex h-8 items-center gap-1 rounded-md border border-line px-2 text-[11px] text-ice transition hover:border-pulse/40 hover:text-pulse"
      >
        <span aria-hidden>📊</span>
        <span className="hidden xl:inline">用量</span>
      </button>
      {app.has_public_access && (
        <button
          type="button"
          onClick={onEmbed}
          title="嵌入配置"
          className="flex h-8 items-center gap-1 rounded-md border border-line px-2 text-[11px] text-ice transition hover:border-pulse/40 hover:text-pulse"
        >
          <Code2 size={12} />
          <span className="hidden xl:inline">嵌入</span>
        </button>
      )}
      {app.has_public_access && (
        <button
          type="button"
          onClick={() => copyText(app.token_prefix ?? "")}
          title="复制 token 前缀"
          className="flex h-8 items-center gap-1 rounded-md border border-line px-2 text-[11px] text-ice transition hover:border-volt/40 hover:text-volt"
        >
          <Copy size={12} />
          <span className="hidden xl:inline">复制前缀</span>
        </button>
      )}
      {canEdit && app.has_public_access && (
        <button
          type="button"
          disabled={busy}
          onClick={() => onRotate(app)}
          title="轮换访问令牌"
          className="flex h-8 items-center gap-1 rounded-md border border-line px-2 text-[11px] text-ice transition hover:border-volt/40 hover:text-volt disabled:opacity-50"
        >
          <RefreshCw size={12} className={busy ? "animate-spin" : ""} />
          <span className="hidden xl:inline">轮换令牌</span>
        </button>
      )}
      {canEdit && (
        <button
          type="button"
          disabled={busy}
          onClick={() => onDelete(app)}
          title="删除应用"
          className="flex h-8 items-center gap-1 rounded-md border border-line px-2 text-[11px] text-ice transition hover:border-bad/40 hover:text-bad disabled:opacity-50"
        >
          <Trash2 size={12} />
          <span className="hidden xl:inline">删除</span>
        </button>
      )}
    </li>
  );
}

interface CreateAppDialogProps {
  projectId: string;
  onClose: () => void;
  onCreate: (body: AppCreate) => void;
}

function CreateAppDialog({ projectId, onClose, onCreate }: CreateAppDialogProps) {
  const [name, setName] = useState("");
  const [type, setType] = useState<AppType>("chatbot");
  const [visibility, setVisibility] = useState<AppVisibility>("project");
  const [welcome, setWelcome] = useState("");
  const [questions, setQuestions] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    setSubmitting(true);
    const suggested = questions
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
    onCreate({
      project_id: projectId,
      name: name.trim(),
      type,
      visibility,
      welcome_message: welcome.trim() || null,
      suggested_questions: suggested,
    });
    setSubmitting(false);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-void/70">
      <form
        onSubmit={submit}
        className="w-[28rem] max-w-[90vw] rounded-lg border border-line bg-ink p-4 text-ice shadow-2xl"
      >
        <h2 className="text-sm font-semibold">新建应用</h2>
        <p className="mt-1 text-[11px] text-ghost">
          应用绑定一个已发布的工作流版本,以对话或 API 形式对外提供服务。
        </p>
        <label className="mt-3 block text-[11px] text-ghost">名称</label>
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={120}
          placeholder="例如 客服助手"
          className="mt-1 h-9 w-full rounded-md border border-line bg-void px-2 text-xs"
        />
        <div className="mt-3 flex gap-3">
          <div className="flex-1">
            <label className="block text-[11px] text-ghost">类型</label>
            <select
              value={type}
              onChange={(event) => setType(event.target.value as AppType)}
              className="mt-1 h-9 w-full rounded-md border border-line bg-void px-2 text-xs"
            >
              {TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex-1">
            <label className="block text-[11px] text-ghost">可见性</label>
            <select
              value={visibility}
              onChange={(event) =>
                setVisibility(event.target.value as AppVisibility)
              }
              className="mt-1 h-9 w-full rounded-md border border-line bg-void px-2 text-xs"
            >
              {VISIBILITY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <label className="mt-3 block text-[11px] text-ghost">欢迎语(可选)</label>
        <textarea
          value={welcome}
          onChange={(event) => setWelcome(event.target.value)}
          maxLength={2000}
          rows={2}
          className="mt-1 w-full rounded-md border border-line bg-void px-2 py-1 text-xs"
        />
        <label className="mt-3 block text-[11px] text-ghost">
          推荐问题(每行一条,最多 10 条)
        </label>
        <textarea
          value={questions}
          onChange={(event) => setQuestions(event.target.value)}
          rows={3}
          className="mt-1 w-full rounded-md border border-line bg-void px-2 py-1 text-xs"
        />
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="h-9 rounded-md border border-line px-3 text-xs text-ice transition hover:bg-void"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={submitting || !name.trim()}
            className="h-9 rounded-md border border-pulse/40 bg-pulse/10 px-3 text-xs text-pulse transition hover:bg-pulse/20 disabled:opacity-50"
          >
            创建
          </button>
        </div>
      </form>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Embed configuration dialog (C3-3)                                   */
/* ------------------------------------------------------------------ */

interface EmbedConfigDialogProps {
  app: AppDTO;
  onClose: () => void;
  onSave: (app: AppDTO, body: AppUpdate) => void;
}

function EmbedConfigDialog({ app, onClose, onSave }: EmbedConfigDialogProps) {
  const [themeColor, setThemeColor] = useState(app.theme_color ?? "");
  const [originsText, setOriginsText] = useState(
    (app.embed_allowed_origins ?? []).join("\n"),
  );
  const [saved, setSaved] = useState(false);

  const runtimeUrl = (() => {
    const base = `${window.location.origin}/apps/p/${app.slug}`;
    return app.visibility === "link"
      ? `${base}?t=TOKEN&embed=1`
      : `${base}?embed=1`;
  })();

  const iframeCode = `<iframe
  src="${runtimeUrl}"
  width="400"
  height="600"
  frameborder="0"
  allow="clipboard-write"
  style="border:none;border-radius:8px;max-width:100%;"
></iframe>`;

  const bubbleCode = `<script
  src="${window.location.origin}/api/apps/p/${app.slug}/embed.js"
${app.visibility === "link" ? '  data-token="TOKEN"\n' : ""}  async
></script>`;

  const handleSave = () => {
    const origins = originsText
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
    onSave(app, {
      theme_color: themeColor || null,
      embed_allowed_origins: origins,
    });
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-void/70">
      <div className="w-[32rem] max-w-[90vw] rounded-lg border border-line bg-ink p-4 text-ice shadow-2xl">
        <h2 className="text-sm font-semibold">嵌入配置</h2>
        <p className="mt-1 text-[11px] text-ghost">
          将此应用嵌入为 iframe。添加允许嵌入的域名（每行一个），浏览器将拒绝未列出的来源加载此页面。
        </p>

        <label className="mt-3 flex items-center gap-2 text-[11px] text-ghost">
          <Palette size={12} /> 主题色（可选，#rrggbb）
        </label>
        <div className="mt-1 flex items-center gap-2">
          <input
            type="color"
            value={themeColor || "#3b82f6"}
            onChange={(e) => setThemeColor(e.target.value)}
            className="h-9 w-12 cursor-pointer rounded-md border border-line bg-void"
          />
          <input
            type="text"
            value={themeColor}
            onChange={(e) => setThemeColor(e.target.value)}
            placeholder="#3b82f6"
            maxLength={9}
            className="field-input h-9 flex-1 font-mono text-xs"
          />
          {themeColor && (
            <button
              type="button"
              onClick={() => setThemeColor("")}
              className="h-9 rounded-md border border-line px-2 text-[11px] text-ghost hover:text-ice"
            >
              清除
            </button>
          )}
        </div>

        <label className="mt-3 block text-[11px] text-ghost">
          允许嵌入的域名（每行一个，http/https + 主机[:端口]，无路径）
        </label>
        <textarea
          value={originsText}
          onChange={(e) => setOriginsText(e.target.value)}
          rows={4}
          placeholder={"https://example.com\nhttps://app.example.com:8443"}
          className="mt-1 w-full rounded-md border border-line bg-void px-2 py-1 font-mono text-xs"
        />

        <label className="mt-4 block text-[11px] text-ghost">iframe 嵌入代码</label>
        <pre className="mt-1 max-h-32 overflow-auto rounded-md border border-line bg-void p-2 text-[10px] leading-relaxed text-ghost/80">
          <code>{iframeCode}</code>
        </pre>
        <button
          type="button"
          onClick={() => copyText(iframeCode)}
          className="mt-2 flex h-8 items-center gap-1.5 rounded-md border border-line px-2 text-[11px] text-ice transition hover:border-volt/40 hover:text-volt"
        >
          <Copy size={12} /> 复制 iframe 代码
        </button>

        <label className="mt-4 block text-[11px] text-ghost">浮动气泡 script 标签</label>
        <p className="mt-1 text-[10px] text-ghost/60">
          在页面底部注入一个可点击的聊天气泡，点击后弹出对话窗口；支持 data-color（气泡颜色）、data-title（提示文案）、data-position="left"（靠左）。
        </p>
        <pre className="mt-1 max-h-32 overflow-auto rounded-md border border-line bg-void p-2 text-[10px] leading-relaxed text-ghost/80">
          <code>{bubbleCode}</code>
        </pre>
        <button
          type="button"
          onClick={() => copyText(bubbleCode)}
          className="mt-2 flex h-8 items-center gap-1.5 rounded-md border border-line px-2 text-[11px] text-ice transition hover:border-volt/40 hover:text-volt"
        >
          <Copy size={12} /> 复制气泡代码
        </button>
        <p className="mt-1 text-[10px] text-ghost/60">
          {app.visibility === "link"
            ? "将 TOKEN 替换为实际的访问令牌。"
            : "公开应用无需令牌。"}
        </p>

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="h-9 rounded-md border border-line px-3 text-xs text-ice transition hover:bg-void"
          >
            关闭
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saved}
            className="h-9 rounded-md border border-pulse/40 bg-pulse/10 px-3 text-xs text-pulse transition hover:bg-pulse/20 disabled:opacity-50"
          >
            {saved ? "已保存" : "保存配置"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Usage dialog (C3-5)                                                 */
/* ------------------------------------------------------------------ */

interface UsageDialogProps {
  app: AppDTO;
  onClose: () => void;
}

const USAGE_WINDOWS = [7, 30, 90] as const;

function formatUsd(value: string | null, known: boolean): string {
  if (!known || value === null) return "未知(缺少用量或定价)";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return value;
  return `$${parsed.toFixed(6)}`;
}

function UsageDialog({ app, onClose }: UsageDialogProps) {
  const [days, setDays] = useState<number>(30);
  const [usage, setUsage] = useState<AppUsageDTO | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    getAppUsage(app.id, days)
      .then((body) => {
        if (active) setUsage(body);
      })
      .catch((cause: unknown) => {
        if (active)
          setError(cause instanceof Error ? cause.message : "加载用量失败");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [app.id, days]);

  const feedbackLabel =
    usage === null
      ? "—"
      : usage.feedback_rate === null
        ? "暂无反馈"
        : `${(usage.feedback_rate * 100).toFixed(1)}%(👍${usage.positive_feedback}/👎${usage.negative_feedback})`;
  const citationCoverageLabel =
    usage === null
      ? "—"
      : usage.citation_coverage === null
        ? "暂无引用"
        : `${(usage.citation_coverage * 100).toFixed(1)}%`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-void/70">
      <div className="w-[36rem] max-w-[92vw] rounded-lg border border-line bg-ink p-4 text-ice shadow-2xl">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">应用用量 · {app.name}</h2>
          <button
            type="button"
            onClick={onClose}
            className="h-8 rounded-md border border-line px-2 text-xs text-ghost transition hover:text-ice"
          >
            关闭
          </button>
        </div>

        <div className="mt-2 flex items-center gap-2 text-[11px] text-ghost">
          <span>统计窗口</span>
          {USAGE_WINDOWS.map((window) => (
            <button
              key={window}
              type="button"
              onClick={() => setDays(window)}
              className={`h-7 rounded-md border px-2 transition ${
                days === window
                  ? "border-pulse/50 bg-pulse/10 text-pulse"
                  : "border-line text-ghost hover:text-ice"
              }`}
            >
              近 {window} 天
            </button>
          ))}
        </div>

        {loading ? (
          <div className="flex items-center gap-2 py-6 text-xs text-ghost">
            <Loader2 size={13} className="animate-spin" /> 加载用量…
          </div>
        ) : error ? (
          <p className="mt-3 rounded-md border border-bad/40 bg-bad/5 px-3 py-2 text-xs text-bad">
            {error}
          </p>
        ) : usage ? (
          <>
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
              <UsageMetric label="会话数" value={usage.sessions} />
              <UsageMetric
                label="消息数"
                value={usage.user_messages + usage.assistant_messages}
                hint={`用户 ${usage.user_messages} / 助手 ${usage.assistant_messages}`}
              />
              <UsageMetric label="执行数" value={usage.executions} />
              <UsageMetric label="总 Tokens" value={usage.total_tokens} />
              <UsageMetric
                label="预估费用"
                text={formatUsd(usage.estimated_cost_usd, usage.cost_known)}
              />
              <UsageMetric label="好评率" text={feedbackLabel} />
              <UsageMetric
                label="引用覆盖率"
                text={citationCoverageLabel}
                hint={`${usage.referenced_citations} / ${usage.available_citations}`}
              />
            </div>

            <div className="mt-4">
              <h3 className="text-[11px] text-ghost">每日趋势(UTC)</h3>
              {usage.daily.length === 0 ? (
                <p className="mt-1 text-[11px] text-ghost/60">
                  窗口内暂无活动。
                </p>
              ) : (
                <div className="mt-1 max-h-44 overflow-auto rounded-md border border-line">
                  <table className="w-full text-left text-[11px]">
                    <thead className="sticky top-0 bg-void/95 text-ghost">
                      <tr>
                        <th className="px-2 py-1 font-normal">日期</th>
                        <th className="px-2 py-1 font-normal">会话</th>
                        <th className="px-2 py-1 font-normal">消息</th>
                        <th className="px-2 py-1 font-normal">执行</th>
                        <th className="px-2 py-1 font-normal">Tokens</th>
                        <th className="px-2 py-1 font-normal">费用</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...usage.daily].reverse().map((day) => (
                        <tr key={day.date} className="border-t border-line/60">
                          <td className="px-2 py-1 font-mono text-ghost">
                            {day.date}
                          </td>
                          <td className="px-2 py-1">{day.sessions}</td>
                          <td className="px-2 py-1">{day.messages}</td>
                          <td className="px-2 py-1">{day.executions}</td>
                          <td className="px-2 py-1">{day.total_tokens}</td>
                          <td className="px-2 py-1 font-mono text-ghost">
                            {day.estimated_cost_usd === null
                              ? "未知"
                              : `$${Number(day.estimated_cost_usd).toFixed(6)}`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            <p className="mt-2 text-[10px] text-ghost/60">
              费用按 D2 成本治理从执行事件估算;缺少 Provider 用量或模型定价时显示「未知」而不是零。统计自{" "}
              {usage.since.slice(0, 10)} 起。
            </p>
          </>
        ) : null}
      </div>
    </div>
  );
}

function UsageMetric({
  label,
  value,
  text,
  hint,
}: {
  label: string;
  value?: number;
  text?: string;
  hint?: string;
}) {
  return (
    <div className="rounded-md border border-line bg-void/60 px-2.5 py-2">
      <div className="text-[10px] uppercase tracking-wider text-ghost/70">
        {label}
      </div>
      <div className="mt-0.5 text-sm font-semibold text-ice">
        {text ?? (value ?? 0).toLocaleString()}
      </div>
      {hint && <div className="text-[10px] text-ghost/60">{hint}</div>}
    </div>
  );
}
