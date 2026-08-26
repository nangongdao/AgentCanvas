/** Durable comments and reviews attached to immutable workflow versions. */

import { apiGet, apiSend } from "@/api/client";
import {
  type PageQuery,
  type PageResult,
  withQuery,
} from "@/api/pagination";

export interface WorkflowCommentDTO {
  id: string;
  workflow_id: string;
  version_id: string;
  parent_comment_id: string | null;
  node_id: string | null;
  author_subject: string;
  body: string;
  created_at: string | null;
  resolved_at: string | null;
  resolved_by_subject: string | null;
}

export type WorkflowReviewStatus =
  | "open"
  | "approved"
  | "changes_requested"
  | "dismissed";

export interface WorkflowReviewDTO {
  id: string;
  workflow_id: string;
  version_id: string;
  status: WorkflowReviewStatus;
  summary: string;
  requested_by_subject: string;
  requester_is_current_actor: boolean;
  created_at: string | null;
  decision_summary: string;
  decided_by_subject: string | null;
  decided_at: string | null;
}

function basePath(workflowId: string): string {
  return `/api/workflows/${workflowId}`;
}

export function listWorkflowComments(
  workflowId: string,
  versionId: string,
  query: PageQuery = {},
): Promise<PageResult<WorkflowCommentDTO>> {
  return apiGet<PageResult<WorkflowCommentDTO>>(
    withQuery(`${basePath(workflowId)}/comments`, {
      version_id: versionId,
      order: "asc",
      limit: 200,
      ...query,
    }),
  );
}

export async function listAllWorkflowComments(
  workflowId: string,
  versionId: string,
): Promise<WorkflowCommentDTO[]> {
  const comments: WorkflowCommentDTO[] = [];
  let cursor: string | undefined;
  do {
    const page = await listWorkflowComments(workflowId, versionId, { cursor });
    comments.push(...page.items);
    cursor = page.next_cursor ?? undefined;
  } while (cursor);
  return comments;
}

export function createWorkflowComment(
  workflowId: string,
  body: {
    version_id: string;
    body: string;
    node_id?: string;
    parent_comment_id?: string;
  },
): Promise<WorkflowCommentDTO> {
  return apiSend<WorkflowCommentDTO>(
    `${basePath(workflowId)}/comments`,
    "POST",
    body,
  );
}

export function setWorkflowCommentResolved(
  workflowId: string,
  commentId: string,
  resolved: boolean,
): Promise<WorkflowCommentDTO> {
  return apiSend<WorkflowCommentDTO>(
    `${basePath(workflowId)}/comments/${commentId}/resolution`,
    "PUT",
    { resolved },
  );
}

export function listWorkflowReviews(
  workflowId: string,
  versionId: string,
): Promise<PageResult<WorkflowReviewDTO>> {
  return apiGet<PageResult<WorkflowReviewDTO>>(
    withQuery(`${basePath(workflowId)}/reviews`, {
      version_id: versionId,
      limit: 1,
    }),
  );
}

export function createWorkflowReview(
  workflowId: string,
  versionId: string,
  summary: string,
): Promise<WorkflowReviewDTO> {
  return apiSend<WorkflowReviewDTO>(
    `${basePath(workflowId)}/reviews`,
    "POST",
    { version_id: versionId, summary },
  );
}

export function decideWorkflowReview(
  workflowId: string,
  reviewId: string,
  decision: "approved" | "changes_requested" | "dismissed",
  summary: string,
): Promise<WorkflowReviewDTO> {
  return apiSend<WorkflowReviewDTO>(
    `${basePath(workflowId)}/reviews/${reviewId}/decision`,
    "PUT",
    { decision, summary },
  );
}
