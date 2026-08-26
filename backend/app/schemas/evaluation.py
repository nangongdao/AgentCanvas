"""HTTP contracts for datasets, evaluators, and evaluation reports."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

EvaluatorType = Literal["exact", "contains", "json_schema", "llm_judge", "rag"]


class RagExpectation(BaseModel):
    answerable: bool = True
    relevant_document_ids: list[str] = Field(default_factory=list, max_length=100)
    relevant_chunk_ids: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("relevant_document_ids", "relevant_chunk_ids")
    @classmethod
    def normalize_source_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("RAG relevant source ids must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_relevance_scope(self) -> RagExpectation:
        self.relevant_document_ids = list(dict.fromkeys(self.relevant_document_ids))
        self.relevant_chunk_ids = list(dict.fromkeys(self.relevant_chunk_ids))
        has_documents = bool(self.relevant_document_ids)
        has_chunks = bool(self.relevant_chunk_ids)
        if self.answerable and has_documents == has_chunks:
            raise ValueError(
                "answerable RAG cases require exactly one relevant document or chunk list"
            )
        if not self.answerable and (has_documents or has_chunks):
            raise ValueError("unanswerable RAG cases must not declare relevant sources")
        return self


class EvaluationCaseInput(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12], min_length=1, max_length=64)
    name: str = Field(default="", max_length=200)
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected: Any = None


class EvaluationDatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    cases: list[EvaluationCaseInput] = Field(min_length=1, max_length=200)
    change_summary: str = Field(default="Initial dataset", max_length=2000)

    @model_validator(mode="after")
    def unique_case_ids(self) -> EvaluationDatasetCreate:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")
        return self


class EvaluationDatasetUpdate(EvaluationDatasetCreate):
    change_summary: str = Field(default="Dataset update", max_length=2000)
    expected_version: int | None = Field(default=None, ge=1)


class EvaluationDatasetVersionOut(BaseModel):
    id: str
    dataset_id: str
    number: int
    cases: list[EvaluationCaseInput]
    change_summary: str = ""
    created_at: datetime | None = None


class EvaluationDatasetOut(BaseModel):
    id: str
    name: str
    description: str = ""
    current_version: int
    case_count: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EvaluationDatasetDetailOut(EvaluationDatasetOut):
    versions: list[EvaluationDatasetVersionOut] = Field(default_factory=list)


class EvaluationConfigInput(BaseModel):
    evaluator_type: EvaluatorType
    actual_path: str = Field(default="", max_length=500)
    case_sensitive: bool = True
    model_config_id: str | None = Field(default=None, min_length=1, max_length=64)
    rubric: str = Field(default="", max_length=8000)
    threshold: float = Field(default=0.5, ge=0, le=1)
    allow_llm_judge: bool = False
    allow_side_effects: bool = False
    rag_node_id: str | None = Field(default=None, min_length=1, max_length=64)
    citation_node_id: str | None = Field(default=None, min_length=1, max_length=64)
    retrieval_k: int = Field(default=5, ge=1, le=50)
    min_recall_at_k: float = Field(default=1.0, ge=0, le=1)
    min_mrr: float = Field(default=1.0, ge=0, le=1)
    min_citation_coverage: float = Field(default=0.0, ge=0, le=1)
    require_correct_no_answer: bool = True

    @model_validator(mode="after")
    def validate_judge_opt_in(self) -> EvaluationConfigInput:
        if self.evaluator_type == "llm_judge":
            if not self.allow_llm_judge:
                raise ValueError("LLM judge requires explicit opt-in")
            if not self.model_config_id:
                raise ValueError("LLM judge requires model_config_id")
            if not self.rubric.strip():
                raise ValueError("LLM judge requires a rubric")
        return self

    def evaluator_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "actual_path": self.actual_path,
            "case_sensitive": self.case_sensitive,
            "model_config_id": self.model_config_id,
            "rubric": self.rubric,
            "threshold": self.threshold,
        }
        if self.evaluator_type == "rag":
            config.update(
                {
                    "rag_node_id": self.rag_node_id,
                    "citation_node_id": self.citation_node_id,
                    "retrieval_k": self.retrieval_k,
                    "min_recall_at_k": self.min_recall_at_k,
                    "min_mrr": self.min_mrr,
                    "min_citation_coverage": self.min_citation_coverage,
                    "require_correct_no_answer": self.require_correct_no_answer,
                }
            )
        return config


class EvaluationRunCreate(EvaluationConfigInput):
    dataset_version_id: str = Field(min_length=1, max_length=32)
    workflow_version_id: str = Field(min_length=1, max_length=32)


class EvaluationComparisonCreate(EvaluationConfigInput):
    dataset_version_id: str = Field(min_length=1, max_length=32)
    workflow_version_a_id: str = Field(min_length=1, max_length=32)
    workflow_version_b_id: str = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def distinct_versions(self) -> EvaluationComparisonCreate:
        if self.workflow_version_a_id == self.workflow_version_b_id:
            raise ValueError("comparison requires two different workflow versions")
        return self

    def run_input(self, workflow_version_id: str) -> EvaluationRunCreate:
        return EvaluationRunCreate(
            dataset_version_id=self.dataset_version_id,
            workflow_version_id=workflow_version_id,
            **self.model_dump(
                exclude={
                    "dataset_version_id",
                    "workflow_version_a_id",
                    "workflow_version_b_id",
                }
            ),
        )


class EvaluationCaseResultOut(BaseModel):
    id: str
    case_id: str
    case_index: int
    name: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected: Any = None
    actual: Any = None
    execution_id: str | None = None
    status: str
    score: float | None = None
    message: str = ""
    duration_ms: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: str | None = None
    cost_known: bool = False
    cost_error_bound_usd: str | None = None
    price_versions: list[str] = Field(default_factory=list)
    finished_at: datetime | None = None


class EvaluationRunOut(BaseModel):
    id: str
    dataset_version_id: str
    dataset_id: str | None = None
    dataset_name: str | None = None
    dataset_version_number: int | None = None
    workflow_version_id: str
    workflow_id: str | None = None
    workflow_name: str | None = None
    workflow_version_number: int | None = None
    evaluator_type: EvaluatorType
    evaluator_config: dict[str, Any] = Field(default_factory=dict)
    status: str
    summary: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    cases: list[EvaluationCaseResultOut] = Field(default_factory=list)
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class EvaluationComparisonCaseOut(BaseModel):
    case_id: str
    name: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected: Any = None
    variant_a: EvaluationCaseResultOut
    variant_b: EvaluationCaseResultOut
    score_delta: float | None = None
    duration_delta_ms: int | None = None
    estimated_cost_delta_usd: str | None = None


class EvaluationComparisonOut(BaseModel):
    id: str
    dataset_version_id: str
    dataset_id: str | None = None
    dataset_name: str | None = None
    dataset_version_number: int | None = None
    status: str
    summary: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    variant_a: EvaluationRunOut
    variant_b: EvaluationRunOut
    cases: list[EvaluationComparisonCaseOut] = Field(default_factory=list)
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


__all__ = [
    "EvaluationCaseInput",
    "EvaluationCaseResultOut",
    "EvaluationComparisonCaseOut",
    "EvaluationComparisonCreate",
    "EvaluationComparisonOut",
    "EvaluationConfigInput",
    "EvaluationDatasetCreate",
    "EvaluationDatasetDetailOut",
    "EvaluationDatasetOut",
    "EvaluationDatasetUpdate",
    "EvaluationDatasetVersionOut",
    "EvaluationRunCreate",
    "EvaluationRunOut",
    "EvaluatorType",
    "RagExpectation",
]
