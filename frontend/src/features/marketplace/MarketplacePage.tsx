import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Download,
  Filter,
  Loader2,
  Search,
  Star,
  TrendingUp,
  Clock,
  Package,
  X,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  listMarketplaceWorkflows,
  type MarketplaceWorkflowDTO,
} from "@/api/endpoints/marketplace";
import { cn } from "@/utils/cn";
import { WorkflowDetailDialog } from "./WorkflowDetailDialog";

interface Props {
  onNotify: (message: string) => void;
}

type SortOption = "downloads" | "rating" | "recent";

const SORT_OPTIONS: Array<{ value: SortOption; label: string; icon: typeof TrendingUp }> = [
  { value: "downloads", label: "下载量", icon: TrendingUp },
  { value: "rating", label: "评分", icon: Star },
  { value: "recent", label: "最新", icon: Clock },
];

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string" ? error.detail : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

export function MarketplacePage({ onNotify }: Props) {
  const [workflows, setWorkflows] = useState<MarketplaceWorkflowDTO[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [sortBy, setSortBy] = useState<SortOption>("downloads");
  const [page, setPage] = useState(1);
  const [selectedWorkflow, setSelectedWorkflow] = useState<MarketplaceWorkflowDTO | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listMarketplaceWorkflows({
        category: selectedCategory || undefined,
        tags: selectedTags,
        sortBy,
        page,
        pageSize: 20,
      });
      setWorkflows(result);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [selectedCategory, selectedTags, sortBy, page]);

  useEffect(() => {
    void load();
  }, [load]);

  const availableCategories = useMemo(() => {
    const cats = new Set(workflows.map((w) => w.category));
    return Array.from(cats).sort();
  }, [workflows]);

  const availableTags = useMemo(() => {
    const tags = new Set(workflows.flatMap((w) => w.tags));
    return Array.from(tags).sort();
  }, [workflows]);

  const filteredWorkflows = useMemo(() => {
    if (!searchQuery.trim()) return workflows;
    const q = searchQuery.toLowerCase();
    return workflows.filter(
      (w) =>
        w.display_name.toLowerCase().includes(q) ||
        w.description.toLowerCase().includes(q) ||
        w.author_name.toLowerCase().includes(q),
    );
  }, [workflows, searchQuery]);

  const toggleTag = (tag: string) => {
    setSelectedTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag],
    );
    setPage(1);
  };

  return (
    <div className="flex h-full flex-col bg-void">
      {/* Header */}
      <header className="border-b border-line bg-ink/30 px-6 py-4">
        <div className="flex items-center gap-4">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-accent/40 bg-accent/10">
            <Package size={20} className="text-accent" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-ice">工作流市场</h1>
            <p className="text-xs text-ghost">发现并安装社区工作流</p>
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Sidebar Filter */}
        <aside className="w-64 shrink-0 border-r border-line bg-ink/20 p-4">
          <div className="space-y-6">
            {/* Sort */}
            <div>
              <h3 className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-ghost">
                <Filter size={12} />
                排序方式
              </h3>
              <div className="space-y-1">
                {SORT_OPTIONS.map(({ value, label, icon: Icon }) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => {
                      setSortBy(value);
                      setPage(1);
                    }}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-3 py-2 text-xs transition",
                      sortBy === value
                        ? "bg-accent/20 text-accent"
                        : "text-fog hover:bg-ink/50 hover:text-ice",
                    )}
                  >
                    <Icon size={13} />
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {/* Categories */}
            {availableCategories.length > 0 && (
              <div>
                <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-ghost">
                  分类
                </h3>
                <div className="space-y-1">
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedCategory(null);
                      setPage(1);
                    }}
                    className={cn(
                      "w-full rounded-md px-3 py-1.5 text-left text-xs transition",
                      !selectedCategory
                        ? "bg-accent/20 text-accent"
                        : "text-fog hover:bg-ink/50 hover:text-ice",
                    )}
                  >
                    全部
                  </button>
                  {availableCategories.map((cat) => (
                    <button
                      key={cat}
                      type="button"
                      onClick={() => {
                        setSelectedCategory(cat);
                        setPage(1);
                      }}
                      className={cn(
                        "w-full rounded-md px-3 py-1.5 text-left text-xs transition",
                        selectedCategory === cat
                          ? "bg-accent/20 text-accent"
                          : "text-fog hover:bg-ink/50 hover:text-ice",
                      )}
                    >
                      {cat}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Tags */}
            {availableTags.length > 0 && (
              <div>
                <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-ghost">
                  标签
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {availableTags.map((tag) => (
                    <button
                      key={tag}
                      type="button"
                      onClick={() => toggleTag(tag)}
                      className={cn(
                        "rounded-full border px-2.5 py-1 text-[10px] font-medium transition",
                        selectedTags.includes(tag)
                          ? "border-accent bg-accent/20 text-accent"
                          : "border-line/50 bg-ink/30 text-fog hover:border-accent/50 hover:text-ice",
                      )}
                    >
                      {tag}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </aside>

        {/* Main Content */}
        <main className="flex min-w-0 flex-1 flex-col">
          {/* Search Bar */}
          <div className="border-b border-line bg-ink/10 px-6 py-3">
            <div className="relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ghost" />
              <input
                ref={searchRef}
                type="text"
                placeholder="搜索工作流..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="h-9 w-full rounded-lg border border-line bg-ink/50 pl-9 pr-3 text-xs text-ice placeholder-ghost focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>
          </div>

          {/* Content Area */}
          <div className="flex-1 overflow-y-auto p-6">
            {error && (
              <div className="mb-4 rounded-lg border border-warn/40 bg-warn/10 p-4 text-xs text-warn">
                {error}
              </div>
            )}

            {loading ? (
              <div className="flex h-64 items-center justify-center">
                <Loader2 size={20} className="animate-spin text-accent" />
              </div>
            ) : filteredWorkflows.length === 0 ? (
              <div className="flex h-64 flex-col items-center justify-center text-center">
                <Package size={32} className="mb-3 text-ghost" />
                <p className="text-sm text-fog">暂无工作流</p>
              </div>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {filteredWorkflows.map((workflow) => (
                  <button
                    key={workflow.id}
                    type="button"
                    onClick={() => setSelectedWorkflow(workflow)}
                    className="group flex flex-col gap-3 rounded-lg border border-line bg-ink/40 p-4 text-left transition hover:border-accent/50 hover:bg-ink/60"
                  >
                    <div className="flex items-start gap-3">
                      {workflow.icon_url ? (
                        <img
                          src={workflow.icon_url}
                          alt=""
                          className="h-10 w-10 shrink-0 rounded-lg border border-line object-cover"
                        />
                      ) : (
                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-line bg-ink/30">
                          <Package size={18} className="text-fog" />
                        </div>
                      )}
                      <div className="min-w-0 flex-1">
                        <h3 className="truncate text-sm font-medium text-ice group-hover:text-accent">
                          {workflow.display_name}
                        </h3>
                        <p className="text-[10px] text-ghost">by {workflow.author_name}</p>
                      </div>
                    </div>
                    <p className="line-clamp-2 text-xs text-fog">{workflow.description}</p>
                    <div className="flex items-center gap-4 text-[10px] text-ghost">
                      <span className="flex items-center gap-1">
                        <Download size={11} />
                        {workflow.downloads}
                      </span>
                      <span className="flex items-center gap-1">
                        <Star size={11} className="fill-current text-accent" />
                        {workflow.rating.toFixed(1)} ({workflow.rating_count})
                      </span>
                    </div>
                    {workflow.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {workflow.tags.slice(0, 3).map((tag) => (
                          <span
                            key={tag}
                            className="rounded-full border border-line/50 bg-ink/20 px-2 py-0.5 text-[9px] text-ghost"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        </main>
      </div>

      {selectedWorkflow && (
        <WorkflowDetailDialog
          workflowId={selectedWorkflow.id}
          onClose={() => setSelectedWorkflow(null)}
          onNotify={onNotify}
          onInstalled={() => {
            setSelectedWorkflow(null);
            void load();
          }}
        />
      )}
    </div>
  );
}
