import {
  Check,
  CheckCircle2,
  Circle,
  Loader2,
  MessageSquareReply,
  Send,
  ThumbsDown,
  X,
} from "lucide-react";

import type {
  WorkflowCommentDTO,
  WorkflowReviewDTO,
  WorkflowReviewStatus,
} from "@/api/endpoints/workflowReviews";
import { cn } from "@/utils/cn";

export type ReviewDecision =
  | "approved"
  | "changes_requested"
  | "dismissed";

const STATUS_STYLE: Record<WorkflowReviewStatus, string> = {
  open: "border-pulse/35 bg-pulse/10 text-pulse",
  approved: "border-ok/35 bg-ok/10 text-ok",
  changes_requested: "border-warn/35 bg-warn/10 text-warn",
  dismissed: "border-line bg-line/40 text-ghost",
};

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "";
}

interface ReviewSectionProps {
  versionNumber: number | null;
  review: WorkflowReviewDTO | null;
  canEdit: boolean;
  summary: string;
  action: string | null;
  onSummaryChange: (value: string) => void;
  onRequest: () => void;
  onDecide: (decision: ReviewDecision) => void;
}

export function WorkflowReviewSection(
  props: ReviewSectionProps,
): React.JSX.Element {
  const open = props.review?.status === "open";
  return (
    <section className="border-b border-line px-4 py-4">
      <div className="flex items-center gap-2">
        <h3 className="text-xs font-semibold text-ice">变更审阅</h3>
        {props.review && (
          <span
            className={cn(
              "rounded-xs border px-2 py-0.5 font-mono text-[9px] uppercase",
              STATUS_STYLE[props.review.status],
            )}
          >
            {props.review.status}
          </span>
        )}
      </div>
      {props.review ? (
        <div className="mt-3 text-[11px] leading-5 text-ghost">
          <p>{props.review.summary || "未填写审阅说明"}</p>
          <p className="mt-1 font-mono text-[9px] text-ghost/55">
            {props.review.requested_by_subject} · {formatDate(props.review.created_at)}
          </p>
          {props.review.decision_summary && (
            <p className="mt-2 text-ice/75">{props.review.decision_summary}</p>
          )}
        </div>
      ) : (
        <p className="mt-2 text-[11px] text-ghost">
          v{props.versionNumber ?? "-"} 尚无审阅记录
        </p>
      )}

      {props.canEdit && (!props.review || open) && (
        <div className="mt-3">
          <input
            value={props.summary}
            onChange={(event) => props.onSummaryChange(event.target.value)}
            placeholder={props.review ? "决策说明" : "审阅说明"}
            maxLength={4000}
            className="h-9 w-full rounded-md border border-line bg-ink px-3 text-xs text-ice outline-hidden placeholder:text-ghost/40 focus:border-pulse/50"
          />
          <div className="mt-2 flex flex-wrap justify-end gap-2">
            {!props.review ? (
              <button
                type="button"
                onClick={props.onRequest}
                disabled={props.action !== null}
                className="flex h-8 items-center gap-1.5 rounded-md bg-pulse px-3 text-[10px] font-semibold text-void disabled:opacity-40"
              >
                {props.action === "request" ? (
                  <Loader2 size={11} className="animate-spin" />
                ) : (
                  <Send size={11} />
                )}
                发起审阅
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => props.onDecide("dismissed")}
                  disabled={props.action !== null}
                  className="h-8 rounded-md px-3 text-[10px] text-ghost hover:bg-line disabled:opacity-40"
                >
                  关闭审阅
                </button>
                {!props.review.requester_is_current_actor && (
                  <>
                    <button
                      type="button"
                      onClick={() => props.onDecide("changes_requested")}
                      disabled={props.action !== null}
                      className="flex h-8 items-center gap-1.5 rounded-md border border-warn/35 px-3 text-[10px] text-warn disabled:opacity-40"
                    >
                      <ThumbsDown size={11} />需修改
                    </button>
                    <button
                      type="button"
                      onClick={() => props.onDecide("approved")}
                      disabled={props.action !== null}
                      className="flex h-8 items-center gap-1.5 rounded-md bg-ok px-3 text-[10px] font-semibold text-void disabled:opacity-40"
                    >
                      <Check size={11} />通过
                    </button>
                  </>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

interface CommentSectionProps {
  comments: WorkflowCommentDTO[];
  canEdit: boolean;
  body: string;
  replyTo: WorkflowCommentDTO | null;
  action: string | null;
  onBodyChange: (value: string) => void;
  onReplyTo: (comment: WorkflowCommentDTO | null) => void;
  onSubmit: () => void;
  onToggleResolved: (comment: WorkflowCommentDTO) => void;
}

export function WorkflowCommentSection(
  props: CommentSectionProps,
): React.JSX.Element {
  const commentsById = new Map(props.comments.map((comment) => [comment.id, comment]));
  return (
    <section className="px-4 py-4">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-ice">版本评论</h3>
        <span className="font-mono text-[9px] text-ghost/55">{props.comments.length}</span>
      </div>
      {props.replyTo && (
        <div className="mt-3 flex items-center gap-2 border-l-2 border-pulse/50 bg-pulse/5 px-3 py-2 text-[10px] text-ghost">
          <MessageSquareReply size={12} className="shrink-0 text-pulse" />
          <span className="min-w-0 flex-1 truncate">
            回复 {props.replyTo.author_subject}：{props.replyTo.body}
          </span>
          <button
            type="button"
            onClick={() => props.onReplyTo(null)}
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md hover:bg-line hover:text-ice"
            aria-label="取消回复"
          >
            <X size={12} />
          </button>
        </div>
      )}
      <div className="mt-3 flex gap-2">
        <textarea
          value={props.body}
          onChange={(event) => props.onBodyChange(event.target.value)}
          placeholder={props.replyTo ? "回复评论" : "添加评论"}
          maxLength={8000}
          rows={2}
          className="min-h-16 flex-1 resize-y rounded-md border border-line bg-ink px-3 py-2 text-xs text-ice outline-hidden placeholder:text-ghost/40 focus:border-pulse/50"
        />
        <button
          type="button"
          onClick={props.onSubmit}
          disabled={!props.body.trim() || props.action !== null}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-pulse text-void disabled:opacity-40"
          title={props.replyTo ? "发送回复" : "发送评论"}
        >
          {props.action === "comment" ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <Send size={13} />
          )}
        </button>
      </div>

      <ul className="mt-4 divide-y divide-line border-y border-line">
        {props.comments.map((comment) => {
          const parent = comment.parent_comment_id
            ? commentsById.get(comment.parent_comment_id)
            : null;
          return (
            <li
              key={comment.id}
              className={cn(
                "py-3",
                comment.parent_comment_id && "pl-5",
                comment.resolved_at && "opacity-55",
              )}
            >
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 font-mono text-[9px] text-ghost/55">
                    <span className="text-ice/75">{comment.author_subject}</span>
                    <span>{formatDate(comment.created_at)}</span>
                    {parent && <span>回复 {parent.author_subject}</span>}
                    {comment.node_id && <span className="text-pulse">NODE {comment.node_id}</span>}
                  </div>
                  <p className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-5 text-ghost">
                    {comment.body}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    onClick={() => props.onReplyTo(comment)}
                    disabled={props.action !== null}
                    className="flex h-7 w-7 items-center justify-center rounded-md text-ghost hover:bg-line hover:text-pulse disabled:opacity-40"
                    title="回复评论"
                  >
                    <MessageSquareReply size={13} />
                  </button>
                  {props.canEdit && (
                    <button
                      type="button"
                      onClick={() => props.onToggleResolved(comment)}
                      disabled={props.action !== null}
                      className="flex h-7 w-7 items-center justify-center rounded-md text-ghost hover:bg-line hover:text-ok disabled:opacity-40"
                      title={comment.resolved_at ? "重新打开评论" : "解决评论"}
                    >
                      {comment.resolved_at ? (
                        <CheckCircle2 size={13} />
                      ) : (
                        <Circle size={13} />
                      )}
                    </button>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
