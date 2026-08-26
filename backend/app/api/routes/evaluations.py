"""Versioned dataset CRUD and evaluation report endpoints."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import EditorDep, ViewerDep, get_container, get_session
from app.api.pagination import PageParams, PageResult, page_result
from app.core.container import ServiceContainer
from app.db.models import EvaluationDatasetVersion, EvaluationRun
from app.db.repositories import (
    EvaluationComparisonRepo,
    EvaluationDatasetRepo,
    EvaluationRunRepo,
)
from app.schemas.evaluation import (
    EvaluationCaseInput,
    EvaluationCaseResultOut,
    EvaluationComparisonCaseOut,
    EvaluationComparisonCreate,
    EvaluationComparisonOut,
    EvaluationDatasetCreate,
    EvaluationDatasetDetailOut,
    EvaluationDatasetOut,
    EvaluationDatasetUpdate,
    EvaluationDatasetVersionOut,
    EvaluationRunCreate,
    EvaluationRunOut,
)

router = APIRouter(prefix="/api", tags=["evaluations"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


def _version_out(row) -> EvaluationDatasetVersionOut:
    return EvaluationDatasetVersionOut(
        id=row.id,
        dataset_id=row.dataset_id,
        number=row.number,
        cases=[EvaluationCaseInput.model_validate(case) for case in row.cases_json or []],
        change_summary=row.change_summary or "",
        created_at=row.created_at,
    )


def _dataset_out(row) -> EvaluationDatasetOut:
    current = next(
        (version for version in row.versions if version.number == row.current_version), None
    )
    return EvaluationDatasetOut(
        id=row.id,
        name=row.name,
        description=row.description or "",
        current_version=row.current_version,
        case_count=len(current.cases_json or []) if current is not None else 0,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _dataset_detail(row) -> EvaluationDatasetDetailOut:
    base = _dataset_out(row)
    return EvaluationDatasetDetailOut(
        **base.model_dump(),
        versions=[_version_out(version) for version in reversed(row.versions)],
    )


def _case_out(row) -> EvaluationCaseResultOut:
    return EvaluationCaseResultOut(
        id=row.id,
        case_id=row.case_id,
        case_index=row.case_index,
        name=row.name or "",
        inputs=row.input_json or {},
        expected=row.expected_json,
        actual=row.actual_json,
        execution_id=row.execution_id,
        status=row.status,
        score=row.score,
        message=row.message or "",
        duration_ms=row.duration_ms,
        prompt_tokens=row.prompt_tokens or 0,
        completion_tokens=row.completion_tokens or 0,
        total_tokens=row.total_tokens or 0,
        estimated_cost_usd=row.estimated_cost_usd,
        cost_known=bool(row.cost_known),
        cost_error_bound_usd=row.cost_error_bound_usd,
        price_versions=list(row.price_versions_json or []),
        finished_at=row.finished_at,
    )


def _comparison_out(row) -> EvaluationComparisonOut:
    a = _run_out(row.variant_a_run)
    b = _run_out(row.variant_b_run)
    b_by_case = {case.case_id: case for case in b.cases}
    cases: list[EvaluationComparisonCaseOut] = []
    for case_a in a.cases:
        case_b = b_by_case.get(case_a.case_id)
        if case_b is None:
            continue
        cost_delta = None
        if case_a.estimated_cost_usd is not None and case_b.estimated_cost_usd is not None:
            try:
                cost_delta = format(
                    Decimal(case_b.estimated_cost_usd) - Decimal(case_a.estimated_cost_usd),
                    "f",
                )
            except (InvalidOperation, ValueError):
                cost_delta = None
        cases.append(
            EvaluationComparisonCaseOut(
                case_id=case_a.case_id,
                name=case_a.name,
                inputs=case_a.inputs,
                expected=case_a.expected,
                variant_a=case_a,
                variant_b=case_b,
                score_delta=(
                    case_b.score - case_a.score
                    if case_a.score is not None and case_b.score is not None
                    else None
                ),
                duration_delta_ms=(
                    case_b.duration_ms - case_a.duration_ms
                    if case_a.duration_ms is not None and case_b.duration_ms is not None
                    else None
                ),
                estimated_cost_delta_usd=cost_delta,
            )
        )
    dataset_version = row.dataset_version
    return EvaluationComparisonOut(
        id=row.id,
        dataset_version_id=row.dataset_version_id,
        dataset_id=dataset_version.dataset_id if dataset_version else None,
        dataset_name=(dataset_version.dataset.name if dataset_version and dataset_version.dataset else None),
        dataset_version_number=dataset_version.number if dataset_version else None,
        status=row.status,
        summary=row.summary_json or {},
        error=row.error,
        variant_a=a,
        variant_b=b,
        cases=cases,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _run_out(row) -> EvaluationRunOut:
    dataset_version = row.dataset_version
    workflow_version = row.workflow_version
    return EvaluationRunOut(
        id=row.id,
        dataset_version_id=row.dataset_version_id,
        dataset_id=dataset_version.dataset_id if dataset_version is not None else None,
        dataset_name=(
            dataset_version.dataset.name
            if dataset_version is not None and dataset_version.dataset is not None
            else None
        ),
        dataset_version_number=(dataset_version.number if dataset_version is not None else None),
        workflow_version_id=row.workflow_version_id,
        workflow_id=workflow_version.workflow_id if workflow_version is not None else None,
        workflow_name=workflow_version.name if workflow_version is not None else None,
        workflow_version_number=(workflow_version.number if workflow_version is not None else None),
        evaluator_type=row.evaluator_type,
        evaluator_config=row.evaluator_config_json or {},
        status=row.status,
        summary=row.summary_json or {},
        error=row.error,
        cases=[_case_out(case) for case in row.case_results],
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


@router.get("/evaluation-datasets", response_model=PageResult[EvaluationDatasetOut])
async def list_datasets(
    session: SessionDep,
    _principal: ViewerDep,
    params: Annotated[PageParams, Depends()],
) -> PageResult[EvaluationDatasetOut]:
    spec = params.to_spec(
        allowed_sorts={"created_at", "updated_at", "name", "id"},
        default_sort="updated_at",
    )
    return page_result(await EvaluationDatasetRepo(session).list_page(spec), _dataset_out)


@router.post(
    "/evaluation-datasets",
    response_model=EvaluationDatasetDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_dataset(
    body: EvaluationDatasetCreate, session: SessionDep, _principal: EditorDep
) -> EvaluationDatasetDetailOut:
    row = await EvaluationDatasetRepo(session).create(
        name=body.name.strip(),
        description=body.description,
        cases=[case.model_dump(mode="json") for case in body.cases],
        change_summary=body.change_summary,
    )
    return _dataset_detail(row)


@router.get(
    "/evaluation-datasets/{dataset_id}", response_model=EvaluationDatasetDetailOut
)
async def get_dataset(
    dataset_id: str, session: SessionDep, _principal: ViewerDep
) -> EvaluationDatasetDetailOut:
    row = await EvaluationDatasetRepo(session).get(dataset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="evaluation dataset not found")
    return _dataset_detail(row)


@router.put(
    "/evaluation-datasets/{dataset_id}", response_model=EvaluationDatasetDetailOut
)
async def update_dataset(
    dataset_id: str,
    body: EvaluationDatasetUpdate,
    session: SessionDep,
    _principal: EditorDep,
) -> EvaluationDatasetDetailOut:
    repo = EvaluationDatasetRepo(session)
    row = await repo.get(dataset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="evaluation dataset not found")
    if body.expected_version is not None and row.current_version != body.expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "evaluation dataset version conflict: "
                f"expected {body.expected_version}, got {row.current_version}"
            ),
        )
    try:
        row = await repo.update(
            row,
            name=body.name.strip(),
            description=body.description,
            cases=[case.model_dump(mode="json") for case in body.cases],
            change_summary=body.change_summary,
        )
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="evaluation dataset version changed during update",
        ) from exc
    return _dataset_detail(row)


@router.delete("/evaluation-datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(
    dataset_id: str, session: SessionDep, _principal: EditorDep
) -> None:
    repo = EvaluationDatasetRepo(session)
    row = await repo.get(dataset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="evaluation dataset not found")
    run_count = await session.scalar(
        select(func.count(EvaluationRun.id))
        .join(
            EvaluationDatasetVersion,
            EvaluationRun.dataset_version_id == EvaluationDatasetVersion.id,
        )
        .where(EvaluationDatasetVersion.dataset_id == dataset_id)
    )
    if run_count:
        raise HTTPException(
            status_code=409,
            detail="dataset has evaluation reports and cannot be deleted",
        )
    await repo.delete(row)


@router.get("/evaluation-runs", response_model=PageResult[EvaluationRunOut])
async def list_evaluation_runs(
    session: SessionDep,
    _principal: ViewerDep,
    params: Annotated[PageParams, Depends()],
) -> PageResult[EvaluationRunOut]:
    spec = params.to_spec(
        allowed_sorts={"created_at", "status", "evaluator_type", "id"},
        default_sort="created_at",
    )
    return page_result(await EvaluationRunRepo(session).list_page(spec), _run_out)


@router.post(
    "/evaluation-runs",
    response_model=EvaluationRunOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_evaluation_run(
    body: EvaluationRunCreate,
    container: ContainerDep,
    _principal: EditorDep,
) -> EvaluationRunOut:
    try:
        row = await container.evaluation_manager.create_run(body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _run_out(row)


@router.get("/evaluation-runs/{run_id}", response_model=EvaluationRunOut)
async def get_evaluation_run(
    run_id: str, session: SessionDep, _principal: ViewerDep
) -> EvaluationRunOut:
    row = await EvaluationRunRepo(session).get(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="evaluation run not found")
    return _run_out(row)


@router.get("/evaluation-comparisons", response_model=PageResult[EvaluationComparisonOut])
async def list_evaluation_comparisons(
    session: SessionDep,
    _principal: ViewerDep,
    params: Annotated[PageParams, Depends()],
) -> PageResult[EvaluationComparisonOut]:
    spec = params.to_spec(
        allowed_sorts={"created_at", "status", "id"}, default_sort="created_at"
    )
    return page_result(
        await EvaluationComparisonRepo(session).list_page(spec), _comparison_out
    )


@router.post(
    "/evaluation-comparisons",
    response_model=EvaluationComparisonOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_evaluation_comparison(
    body: EvaluationComparisonCreate,
    container: ContainerDep,
    _principal: EditorDep,
) -> EvaluationComparisonOut:
    try:
        row = await container.evaluation_manager.create_comparison(body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _comparison_out(row)


@router.get("/evaluation-comparisons/{comparison_id}", response_model=EvaluationComparisonOut)
async def get_evaluation_comparison(
    comparison_id: str, session: SessionDep, _principal: ViewerDep
) -> EvaluationComparisonOut:
    row = await EvaluationComparisonRepo(session).get(comparison_id)
    if row is None:
        raise HTTPException(status_code=404, detail="evaluation comparison not found")
    return _comparison_out(row)


__all__ = ["router"]
