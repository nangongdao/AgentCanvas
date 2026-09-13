import { useState } from "react";
import { createPortal } from "react-dom";
import { Loader2, Package, X, Upload } from "lucide-react";

import { ApiError } from "@/api/client";
import { publishWorkflow, type PublishMetadata } from "@/api/endpoints/marketplace";
import { cn } from "@/utils/cn";

interface Props {
  workflowId: string;
  workflowName: string;
  onClose: () => void;
  onNotify: (message: string) => void;
  onPublished: () => void;
}

const CATEGORIES = [
  { value: "automation", label: "自动化" },
  { value: "analytics", label: "数据分析" },
  { value: "ai-agent", label: "AI Agent" },
  { value: "data-processing", label: "数据处理" },
  { value: "integration", label: "系统集成" },
  { value: "tools", label: "工具" },
  { value: "other", label: "其他" },
];

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string" ? error.detail : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

export function PublishWorkflowDialog({
  workflowId,
  workflowName,
  onClose,
  onNotify,
  onPublished,
}: Props) {
  const [displayName, setDisplayName] = useState(workflowName);
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("automation");
  const [tags, setTags] = useState("");
  const [iconUrl, setIconUrl] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [changelog, setChangelog] = useState("");
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPublishing(true);
    setError(null);

    try {
      const metadata: PublishMetadata = {
        display_name: displayName.trim(),
        description: description.trim(),
        category,
        tags: tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
        version: version.trim(),
        changelog: changelog.trim() || undefined,
        icon_url: iconUrl.trim() || undefined,
        dependencies: {},
      };

      await publishWorkflow(workflowId, metadata);
      onNotify("工作流已发布到市场");
      onPublished();
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPublishing(false);
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-void/80 p-4 backdrop-blur-xs">
      <button
        type="button"
        aria-label="关闭"
        className="absolute inset-0 h-full w-full cursor-default"
        onClick={onClose}
      />
      <form
        onSubmit={handleSubmit}
        className="glass relative flex w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-line shadow-card"
      >
        {/* Header */}
        <header className="flex shrink-0 items-center justify-between border-b border-line px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-accent/40 bg-accent/10">
              <Package size={18} className="text-accent" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-ice">发布到市场</h2>
              <p className="text-xs text-ghost">将工作流分享到社区</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-md text-ghost transition hover:bg-ink/50 hover:text-ice"
          >
            <X size={16} />
          </button>
        </header>

        {/* Form Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {error && (
            <div className="mb-4 rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
              {error}
            </div>
          )}

          <div className="space-y-4">
            {/* Display Name */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-fog">
                显示名称 <span className="text-warn">*</span>
              </label>
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                required
                maxLength={255}
                placeholder="工作流的展示名称"
                className="h-9 w-full rounded-md border border-line bg-ink/50 px-3 text-sm text-ice placeholder-ghost focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>

            {/* Description */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-fog">
                描述 <span className="text-warn">*</span>
              </label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                required
                rows={4}
                placeholder="详细描述工作流的功能和用途..."
                className="w-full rounded-md border border-line bg-ink/50 px-3 py-2 text-sm text-ice placeholder-ghost focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>

            {/* Category */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-fog">
                分类 <span className="text-warn">*</span>
              </label>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                required
                className="h-9 w-full rounded-md border border-line bg-ink/50 px-3 text-sm text-ice focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              >
                {CATEGORIES.map((cat) => (
                  <option key={cat.value} value={cat.value}>
                    {cat.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Tags */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-fog">
                标签
              </label>
              <input
                type="text"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder="用逗号分隔，例如：AI, 自动化, 数据处理"
                className="h-9 w-full rounded-md border border-line bg-ink/50 px-3 text-sm text-ice placeholder-ghost focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
              <p className="mt-1 text-[10px] text-ghost">用逗号分隔多个标签</p>
            </div>

            {/* Version */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-fog">
                版本号 <span className="text-warn">*</span>
              </label>
              <input
                type="text"
                value={version}
                onChange={(e) => setVersion(e.target.value)}
                required
                pattern="^\d+\.\d+\.\d+$"
                placeholder="1.0.0"
                className="h-9 w-full rounded-md border border-line bg-ink/50 px-3 text-sm text-ice placeholder-ghost focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
              <p className="mt-1 text-[10px] text-ghost">使用语义化版本，例如：1.0.0</p>
            </div>

            {/* Icon URL */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-fog">
                图标 URL（可选）
              </label>
              <input
                type="url"
                value={iconUrl}
                onChange={(e) => setIconUrl(e.target.value)}
                placeholder="https://example.com/icon.png"
                className="h-9 w-full rounded-md border border-line bg-ink/50 px-3 text-sm text-ice placeholder-ghost focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>

            {/* Changelog */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-fog">
                更新日志（可选）
              </label>
              <textarea
                value={changelog}
                onChange={(e) => setChangelog(e.target.value)}
                rows={3}
                placeholder="描述本次发布的变更内容..."
                className="w-full rounded-md border border-line bg-ink/50 px-3 py-2 text-sm text-ice placeholder-ghost focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>
          </div>
        </div>

        {/* Footer */}
        <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-line px-6 py-4">
          <button
            type="button"
            onClick={onClose}
            disabled={publishing}
            className="rounded-md border border-line px-4 py-2 text-sm text-fog transition hover:bg-ink/50 disabled:opacity-50"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={publishing}
            className="flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-void transition hover:bg-accent/90 disabled:opacity-50"
          >
            {publishing ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                发布中...
              </>
            ) : (
              <>
                <Upload size={14} />
                发布到市场
              </>
            )}
          </button>
        </footer>
      </form>
    </div>,
    document.body,
  );
}
