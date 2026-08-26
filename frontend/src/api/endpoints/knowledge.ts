import { apiGet, apiSend, apiUpload } from "@/api/client";
import type { ApiContractSchema } from "@/api/contracts";
import {
  type PageQuery,
  type PageResult,
  withQuery,
} from "@/api/pagination";

export interface KnowledgeBaseDTO {
  id: string;
  project_id?: string | null;
  name: string;
  description: string;
  embedding_model_id: string;
  chunk_size: number;
  chunk_overlap: number;
  retrieval_mode: "vector" | "hybrid";
  rerank_enabled: boolean;
  rerank_model_id?: string | null;
  split_strategy: "window" | "recursive" | "heading";
  parent_chunk: boolean;
  document_count: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface KnowledgeBaseInput {
  name: string;
  description?: string;
  embedding_model_id?: string;
  chunk_size?: number;
  chunk_overlap?: number;
  retrieval_mode?: "vector" | "hybrid";
  rerank_enabled?: boolean;
  rerank_model_id?: string | null;
  split_strategy?: "window" | "recursive" | "heading";
  parent_chunk?: boolean;
}

export interface KnowledgeBaseUpdate {
  name?: string;
  description?: string;
  embedding_model_id?: string;
  chunk_size?: number;
  chunk_overlap?: number;
  retrieval_mode?: "vector" | "hybrid";
  rerank_enabled?: boolean;
  rerank_model_id?: string | null;
  split_strategy?: "window" | "recursive" | "heading";
  parent_chunk?: boolean;
}

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export interface KnowledgeDocumentDTO {
  id: string;
  kb_id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  content_sha256: string;
  status: DocumentStatus;
  chunk_count: number;
  error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface IngestResultDTO {
  document: KnowledgeDocumentDTO;
  job_id?: string | null;
  job_status?: "queued" | "running" | "succeeded" | "failed" | "cancelled" | null;
  cache_hits: number;
  cache_misses: number;
}

export type OnlineSourceDTO = ApiContractSchema<"OnlineSourceOut">;
export type OnlineSourceInput = ApiContractSchema<"OnlineSourceCreate">;
export type OnlineSourceUpdate = ApiContractSchema<"OnlineSourceUpdate">;
export type OnlineSourceStatus = OnlineSourceDTO["status"];

export interface RetrievalHitDTO {
  id: string;
  document_id: string;
  filename: string;
  chunk_index: number;
  page?: number | null;
  text: string;
  score: number;
  citation: string;
}

export interface RetrievalScoreDetailDTO {
  hit_id: string;
  retrieval_mode: string;
  vector_score?: number | null;
  keyword_score?: number | null;
  fused_score?: number | null;
  rerank_score?: number | null;
  rerank_applied: boolean;
}

export interface RetrievalResultDTO {
  query: string;
  hits: RetrievalHitDTO[];
  retrieval_mode: string;
  rerank_applied: boolean;
  score_detail?: RetrievalScoreDetailDTO[] | null;
}

const basePath = "/api/knowledge-bases";

export function listKnowledgeBases(query: PageQuery = {}) {
  return apiGet<PageResult<KnowledgeBaseDTO>>(withQuery(basePath, query));
}

export function getKnowledgeBase(id: string) {
  return apiGet<KnowledgeBaseDTO>(`${basePath}/${id}`);
}

export function createKnowledgeBase(body: KnowledgeBaseInput) {
  return apiSend<KnowledgeBaseDTO>(basePath, "POST", body);
}

export function updateKnowledgeBase(id: string, body: KnowledgeBaseUpdate) {
  return apiSend<KnowledgeBaseDTO>(`${basePath}/${id}`, "PUT", body);
}

export function deleteKnowledgeBase(id: string) {
  return apiSend<void>(`${basePath}/${id}`, "DELETE");
}

export function listKnowledgeDocuments(kbId: string, query: PageQuery = {}) {
  return apiGet<PageResult<KnowledgeDocumentDTO>>(
    withQuery(`${basePath}/${kbId}/documents`, query),
  );
}

export function getKnowledgeDocument(kbId: string, documentId: string) {
  return apiGet<KnowledgeDocumentDTO>(`${basePath}/${kbId}/documents/${documentId}`);
}

export function uploadKnowledgeDocument(kbId: string, file: File) {
  const form = new FormData();
  form.set("file", file);
  return apiUpload<KnowledgeDocumentDTO>(`${basePath}/${kbId}/documents`, form);
}

export function ingestKnowledgeDocument(kbId: string, documentId: string) {
  return apiSend<IngestResultDTO>(
    `${basePath}/${kbId}/documents/${documentId}/ingest`,
    "POST",
  );
}

export function getIngestJob(kbId: string, documentId: string) {
  return apiGet<IngestResultDTO>(
    `${basePath}/${kbId}/documents/${documentId}/ingest`,
  );
}

export function deleteKnowledgeDocument(kbId: string, documentId: string) {
  return apiSend<void>(`${basePath}/${kbId}/documents/${documentId}`, "DELETE");
}

export function listOnlineSources(kbId: string) {
  return apiGet<OnlineSourceDTO[]>(`${basePath}/${kbId}/online-sources`);
}

export function createOnlineSource(kbId: string, body: OnlineSourceInput) {
  return apiSend<OnlineSourceDTO>(`${basePath}/${kbId}/online-sources`, "POST", body);
}

export function updateOnlineSource(
  kbId: string,
  sourceId: string,
  body: OnlineSourceUpdate,
) {
  return apiSend<OnlineSourceDTO>(
    `${basePath}/${kbId}/online-sources/${sourceId}`,
    "PUT",
    body,
  );
}

export function deleteOnlineSource(kbId: string, sourceId: string) {
  return apiSend<void>(`${basePath}/${kbId}/online-sources/${sourceId}`, "DELETE");
}

export function syncOnlineSource(kbId: string, sourceId: string) {
  return apiSend<OnlineSourceDTO>(
    `${basePath}/${kbId}/online-sources/${sourceId}/sync`,
    "POST",
  );
}

export function retrieveKnowledge(
  kbId: string,
  body: {
    query: string;
    top_k?: number;
    score_threshold?: number;
    include_scores?: boolean;
  },
) {
  return apiSend<RetrievalResultDTO>(`${basePath}/${kbId}/retrieve`, "POST", body);
}
