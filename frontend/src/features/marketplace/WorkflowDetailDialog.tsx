import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import {
  Download,
  Loader2,
  Star,
  Calendar,
  User,
  Package,
  X,
  MessageSquare,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  getMarketplaceWorkflow,
  installWorkflow,
  listReviews,
  createOrUpdateReview,
  type MarketplaceWorkflowDTO,
  type ReviewDTO,
} from "@/api/endpoints/marketplace";
import { cn } from "@/utils/cn";

interface Props {
  workflowId: string;
  onClose: () => void;
  onNotify: (message: string) => void;
  onInstalled: () => void;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string" ? error.detail : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

/** Validate icon URL protocol to prevent XSS via javascript:/data: URLs. */
function getSafeIconUrl(url: string | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return null;
    }
    return url;
  } catch {
    return null;
  }
}

export function WorkflowDetailDialog({ workflowId, onClose, onNotify, onInstalled }: Props) {
  const [workflow, setWorkflow] = useState<MarketplaceWorkflowDTO | null>(null);
  const [reviews, setReviews] = useState<ReviewDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reviewMode, setReviewMode] = useState(false);
  const [reviewRating, setReviewRating] = useState(5);
  const [reviewComment, setReviewComment] = useState("");
  const [submittingReview, setSubmittingReview] = useState(false);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const [wf, rvs] = await Promise.all([
          getMarketplaceWorkflow(workflowId),
          listReviews(workflowId, 1, 10),
        ]);
        setWorkflow(wf);
        setReviews(rvs);
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, [workflowId]);

  const handleInstall = async () => {
    if (!workflow) return;
    setInstalling(true);
    setError(null);
    try {
      const result = await installWorkflow(workflow.id);
      onNotify(result.message);
      onInstalled();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setInstalling(false);
    }
  };

  const handleSubmitReview = async () => {
    if (!workflow) return;
    setSubmittingReview(true);
    setError(null);
    try {
      await createOrUpdateReview(workflow.id, reviewRating, reviewComment.trim() || undefined);
      const updatedReviews = await listReviews(workflow.id, 1, 10);
      setReviews(updatedReviews);
      setReviewMode(false);
      setReviewComment("");
      onNotify("评价已提交");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmittingReview(false);
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
      <div className="glass relative flex h-full max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-lg border border-line shadow-card">
        {/* Header */}
        <header className="flex shrink-0 items-center justify-between border-b border-line px-6 py-4">
          <div className="flex items-center gap-3">
            {getSafeIconUrl(workflow?.icon_url ?? null) ? (
              <img
                src={getSafeIconUrl(workflow?.icon_url ?? null)!}
                alt=""
                className="h-12 w-12 rounded-lg border border-line object-cover"
              />
            ) : (
              <div className="flex h-12 w-12 items-center justify-center rounded-lg border border-line bg-ink/30">
                <Package size={22} className="text-fog" />
              </div>
            )}
            <div>
              <h2 className="text-lg font-semibold text-ice">
                {workflow?.display_name || "加载中..."}
              </h2>
              {workflow && (
                <p className="text-xs text-ghost">
                  by {workflow.author_name} • v{workflow.version}
                </p>
              )}
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

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <div className="flex h-64 items-center justify-center">
              <Loader2 size={24} className="animate-spin text-accent" />
            </div>
          ) : error ? (
            <div className="rounded-lg border border-warn/40 bg-warn/10 p-4 text-sm text-warn">
              {error}
            </div>
          ) : workflow ? (
            <div className="space-y-6">
              {/* Stats */}
              <div className="flex items-center gap-6 text-sm">
                <div className="flex items-center gap-2 text-fog">
                  <Download size={14} />
                  <span>{workflow.downloads} 下载</span>
                </div>
                <div className="flex items-center gap-2 text-accent">
                  <Star size={14} className="fill-current" />
                  <span>
                    {workflow.rating.toFixed(1)} ({workflow.rating_count} 评价)
                  </span>
                </div>
                <div className="flex items-center gap-2 text-ghost">
                  <Calendar size={14} />
                  <span>{new Date(workflow.published_at).toLocaleDateString("zh-CN")}</span>
                </div>
              </div>

              {/* Description */}
              <div>
                <h3 className="mb-2 text-sm font-medium text-ice">描述</h3>
                <p className="text-sm leading-relaxed text-fog">{workflow.description}</p>
              </div>

              {/* Tags */}
              {workflow.tags.length > 0 && (
                <div>
                  <h3 className="mb-2 text-sm font-medium text-ice">标签</h3>
                  <div className="flex flex-wrap gap-2">
                    {workflow.tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded-full border border-line/50 bg-ink/30 px-3 py-1 text-xs text-fog"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Changelog */}
              {workflow.changelog && (
                <div>
                  <h3 className="mb-2 text-sm font-medium text-ice">更新日志</h3>
                  <div className="rounded-lg border border-line bg-ink/30 p-3 text-xs text-fog">
                    {workflow.changelog}
                  </div>
                </div>
              )}

              {/* Reviews Section */}
              <div>
                <div className="mb-3 flex items-center justify-between">
                  <h3 className="text-sm font-medium text-ice">用户评价</h3>
                  {!reviewMode && (
                    <button
                      type="button"
                      onClick={() => setReviewMode(true)}
                      className="flex items-center gap-1.5 rounded-md border border-accent/40 bg-accent/10 px-3 py-1.5 text-xs text-accent transition hover:bg-accent/20"
                    >
                      <MessageSquare size={12} />
                      写评价
                    </button>
                  )}
                </div>

                {/* Review Form */}
                {reviewMode && (
                  <div className="mb-4 rounded-lg border border-line bg-ink/30 p-4">
                    <div className="mb-3">
                      <label className="mb-2 block text-xs font-medium text-fog">评分</label>
                      <div className="flex gap-1">
                        {[1, 2, 3, 4, 5].map((star) => (
                          <button
                            key={star}
                            type="button"
                            onClick={() => setReviewRating(star)}
                            className="transition hover:scale-110"
                          >
                            <Star
                              size={20}
                              className={cn(
                                star <= reviewRating
                                  ? "fill-accent text-accent"
                                  : "text-line",
                              )}
                            />
                          </button>
                        ))}
                      </div>
                    </div>
                    <div className="mb-3">
                      <label className="mb-2 block text-xs font-medium text-fog">
                        评论（可选）
                      </label>
                      <textarea
                        value={reviewComment}
                        onChange={(e) => setReviewComment(e.target.value)}
                        placeholder="分享你的使用体验..."
                        rows={3}
                        className="w-full rounded-md border border-line bg-ink/50 px-3 py-2 text-xs text-ice placeholder-ghost focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
                      />
                    </div>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={handleSubmitReview}
                        disabled={submittingReview}
                        className="flex items-center gap-1.5 rounded-md bg-accent px-4 py-2 text-xs font-medium text-void transition hover:bg-accent/90 disabled:opacity-50"
                      >
                        {submittingReview ? (
                          <Loader2 size={12} className="animate-spin" />
                        ) : (
                          "提交"
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setReviewMode(false);
                          setReviewComment("");
                        }}
                        className="rounded-md border border-line px-4 py-2 text-xs text-fog transition hover:bg-ink/50"
                      >
                        取消
                      </button>
                    </div>
                  </div>
                )}

                {/* Reviews List */}
                {reviews.length === 0 ? (
                  <p className="py-4 text-center text-xs text-ghost">暂无评价</p>
                ) : (
                  <div className="space-y-3">
                    {reviews.map((review) => (
                      <div
                        key={review.id}
                        className="rounded-lg border border-line bg-ink/20 p-3"
                      >
                        <div className="mb-2 flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <User size={12} className="text-ghost" />
                            <span className="text-xs font-medium text-fog">
                              {review.user_name}
                            </span>
                          </div>
                          <div className="flex items-center gap-1">
                            {Array.from({ length: review.rating }).map((_, i) => (
                              <Star
                                key={i}
                                size={11}
                                className="fill-accent text-accent"
                              />
                            ))}
                          </div>
                        </div>
                        {review.comment && (
                          <p className="text-xs leading-relaxed text-fog">{review.comment}</p>
                        )}
                        <p className="mt-2 text-[10px] text-ghost">
                          {new Date(review.created_at).toLocaleDateString("zh-CN")}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ) : null}
        </div>

        {/* Footer */}
        <footer className="flex shrink-0 items-center justify-between border-t border-line px-6 py-4">
          <div className="text-xs text-ghost">
            {workflow && (
              <span>
                分类: <span className="text-fog">{workflow.category}</span>
              </span>
            )}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-line px-4 py-2 text-xs text-fog transition hover:bg-ink/50"
            >
              关闭
            </button>
            <button
              type="button"
              onClick={handleInstall}
              disabled={installing || !workflow}
              className="flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-xs font-medium text-void transition hover:bg-accent/90 disabled:opacity-50"
            >
              {installing ? (
                <>
                  <Loader2 size={12} className="animate-spin" />
                  安装中...
                </>
              ) : (
                <>
                  <Download size={12} />
                  安装工作流
                </>
              )}
            </button>
          </div>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
