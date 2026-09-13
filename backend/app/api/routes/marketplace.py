"""Marketplace API routes."""

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import EditorDep, ViewerDep, get_session
from app.db.models.identity import User
from app.db.models.marketplace import MarketplaceWorkflow, MarketplaceReview
from app.db.models.workflow import Workflow

router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

# In "disabled" auth mode (single-user) FastAPI runs as a local-development
# principal without a persistent user row. This stable id backs marketplace
# writes until a multi-user login flow is active.
LOCAL_USER_ID = "local"


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

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, tags: list[str]) -> list[str]:
        """Limit tag count/size and reject control characters."""
        if len(tags) > 10:
            raise ValueError("标签数量不能超过10个")
        for tag in tags:
            if not tag.strip() or len(tag.strip()) > 50:
                raise ValueError("每个标签必须为1-50个字符")
            if any(ord(ch) < 32 for ch in tag):
                raise ValueError("标签不能包含控制字符")
        return [tag.strip() for tag in tags]

    @field_validator("icon_url")
    @classmethod
    def validate_icon_url(cls, url: str | None) -> str | None:
        """Reject non-http(s) or malformed icon URLs server-side."""
        if url is None or not url.strip():
            return None
        url = url.strip()
        if len(url) > 512:
            raise ValueError("图标URL长度不能超过512个字符")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("图标URL必须使用http或https协议")
        return url


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

    model_config = ConfigDict(from_attributes=True)


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

    model_config = ConfigDict(from_attributes=True)


class InstallResponse(BaseModel):
    """Response after installing a workflow."""

    workflow_id: str
    message: str


# ==================== Publish Workflow ====================


async def _author_display_name(
    session: AsyncSession, user_id: str
) -> str:
    """Fetch a user's display name, falling back to their email."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return "unknown"
    return user.display_name or user.email


async def _require_user_id(session: AsyncSession, principal) -> str:
    """Return the authenticated user id or reject service-account calls.

    In "disabled" auth mode (single developer, no login) every request is the
    local principal. Ensure a synthetic User row exists so author_id FKs stay
    valid, then reuse it for all local marketplace writes.
    """
    if principal.user_id is not None:
        return principal.user_id
    if principal.auth_method != "disabled":
        raise HTTPException(
            status_code=403,
            detail="service accounts cannot publish or install marketplace workflows",
        )
    # Single-user mode: create/reuse a stable local user row.
    result = await session.execute(select(User).where(User.id == LOCAL_USER_ID))
    local_user = result.scalar_one_or_none()
    if local_user is None:
        local_user = User(
            id=LOCAL_USER_ID,
            email="local@agentcanvas.local",
            display_name="本地用户",
        )
        session.add(local_user)
        await session.commit()
    return LOCAL_USER_ID


@router.post("/publish", response_model=MarketplaceWorkflowResponse)
async def publish_workflow(
    workflow_id: str,
    metadata: PublishMetadata,
    session: SessionDep,
    principal: EditorDep,
) -> MarketplaceWorkflowResponse:
    """Publish a workflow to the marketplace."""

    user_id = await _require_user_id(session, principal)

    # 1. Verify workflow exists and user owns it
    result = await session.execute(select(Workflow).where(Workflow.id == workflow_id))
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # TODO: Add ownership check when multi-user is fully implemented
    # For now, assume current_user owns all workflows

    # 2. Check if already published
    result = await session.execute(
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
        author_id=user_id,
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

    session.add(marketplace_workflow)
    await session.commit()
    await session.refresh(marketplace_workflow)

    return MarketplaceWorkflowResponse(
        id=marketplace_workflow.id,
        workflow_id=marketplace_workflow.workflow_id,
        author_id=marketplace_workflow.author_id,
        author_name=await _author_display_name(session, user_id),
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
    session: SessionDep,
    principal: EditorDep,
) -> MarketplaceWorkflowResponse:
    """Update an already published workflow in the marketplace."""

    user_id = await _require_user_id(session, principal)

    # 1. Get existing marketplace workflow
    result = await session.execute(
        select(MarketplaceWorkflow).where(MarketplaceWorkflow.id == marketplace_id)
    )
    marketplace_workflow = result.scalar_one_or_none()

    if not marketplace_workflow:
        raise HTTPException(status_code=404, detail="Marketplace workflow not found")

    # 2. Check ownership
    if marketplace_workflow.author_id != user_id:
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

    await session.commit()
    await session.refresh(marketplace_workflow)

    return MarketplaceWorkflowResponse(
        id=marketplace_workflow.id,
        workflow_id=marketplace_workflow.workflow_id,
        author_id=marketplace_workflow.author_id,
        author_name=await _author_display_name(session, user_id),
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
    session: SessionDep,
    _principal: ViewerDep,
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
        # Escape LIKE wildcards (%/_) using '!' as the escape char so user
        # input is matched literally; '!' itself is escaped too. '!' has no
        # special meaning in SQL string literals (unlike backslash), so this
        # is portable across SQLite and PostgreSQL inside ilike().
        escaped = (
            search.strip()
            .replace("!", "!!")
            .replace("%", "!%")
            .replace("_", "!_")
        )
        search_term = f"%{escaped.lower()}%"
        query = query.where(
            MarketplaceWorkflow.display_name.ilike(search_term, escape="!")
            | MarketplaceWorkflow.description.ilike(search_term, escape="!")
            | User.display_name.ilike(search_term, escape="!")
            | User.email.ilike(search_term, escape="!"),
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

    result = await session.execute(query)
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
    session: SessionDep,
    _principal: ViewerDep,
) -> MarketplaceWorkflowResponse:
    """Get marketplace workflow details.

    ``workflow_id`` here is the *marketplace* entry id (the primary key of
    ``marketplace_workflows``), not the originating workflow id.
    """

    result = await session.execute(
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


# ==================== Get Marketplace Entry by Workflow ====================


@router.get("/entry/{workflow_id}", response_model=MarketplaceWorkflowResponse | None)
async def get_marketplace_entry_by_workflow(
    workflow_id: str,
    session: SessionDep,
    _principal: ViewerDep,
) -> MarketplaceWorkflowResponse | None:
    """Return the marketplace entry for an originating workflow, if any.

    This lets the frontend distinguish "never published" from "published —
    use the update endpoint". Returns 200 with ``null`` when unpublished.
    """

    result = await session.execute(
        select(MarketplaceWorkflow, User)
        .join(User, MarketplaceWorkflow.author_id == User.id)
        .where(MarketplaceWorkflow.workflow_id == workflow_id)
    )
    row = result.one_or_none()

    if not row:
        return None

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
    session: SessionDep,
    principal: EditorDep,
) -> InstallResponse:
    """Install a workflow from marketplace (clone to user's workspace)."""

    _user_id = await _require_user_id(session, principal)

    # 1. Get marketplace workflow
    result = await session.execute(
        select(MarketplaceWorkflow).where(MarketplaceWorkflow.id == workflow_id)
    )
    mw = result.scalar_one_or_none()

    if not mw:
        raise HTTPException(status_code=404, detail="Marketplace workflow not found")

    if mw.status != "approved":
        raise HTTPException(status_code=400, detail="Workflow not approved for installation")

    # 2. Get source workflow
    result = await session.execute(select(Workflow).where(Workflow.id == mw.workflow_id))
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

    session.add(cloned_workflow)

    # 4. Increment download count
    mw.downloads += 1
    mw.updated_at = datetime.now(UTC)

    await session.commit()

    return InstallResponse(
        workflow_id=cloned_workflow.id,
        message=f"Workflow '{mw.display_name}' installed successfully",
    )


# ==================== Reviews ====================


@router.post("/workflows/{workflow_id}/reviews", response_model=ReviewResponse)
async def create_review(
    workflow_id: str,
    review_input: ReviewInput,
    session: SessionDep,
    principal: EditorDep,
) -> ReviewResponse:
    """Create or update a review for a marketplace workflow."""

    user_id = await _require_user_id(session, principal)

    # 1. Verify marketplace workflow exists
    result = await session.execute(
        select(MarketplaceWorkflow).where(MarketplaceWorkflow.id == workflow_id)
    )
    mw = result.scalar_one_or_none()

    if not mw:
        raise HTTPException(status_code=404, detail="Marketplace workflow not found")

    # 2. Check if user already reviewed
    result = await session.execute(
        select(MarketplaceReview).where(
            MarketplaceReview.marketplace_workflow_id == workflow_id,
            MarketplaceReview.user_id == user_id,
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

        await session.commit()
        await session.refresh(existing_review)

        return ReviewResponse(
            id=existing_review.id,
            marketplace_workflow_id=existing_review.marketplace_workflow_id,
            user_id=existing_review.user_id,
            user_name=await _author_display_name(session, user_id),
            rating=existing_review.rating,
            comment=existing_review.comment,
            created_at=existing_review.created_at,
            updated_at=existing_review.updated_at,
        )

    # 3. Create new review
    review = MarketplaceReview(
        id=uuid4().hex,
        marketplace_workflow_id=workflow_id,
        user_id=user_id,
        rating=review_input.rating,
        comment=review_input.comment,
        created_at=now,
        updated_at=now,
    )

    session.add(review)

    # 4. Update aggregate rating
    new_rating_count = mw.rating_count + 1
    mw.rating = (mw.rating * mw.rating_count + review_input.rating) / new_rating_count
    mw.rating_count = new_rating_count
    mw.updated_at = now

    await session.commit()
    await session.refresh(review)

    return ReviewResponse(
        id=review.id,
        marketplace_workflow_id=review.marketplace_workflow_id,
        user_id=review.user_id,
        user_name=await _author_display_name(session, user_id),
        rating=review.rating,
        comment=review.comment,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


@router.get("/workflows/{workflow_id}/reviews", response_model=list[ReviewResponse])
async def list_reviews(
    workflow_id: str,
    session: SessionDep,
    _principal: ViewerDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
) -> list[ReviewResponse]:
    """Get reviews for a marketplace workflow."""

    query = (
        select(MarketplaceReview, User)
        .join(User, MarketplaceReview.user_id == User.id)
        .where(MarketplaceReview.marketplace_workflow_id == workflow_id)
        .order_by(desc(MarketplaceReview.created_at))
    )

    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await session.execute(query)
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
