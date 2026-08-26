import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MessageSquareText, RefreshCw, X } from "lucide-react";

import { ApiError } from "@/api/client";
import {
  createWorkflowComment,
  createWorkflowReview,
  decideWorkflowReview,
  listAllWorkflowComments,
  listWorkflowReviews,
  setWorkflowCommentResolved,
  type WorkflowCommentDTO,
  type WorkflowReviewDTO,
} from "@/api/endpoints/workflowReviews";
import {
  listWorkflowVersions,
  type WorkflowVersionDTO,
} from "@/api/endpoints/workflows";
import { useDialogFocus } from "@/components/useDialogFocus";
import {
  WorkflowCommentSection,
  WorkflowReviewSection,
  type ReviewDecision,
} from "@/features/workflows/WorkflowReviewSections";
import { cn } from "@/utils/cn";

interface Props {
  workflowId: string | null;
  currentVersion: number;
  canEdit: boolean;
  onNotify: (message: string) => void;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string"
      ? error.detail
      : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

export function WorkflowReviewPanel(props: Props): React.JSX.Element | null {
  const [open, setOpen] = useState(false);
  const [versions, setVersions] = useState<WorkflowVersionDTO[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [comments, setComments] = useState<WorkflowCommentDTO[]>([]);
  const [review, setReview] = useState<WorkflowReviewDTO | null>(null);
  const [commentBody, setCommentBody] = useState("");
  const [replyTo, setReplyTo] = useState<WorkflowCommentDTO | null>(null);
  const [reviewSummary, setReviewSummary] = useState("");
  const [loading, setLoading] = useState(false);
  const [action, setAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const requestGeneration = useRef(0);
  const dialogRef = useDialogFocus<HTMLElement>({
    open,
    onClose: () => setOpen(false),
  });

  const selectedVersion = useMemo(
    () => versions.find((version) => version.id === selectedId) ?? null,
    [selectedId, versions],
  );

  const loadVersions = useCallback(async () => {
    if (!props.workflowId) return;
    const generation = ++requestGeneration.current;
    setLoading(true);
    setError(null);
    try {
      const rows = await listWorkflowVersions(props.workflowId);
      if (requestGeneration.current !== generation) return;
      setVersions(rows);
      setSelectedId((current) => {
        if (rows.some((row) => row.id === current)) return current;
        return (
          rows.find((row) => row.number === props.currentVersion)?.id ??
          rows[0]?.id ??
          ""
        );
      });
    } catch (loadError) {
      if (requestGeneration.current === generation) {
        setError(errorMessage(loadError));
      }
    } finally {
      if (requestGeneration.current === generation) setLoading(false);
    }
  }, [props.currentVersion, props.workflowId]);

  const loadDetails = useCallback(async () => {
    if (!props.workflowId || !selectedId) return;
    const generation = ++requestGeneration.current;
    setLoading(true);
    setError(null);
    try {
      const [loadedComments, reviewPage] = await Promise.all([
        listAllWorkflowComments(props.workflowId, selectedId),
        listWorkflowReviews(props.workflowId, selectedId),
      ]);
      if (requestGeneration.current !== generation) return;
      setComments(loadedComments);
      setReview(reviewPage.items[0] ?? null);
    } catch (loadError) {
      if (requestGeneration.current === generation) {
        setError(errorMessage(loadError));
      }
    } finally {
      if (requestGeneration.current === generation) setLoading(false);
    }
  }, [props.workflowId, selectedId]);

  useEffect(() => {
    requestGeneration.current += 1;
    setVersions([]);
    setSelectedId("");
    setComments([]);
    setReview(null);
    setReplyTo(null);
    setCommentBody("");
  }, [props.workflowId]);

  useEffect(() => {
    if (open) void loadVersions();
    return () => {
      requestGeneration.current += 1;
    };
  }, [loadVersions, open]);

  useEffect(() => {
    setReplyTo(null);
    if (open && selectedId) void loadDetails();
  }, [loadDetails, open, selectedId]);

  const runAction = async (
    key: string,
    operation: () => Promise<unknown>,
    success: string,
  ): Promise<boolean> => {
    setAction(key);
    setError(null);
    try {
      await operation();
      await loadDetails();
      props.onNotify(success);
      return true;
    } catch (operationError) {
      setError(errorMessage(operationError));
      return false;
    } finally {
      setAction(null);
    }
  };

  const addComment = async () => {
    const body = commentBody.trim();
    if (!props.workflowId || !selectedId || !body) return;
    const created = await runAction(
      "comment",
      () =>
        createWorkflowComment(props.workflowId!, {
          version_id: selectedId,
          body,
          ...(replyTo ? { parent_comment_id: replyTo.id } : {}),
        }),
      replyTo ? "回复已添加" : "评论已添加",
    );
    if (created) {
      setCommentBody("");
      setReplyTo(null);
    }
  };

  const requestReview = async () => {
    if (!props.workflowId || !selectedId) return;
    const created = await runAction(
      "request",
      () =>
        createWorkflowReview(
          props.workflowId!,
          selectedId,
          reviewSummary.trim(),
        ),
      "已发起版本审阅",
    );
    if (created) setReviewSummary("");
  };

  const decide = async (decision: ReviewDecision) => {
    if (!props.workflowId || !review) return;
    const decided = await runAction(
      `decision:${decision}`,
      () =>
        decideWorkflowReview(
          props.workflowId!,
          review.id,
          decision,
          reviewSummary.trim(),
        ),
      decision === "approved" ? "审阅已通过" : "审阅状态已更新",
    );
    if (decided) setReviewSummary("");
  };

  if (!props.workflowId) return null;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex h-8 items-center gap-1.5 rounded-md border border-line bg-ink/80 px-2.5 text-xs text-ice transition hover:border-pulse/40 hover:bg-pulse/5 hover:text-pulse"
        title="评论与审阅"
        aria-label="评论与审阅"
      >
        <MessageSquareText size={13} />
        <span className="hidden 2xl:inline">审阅</span>
      </button>

      {open &&
        createPortal(
          <div className="fixed inset-0 z-50 bg-void/75 backdrop-blur-xs">
            <button
              type="button"
              aria-label="关闭评论与审阅"
              className="absolute inset-0 h-full w-full cursor-default"
              onClick={() => setOpen(false)}
            />
            <section
              ref={dialogRef}
              tabIndex={-1}
              role="dialog"
              aria-modal="true"
              aria-labelledby="workflow-review-title"
              className="glass absolute right-0 flex h-full w-[min(520px,100vw)] animate-slide-in flex-col border-l border-line shadow-card"
            >
              <header className="flex min-h-16 items-center gap-3 border-b border-line px-4">
                <MessageSquareText size={18} className="text-pulse" />
                <div className="min-w-0">
                  <h2
                    id="workflow-review-title"
                    className="text-sm font-semibold text-ice"
                  >
                    评论与审阅
                  </h2>
                  <p className="font-mono text-[9px] uppercase text-ghost">
                    immutable version collaboration
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void loadDetails()}
                  disabled={loading || !selectedId}
                  className="ml-auto flex h-8 w-8 items-center justify-center rounded-md text-ghost hover:bg-line hover:text-ice disabled:opacity-40"
                  title="刷新"
                >
                  <RefreshCw size={14} className={cn(loading && "animate-spin")} />
                </button>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="flex h-8 w-8 items-center justify-center rounded-md text-ghost hover:bg-line hover:text-ice"
                  aria-label="关闭"
                >
                  <X size={15} />
                </button>
              </header>

              <div className="border-b border-line px-4 py-3">
                <label
                  className="text-[10px] font-medium uppercase text-ghost"
                  htmlFor="review-version"
                >
                  工作流版本
                </label>
                <select
                  id="review-version"
                  value={selectedId}
                  onChange={(event) => setSelectedId(event.target.value)}
                  className="mt-1 h-9 w-full rounded-md border border-line bg-ink px-3 text-xs text-ice outline-hidden focus:border-pulse/50"
                >
                  {versions.map((version) => (
                    <option key={version.id} value={version.id}>
                      v{version.number} · {version.status} · {version.name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto">
                <WorkflowReviewSection
                  versionNumber={selectedVersion?.number ?? null}
                  review={review}
                  canEdit={props.canEdit}
                  summary={reviewSummary}
                  action={action}
                  onSummaryChange={setReviewSummary}
                  onRequest={() => void requestReview()}
                  onDecide={(decision) => void decide(decision)}
                />
                <WorkflowCommentSection
                  comments={comments}
                  canEdit={props.canEdit}
                  body={commentBody}
                  replyTo={replyTo}
                  action={action}
                  onBodyChange={setCommentBody}
                  onReplyTo={setReplyTo}
                  onSubmit={() => void addComment()}
                  onToggleResolved={(comment) =>
                    void runAction(
                      `resolve:${comment.id}`,
                      () =>
                        setWorkflowCommentResolved(
                          props.workflowId!,
                          comment.id,
                          !comment.resolved_at,
                        ),
                      comment.resolved_at
                        ? "评论已重新打开"
                        : "评论已解决",
                    )
                  }
                />
              </div>

              {error && (
                <p
                  role="alert"
                  className="border-t border-bad/25 bg-bad/5 px-4 py-2 text-[11px] text-bad"
                >
                  {error}
                </p>
              )}
            </section>
          </div>,
          document.body,
        )}
    </>
  );
}
