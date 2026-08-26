import { apiGet, apiSend } from "@/api/client";
import { type PageQuery, type PageResult, withQuery } from "@/api/pagination";

export type EvaluatorType = "exact" | "contains" | "json_schema" | "llm_judge" | "rag";

export interface EvaluationCaseInput {
  id: string;
  name: string;
  inputs: Record<string, unknown>;
  expected: unknown;
}

export interface EvaluationDatasetVersionDTO {
  id: string;
  dataset_id: string;
  number: number;
  cases: EvaluationCaseInput[];
  change_summary: string;
  created_at?: string;
}

export interface EvaluationDatasetDTO {
  id: string;
  name: string;
  description: string;
  current_version: number;
  case_count: number;
  created_at?: string;
  updated_at?: string;
}

export interface EvaluationDatasetDetailDTO extends EvaluationDatasetDTO {
  versions: EvaluationDatasetVersionDTO[];
}

export interface EvaluationDatasetInput {
  name: string;
  description?: string;
  cases: EvaluationCaseInput[];
  change_summary?: string;
}

export interface EvaluationDatasetUpdateInput extends EvaluationDatasetInput {
  expected_version: number;
}

export interface EvaluationCaseResultDTO {
  id: string;
  case_id: string;
  case_index: number;
  name: string;
  inputs: Record<string, unknown>;
  expected: unknown;
  actual: unknown;
  execution_id?: string | null;
  status: string;
  score?: number | null;
  message: string;
  duration_ms?: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost_usd?: string | null;
  cost_known: boolean;
  cost_error_bound_usd?: string | null;
  price_versions: string[];
  finished_at?: string;
}

export interface EvaluationRunDTO {
  id: string;
  dataset_version_id: string;
  dataset_id?: string | null;
  dataset_name?: string | null;
  dataset_version_number?: number | null;
  workflow_version_id: string;
  workflow_id?: string | null;
  workflow_name?: string | null;
  workflow_version_number?: number | null;
  evaluator_type: EvaluatorType;
  evaluator_config: Record<string, unknown>;
  status: string;
  summary: Record<string, unknown>;
  error?: string | null;
  cases: EvaluationCaseResultDTO[];
  created_at?: string;
  started_at?: string;
  finished_at?: string;
}

export interface EvaluationRunInput {
  dataset_version_id: string;
  workflow_version_id: string;
  evaluator_type: EvaluatorType;
  actual_path?: string;
  case_sensitive?: boolean;
  model_config_id?: string;
  rubric?: string;
  threshold?: number;
  allow_llm_judge?: boolean;
  allow_side_effects?: boolean;
  rag_node_id?: string;
  citation_node_id?: string;
  retrieval_k?: number;
  min_recall_at_k?: number;
  min_mrr?: number;
  min_citation_coverage?: number;
  require_correct_no_answer?: boolean;
}

export interface EvaluationComparisonInput
  extends Omit<EvaluationRunInput, "workflow_version_id"> {
  workflow_version_a_id: string;
  workflow_version_b_id: string;
}

export interface EvaluationComparisonCaseDTO {
  case_id: string;
  name: string;
  inputs: Record<string, unknown>;
  expected: unknown;
  variant_a: EvaluationCaseResultDTO;
  variant_b: EvaluationCaseResultDTO;
  score_delta?: number | null;
  duration_delta_ms?: number | null;
  estimated_cost_delta_usd?: string | null;
}

export interface EvaluationComparisonDTO {
  id: string;
  dataset_version_id: string;
  dataset_id?: string | null;
  dataset_name?: string | null;
  dataset_version_number?: number | null;
  status: string;
  summary: Record<string, unknown>;
  error?: string | null;
  variant_a: EvaluationRunDTO;
  variant_b: EvaluationRunDTO;
  cases: EvaluationComparisonCaseDTO[];
  created_at?: string;
  started_at?: string;
  finished_at?: string;
}

export function listEvaluationDatasets(query: PageQuery = {}) {
  return apiGet<PageResult<EvaluationDatasetDTO>>(
    withQuery("/api/evaluation-datasets", query),
  );
}

export function getEvaluationDataset(id: string) {
  return apiGet<EvaluationDatasetDetailDTO>(`/api/evaluation-datasets/${id}`);
}

export function createEvaluationDataset(body: EvaluationDatasetInput) {
  return apiSend<EvaluationDatasetDetailDTO>("/api/evaluation-datasets", "POST", body);
}

export function updateEvaluationDataset(id: string, body: EvaluationDatasetUpdateInput) {
  return apiSend<EvaluationDatasetDetailDTO>(`/api/evaluation-datasets/${id}`, "PUT", body);
}

export function deleteEvaluationDataset(id: string) {
  return apiSend<void>(`/api/evaluation-datasets/${id}`, "DELETE");
}

export function listEvaluationRuns(query: PageQuery = {}) {
  return apiGet<PageResult<EvaluationRunDTO>>(withQuery("/api/evaluation-runs", query));
}

export function getEvaluationRun(id: string) {
  return apiGet<EvaluationRunDTO>(`/api/evaluation-runs/${id}`);
}

export function createEvaluationRun(body: EvaluationRunInput) {
  return apiSend<EvaluationRunDTO>("/api/evaluation-runs", "POST", body);
}

export function listEvaluationComparisons(query: PageQuery = {}) {
  return apiGet<PageResult<EvaluationComparisonDTO>>(
    withQuery("/api/evaluation-comparisons", query),
  );
}

export function getEvaluationComparison(id: string) {
  return apiGet<EvaluationComparisonDTO>(`/api/evaluation-comparisons/${id}`);
}

export function createEvaluationComparison(body: EvaluationComparisonInput) {
  return apiSend<EvaluationComparisonDTO>("/api/evaluation-comparisons", "POST", body);
}
