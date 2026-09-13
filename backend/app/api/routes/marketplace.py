"""Marketplace API routes."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.db.models.identity import User
from app.db.models.marketplace import MarketplaceWorkflow, WorkflowReview
from app.db.models.workflow import Workflow

router = APIRouter(prefix="/marketplace", tags=["marketplace"])


# ==================== Request/Response Models ====================


class PublishMetadata(BaseModel):
    """Metadata for publishing a workflow to marketplace."""

    display_name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1, max_length=100)
    tags: list[str] = Field(default_factory=list)
    icon_url: str | None = None
    version: str = Field(..., min_length=1, max_length=50)
    changelog: str | None = None
    dependencies: dict = Field(default_factory=dict)


class MarketplaceWorkflowResponse(BaseModel):
    """Response model for marketplace workflow."""

    id: str
    workflow_id: str
    author_id: str
    author_name: str
    display_name: str
    description: str
    category: str
    tags: list[str]
    icon_url: str | None
    version: str
    changelog: str | None
    dependencies: dict
    downloads: int
    rating: float
    rating_count: int
    status: str
    published_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ReviewInput(BaseModel):
    """Input for creating/updating a review."""

    rating: int = Field(..., ge=1, le=5)
    comment: str | None = None


class ReviewResponse(BaseModel):
    """Response model for workflow review."""

    id: str
    marketplace_workflow_id: str
    user_id: str
    user_name: str
    rating: int
    comment: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class InstallResponse(BaseModel):
    """Response after installing a workflow."""

    workflow_id: str
    message: str


# ==================== Publish Workflow ====================


@router.post("/publish", response_model=MarketplaceWorkflowResponse)
async def publish_workflow(
    workflow_id: str,
    metadata: PublishMetadata,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MarketplaceWorkflowResponse:
    """Publish a workflow to the marketplace."""

    # 1. Verify workflow exists and user owns it
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # TODO: Add ownership check when multi-user is fully implemented
    # For now, assume current_user owns all workflows

    # 2. Check if already published
    result = await db.execute(
        select(MarketplaceWorkflow).where(
            MarketplaceWorkflow.workflow_id == workflow_id
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Workflow already published. Use update endpoint instead.",
        )

    # 3. Create marketplace entry
    now = datetime.now(UTC)
    marketplace_workflow = MarketplaceWorkflow(
        id=uuid4().hex,
        workflow_id=workflow_id,
        author_id=current_user.id,
        display_name=metadata.display_name,
        description=metadata.description,
        category=metadata.category,
        tags=metadata.tags,
        icon_url=metadata.icon_url,
        version=metadata.version,
        changelog=metadata.changelog,
        dependencies=metadata.dependencies,
        status="approved",  # Auto-approve for now
        published_at=now,
        updated_at=now,
    )

    db.add(marketplace_workflow)
    await db.commit()
    await db.refresh(marketplace_workflow)

    return MarketplaceWorkflowResponse(
        id=marketplace_workflow.id,
        workflow_id=marketplace_workflow.workflow_id,
        author_id=marketplace_workflow.author_id,
        author_name=current_user.display_name or current_user.email,
        display_name=marketplace_workflow.display_name,
        description=marketplace_workflow.description,
        category=marketplace_workflow.category,
        tags=marketplace_workflow.tags,
        icon_url=marketplace_workflow.icon_url,
        version=marketplace_workflow.version,
        changelog=marketplace_workflow.changelog,
        dependencies=marketplace_workflow.dependencies,
        downloads=marketplace_workflow.downloads,
        rating=marketplace_workflow.rating,
        rating_count=marketplace_workflow.rating_count,
        status=marketplace_workflow.status,
        published_at=marketplace_workflow.published_at,
        updated_at=marketplace_workflow.updated_at,
    )


# ==================== Update Published Workflow ====================


@router.put("/publish/{marketplace_id}", response_model=MarketplaceWorkflowResponse)
async def update_published_workflow(
    marketplace_id: str,
    metadata: PublishMetadata,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MarketplaceWorkflowResponse:
    """Update an already published workflow in the marketplace."""

    # 1. Get existing marketplace workflow
    result = await db.execute(
        select(MarketplaceWorkflow).where(MarketplaceWorkflow.id == marketplace_id)
    )
    marketplace_workflow = result.scalar_one_or_none()

    if not marketplace_workflow:
        raise HTTPException(status_code=404, detail="Marketplace workflow not found")

    # 2. Check ownership
    if marketplace_workflow.author_id != current_user.id:
        raise HTTPException(
            status_code=403, detail="Only the author can update this workflow"
        )

    # 3. Update metadata
    marketplace_workflow.display_name = metadata.display_name
    marketplace_workflow.description = metadata.description
    marketplace_workflow.category = metadata.category
    marketplace_workflow.tags = metadata.tags
    marketplace_workflow.icon_url = metadata.icon_url
    marketplace_workflow.version = metadata.version
    marketplace_workflow.changelog = metadata.changelog
    marketplace_workflow.dependencies = metadata.dependencies
    marketplace_workflow.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(marketplace_workflow)

    return MarketplaceWorkflowResponse(
        id=marketplace_workflow.id,
        workflow_id=marketplace_workflow.workflow_id,
        author_id=marketplace_workflow.author_id,
        author_name=current_user.display_name or current_user.email,
        display_name=marketplace_workflow.display_name,
        description=marketplace_workflow.description,
        category=marketplace_workflow.category,
        tags=marketplace_workflow.tags,
        icon_url=marketplace_workflow.icon_url,
        version=marketplace_workflow.version,
        changelog=marketplace_workflow.changelog,
        dependencies=marketplace_workflow.dependencies,
        downloads=marketplace_workflow.downloads,
        rating=marketplace_workflow.rating,
        rating_count=marketplace_workflow.rating_count,
        status=marketplace_workflow.status,
        published_at=marketplace_workflow.published_at,
        updated_at=marketplace_workflow.updated_at,
    )


# ==================== List Marketplace Workflows ====================


@router.get("/workflows", response_model=list[MarketplaceWorkflowResponse])
async def list_marketplace_workflows(
    db: Annotated[AsyncSession, Depends(get_db)],
    category: str | None = None,
    tags: list[str] = Query(default_factory=list),
    search: str | None = None,
    sort_by: str = "downloads",  # downloads, rating, recent
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> list[MarketplaceWorkflowResponse]:
    """Browse marketplace workflows."""

    query = select(MarketplaceWorkflow, User).join(
        User, MarketplaceWorkflow.author_id == User.id
    ).where(MarketplaceWorkflow.status == "approved")

    # Filter by category
    if category:
        query = query.where(MarketplaceWorkflow.category == category)

    # Filter by tags (workflows containing any of the specified tags)
    if tags:
        # PostgreSQL: tags @> ANY(ARRAY[...])
        # SQLite: JSON filtering (less efficient)
        for tag in tags:
            query = query.where(MarketplaceWorkflow.tags.contains([tag]))

    # Search by display name, description, or author name
    if search and search.strip():
        search_term = f"%{search.strip().lower()}%"
        query = query.where(
            (MarketplaceWorkflow.display_name.ilike(search_term))
            | (MarketplaceWorkflow.description.ilike(search_term))
            | (User.display_name.ilike(search_term))
            | (User.email.ilike(search_term))
        )

    # Sort
    if sort_by == "rating":
        query = query.order_by(desc(MarketplaceWorkflow.rating))
    elif sort_by == "recent":
        query = query.order_by(desc(MarketplaceWorkflow.published_at))
    else:  # downloads
        query = query.order_by(desc(MarketplaceWorkflow.downloads))

    # Pagination
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    rows = result.all()

    return [
        MarketplaceWorkflowResponse(
            id=mw.id,
            workflow_id=mw.workflow_id,
            author_id=mw.author_id,
            author_name=user.display_name or user.email,
            display_name=mw.display_name,
            description=mw.description,
            category=mw.category,
            tags=mw.tags,
            icon_url=mw.icon_url,
            version=mw.version,
            changelog=mw.changelog,
            dependencies=mw.dependencies,
            downloads=mw.downloads,
            rating=mw.rating,
            rating_count=mw.rating_count,
            status=mw.status,
            published_at=mw.published_at,
            updated_at=mw.updated_at,
        )
        for mw, user in rows
    ]


# ==================== Get Workflow Detail ====================


@router.get("/workflows/{workflow_id}", response_model=MarketplaceWorkflowResponse)
async def get_marketplace_workflow(
    workflow_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MarketplaceWorkflowResponse:
    """Get marketplace workflow details."""

    result = await db.execute(
        select(MarketplaceWorkflow, User)
        .join(User, MarketplaceWorkflow.author_id == User.id)
        .where(MarketplaceWorkflow.id == workflow_id)
    )
    row = result.one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="Marketplace workflow not found")

    mw, user = row

    return MarketplaceWorkflowResponse(
        id=mw.id,
        workflow_id=mw.workflow_id,
        author_id=mw.author_id,
        author_name=user.display_name or user.email,
        display_name=mw.display_name,
        description=mw.description,
        category=mw.category,
        tags=mw.tags,
        icon_url=mw.icon_url,
        version=mw.version,
        changelog=mw.changelog,
        dependencies=mw.dependencies,
        downloads=mw.downloads,
        rating=mw.rating,
        rating_count=mw.rating_count,
        status=mw.status,
        published_at=mw.published_at,
        updated_at=mw.updated_at,
    )


# ==================== Install Workflow ====================


@router.post("/install/{workflow_id}", response_model=InstallResponse)
async def install_workflow(
    workflow_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> InstallResponse:
    """Install a workflow from marketplace (clone to user's workspace)."""

    # 1. Get marketplace workflow
    result = await db.execute(
        select(MarketplaceWorkflow).where(MarketplaceWorkflow.id == workflow_id)
    )
    mw = result.scalar_one_or_none()

    if not mw:
        raise HTTPException(status_code=404, detail="Marketplace workflow not found")

    if mw.status != "approved":
        raise HTTPException(status_code=400, detail="Workflow not approved for installation")

    # 2. Get source workflow
    result = await db.execute(select(Workflow).where(Workflow.id == mw.workflow_id))
    source_workflow = result.scalar_one_or_none()

    if not source_workflow:
        raise HTTPException(status_code=404, detail="Source workflow not found")

    # 3. Clone workflow to user's workspace
    cloned_workflow = Workflow(
        id=uuid4().hex,
        name=f"{mw.display_name} (from marketplace)",
        description=mw.description,
        dsl_json=source_workflow.dsl_json,
        version=1,
        # TODO: Set project_id to current_user's default project when multi-tenancy is ready
        project_id=None,
    )

    db.add(cloned_workflow)

    # 4. Increment download count
    mw.downloads += 1
    mw.updated_at = datetime.now(UTC)

    await db.commit()

    return InstallResponse(
        workflow_id=cloned_workflow.id,
        message=f"Workflow '{mw.display_name}' installed successfully",
    )


# ==================== Reviews ====================


@router.post("/workflows/{workflow_id}/reviews", response_model=ReviewResponse)
async def create_review(
    workflow_id: str,
    review_input: ReviewInput,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ReviewResponse:
    """Create or update a review for a marketplace workflow."""

    # 1. Verify marketplace workflow exists
    result = await db.execute(
        select(MarketplaceWorkflow).where(MarketplaceWorkflow.id == workflow_id)
    )
    mw = result.scalar_one_or_none()

    if not mw:
        raise HTTPException(status_code=404, detail="Marketplace workflow not found")

    # 2. Check if user already reviewed
    result = await db.execute(
        select(WorkflowReview).where(
            WorkflowReview.marketplace_workflow_id == workflow_id,
            WorkflowReview.user_id == current_user.id,
        )
    )
    existing_review = result.scalar_one_or_none()

    now = datetime.now(UTC)

    if existing_review:
        # Update existing review
        old_rating = existing_review.rating
        existing_review.rating = review_input.rating
        existing_review.comment = review_input.comment
        existing_review.updated_at = now

        # Recalculate average rating
        rating_diff = review_input.rating - old_rating
        mw.rating = (mw.rating * mw.rating_count + rating_diff) / mw.rating_count
        mw.updated_at = now

        await db.commit()
        await db.refresh(existing_review)

        return ReviewResponse(
            id=existing_review.id,
            marketplace_workflow_id=existing_review.marketplace_workflow_id,
            user_id=existing_review.user_id,
            user_name=current_user.display_name or current_user.email,
            rating=existing_review.rating,
            comment=existing_review.comment,
            created_at=existing_review.created_at,
            updated_at=existing_review.updated_at,
        )

    # 3. Create new review
    review = WorkflowReview(
        id=uuid4().hex,
        marketplace_workflow_id=workflow_id,
        user_id=current_user.id,
        rating=review_input.rating,
        comment=review_input.comment,
        created_at=now,
        updated_at=now,
    )

    db.add(review)

    # 4. Update aggregate rating
    new_rating_count = mw.rating_count + 1
    mw.rating = (mw.rating * mw.rating_count + review_input.rating) / new_rating_count
    mw.rating_count = new_rating_count
    mw.updated_at = now

    await db.commit()
    await db.refresh(review)

    return ReviewResponse(
        id=review.id,
        marketplace_workflow_id=review.marketplace_workflow_id,
        user_id=review.user_id,
        user_name=current_user.display_name or current_user.email,
        rating=review.rating,
        comment=review.comment,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


@router.get("/workflows/{workflow_id}/reviews", response_model=list[ReviewResponse])
async def list_reviews(
    workflow_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
) -> list[ReviewResponse]:
    """Get reviews for a marketplace workflow."""

    query = (
        select(WorkflowReview, User)
        .join(User, WorkflowReview.user_id == User.id)
        .where(WorkflowReview.marketplace_workflow_id == workflow_id)
        .order_by(desc(WorkflowReview.created_at))
    )

    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    rows = result.all()

    return [
        ReviewResponse(
            id=review.id,
            marketplace_workflow_id=review.marketplace_workflow_id,
            user_id=review.user_id,
            user_name=user.display_name or user.email,
            rating=review.rating,
            comment=review.comment,
            created_at=review.created_at,
            updated_at=review.updated_at,
        )
        for review, user in rows
    ]
