import { apiGet, apiSend } from "../client";
import type { ApiError } from "../client";

export { ApiError };

export interface MarketplaceWorkflowDTO {
  id: string;
  workflow_id: string;
  author_id: string;
  author_name: string;
  display_name: string;
  description: string;
  category: string;
  tags: string[];
  icon_url: string | null;
  version: string;
  changelog: string | null;
  dependencies: Record<string, unknown>;
  downloads: number;
  rating: number;
  rating_count: number;
  status: string;
  published_at: string;
  updated_at: string;
}

export interface ReviewDTO {
  id: string;
  marketplace_workflow_id: string;
  user_id: string;
  user_name: string;
  rating: number;
  comment: string | null;
  created_at: string;
  updated_at: string;
}

export interface PublishMetadata {
  display_name: string;
  description: string;
  category: string;
  tags: string[];
  icon_url?: string;
  version: string;
  changelog?: string;
  dependencies?: Record<string, unknown>;
}

export interface InstallResponse {
  workflow_id: string;
  message: string;
}

export async function listMarketplaceWorkflows(params: {
  category?: string;
  tags?: string[];
  search?: string;
  sortBy?: "downloads" | "rating" | "recent";
  page?: number;
  pageSize?: number;
}): Promise<MarketplaceWorkflowDTO[]> {
  const searchParams = new URLSearchParams();
  if (params.category) searchParams.set("category", params.category);
  if (params.tags && params.tags.length > 0) {
    params.tags.forEach((tag) => searchParams.append("tags", tag));
  }
  if (params.search) searchParams.set("search", params.search);
  if (params.sortBy) searchParams.set("sort_by", params.sortBy);
  if (params.page) searchParams.set("page", params.page.toString());
  if (params.pageSize) searchParams.set("page_size", params.pageSize.toString());

  return apiGet<MarketplaceWorkflowDTO[]>(`/marketplace/workflows?${searchParams.toString()}`);
}

export async function getMarketplaceWorkflow(
  workflowId: string,
): Promise<MarketplaceWorkflowDTO> {
  return apiGet<MarketplaceWorkflowDTO>(`/marketplace/workflows/${workflowId}`);
}

export async function publishWorkflow(
  workflowId: string,
  metadata: PublishMetadata,
): Promise<MarketplaceWorkflowDTO> {
  return apiSend<MarketplaceWorkflowDTO>(`/marketplace/publish?workflow_id=${workflowId}`, "POST", metadata);
}

export async function updatePublishedWorkflow(
  marketplaceId: string,
  metadata: PublishMetadata,
): Promise<MarketplaceWorkflowDTO> {
  return apiSend<MarketplaceWorkflowDTO>(`/marketplace/publish/${marketplaceId}`, "PUT", metadata);
}

export async function installWorkflow(workflowId: string): Promise<InstallResponse> {
  return apiSend<InstallResponse>(`/marketplace/install/${workflowId}`, "POST", {});
}

export async function createOrUpdateReview(
  workflowId: string,
  rating: number,
  comment?: string,
): Promise<ReviewDTO> {
  return apiSend<ReviewDTO>(`/marketplace/workflows/${workflowId}/reviews`, "POST", {
    rating,
    comment,
  });
}

export async function listReviews(
  workflowId: string,
  page = 1,
  pageSize = 10,
): Promise<ReviewDTO[]> {
  return apiGet<ReviewDTO[]>(
    `/marketplace/workflows/${workflowId}/reviews?page=${page}&page_size=${pageSize}`,
  );
}
