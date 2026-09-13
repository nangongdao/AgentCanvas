"""Test marketplace API endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.identity import User
from app.db.models.workflow import Workflow


@pytest.mark.asyncio
async def test_publish_workflow(client: AsyncClient, admin_token: str, db: AsyncSession):
    """Test publishing a workflow to marketplace."""
    # Create a workflow first
    workflow_response = await client.post(
        "/api/workflows",
        json={
            "name": "Test Workflow",
            "description": "A test workflow",
            "dsl_json": {"nodes": [], "edges": []},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert workflow_response.status_code == 201
    workflow_id = workflow_response.json()["id"]

    # Publish to marketplace
    response = await client.post(
        f"/api/marketplace/publish?workflow_id={workflow_id}",
        json={
            "display_name": "Amazing Workflow",
            "description": "This is an amazing workflow",
            "category": "automation",
            "tags": ["productivity", "ai"],
            "version": "1.0.0",
            "changelog": "Initial release",
            "dependencies": {},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "Amazing Workflow"
    assert data["category"] == "automation"
    assert data["tags"] == ["productivity", "ai"]
    assert data["version"] == "1.0.0"
    assert data["downloads"] == 0
    assert data["rating"] == 0.0
    assert data["rating_count"] == 0
    assert data["status"] == "approved"


@pytest.mark.asyncio
async def test_list_marketplace_workflows(client: AsyncClient, admin_token: str):
    """Test browsing marketplace workflows."""
    response = await client.get(
        "/api/marketplace/workflows",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_list_marketplace_workflows_with_filters(
    client: AsyncClient, admin_token: str, db: AsyncSession
):
    """Test browsing marketplace workflows with category filter."""
    # Create and publish workflow
    workflow_response = await client.post(
        "/api/workflows",
        json={
            "name": "Analytics Workflow",
            "description": "Data analytics",
            "dsl_json": {"nodes": [], "edges": []},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    workflow_id = workflow_response.json()["id"]

    await client.post(
        f"/api/marketplace/publish?workflow_id={workflow_id}",
        json={
            "display_name": "Analytics Tool",
            "description": "Data analytics workflow",
            "category": "analytics",
            "tags": ["data"],
            "version": "1.0.0",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Filter by category
    response = await client.get(
        "/api/marketplace/workflows?category=analytics",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert all(w["category"] == "analytics" for w in data)


@pytest.mark.asyncio
async def test_install_workflow(client: AsyncClient, admin_token: str, db: AsyncSession):
    """Test installing a workflow from marketplace."""
    # Create and publish workflow
    workflow_response = await client.post(
        "/api/workflows",
        json={
            "name": "Source Workflow",
            "description": "Original",
            "dsl_json": {"nodes": [{"id": "1", "type": "start"}], "edges": []},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    workflow_id = workflow_response.json()["id"]

    publish_response = await client.post(
        f"/api/marketplace/publish?workflow_id={workflow_id}",
        json={
            "display_name": "Installable Workflow",
            "description": "Ready to install",
            "category": "tools",
            "tags": [],
            "version": "1.0.0",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    marketplace_id = publish_response.json()["id"]

    # Install workflow
    install_response = await client.post(
        f"/api/marketplace/install/{marketplace_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert install_response.status_code == 200
    install_data = install_response.json()
    assert "workflow_id" in install_data
    assert "message" in install_data

    # Verify cloned workflow exists
    cloned_id = install_data["workflow_id"]
    verify_response = await client.get(
        f"/api/workflows/{cloned_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert verify_response.status_code == 200
    cloned_workflow = verify_response.json()
    assert "(from marketplace)" in cloned_workflow["name"]

    # Verify download count increased
    detail_response = await client.get(
        f"/api/marketplace/workflows/{marketplace_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert detail_response.json()["downloads"] == 1


@pytest.mark.asyncio
async def test_create_review(client: AsyncClient, admin_token: str, db: AsyncSession):
    """Test creating a review for marketplace workflow."""
    # Create and publish workflow
    workflow_response = await client.post(
        "/api/workflows",
        json={
            "name": "Reviewable Workflow",
            "description": "Can be reviewed",
            "dsl_json": {"nodes": [], "edges": []},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    workflow_id = workflow_response.json()["id"]

    publish_response = await client.post(
        f"/api/marketplace/publish?workflow_id={workflow_id}",
        json={
            "display_name": "Review Test",
            "description": "Testing reviews",
            "category": "test",
            "tags": [],
            "version": "1.0.0",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    marketplace_id = publish_response.json()["id"]

    # Create review
    review_response = await client.post(
        f"/api/marketplace/workflows/{marketplace_id}/reviews",
        json={
            "rating": 5,
            "comment": "Excellent workflow!",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert review_response.status_code == 200
    review_data = review_response.json()
    assert review_data["rating"] == 5
    assert review_data["comment"] == "Excellent workflow!"

    # Verify rating updated
    detail_response = await client.get(
        f"/api/marketplace/workflows/{marketplace_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    marketplace_data = detail_response.json()
    assert marketplace_data["rating"] == 5.0
    assert marketplace_data["rating_count"] == 1


@pytest.mark.asyncio
async def test_update_review(client: AsyncClient, admin_token: str, db: AsyncSession):
    """Test updating an existing review."""
    # Create and publish workflow
    workflow_response = await client.post(
        "/api/workflows",
        json={
            "name": "Update Review Test",
            "description": "Testing review updates",
            "dsl_json": {"nodes": [], "edges": []},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    workflow_id = workflow_response.json()["id"]

    publish_response = await client.post(
        f"/api/marketplace/publish?workflow_id={workflow_id}",
        json={
            "display_name": "Review Update Test",
            "description": "Testing",
            "category": "test",
            "tags": [],
            "version": "1.0.0",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    marketplace_id = publish_response.json()["id"]

    # Create initial review
    await client.post(
        f"/api/marketplace/workflows/{marketplace_id}/reviews",
        json={"rating": 3, "comment": "Okay"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Update review
    update_response = await client.post(
        f"/api/marketplace/workflows/{marketplace_id}/reviews",
        json={"rating": 5, "comment": "Actually great!"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["rating"] == 5

    # Verify rating recalculated correctly
    detail_response = await client.get(
        f"/api/marketplace/workflows/{marketplace_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert detail_response.json()["rating"] == 5.0
    assert detail_response.json()["rating_count"] == 1


@pytest.mark.asyncio
async def test_list_reviews(client: AsyncClient, admin_token: str, db: AsyncSession):
    """Test listing reviews for a marketplace workflow."""
    # Create and publish workflow
    workflow_response = await client.post(
        "/api/workflows",
        json={
            "name": "List Reviews Test",
            "description": "Testing review listing",
            "dsl_json": {"nodes": [], "edges": []},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    workflow_id = workflow_response.json()["id"]

    publish_response = await client.post(
        f"/api/marketplace/publish?workflow_id={workflow_id}",
        json={
            "display_name": "Review List Test",
            "description": "Testing",
            "category": "test",
            "tags": [],
            "version": "1.0.0",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    marketplace_id = publish_response.json()["id"]

    # Create review
    await client.post(
        f"/api/marketplace/workflows/{marketplace_id}/reviews",
        json={"rating": 4, "comment": "Good"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # List reviews
    list_response = await client.get(
        f"/api/marketplace/workflows/{marketplace_id}/reviews",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert list_response.status_code == 200
    reviews = list_response.json()
    assert len(reviews) == 1
    assert reviews[0]["rating"] == 4
    assert reviews[0]["comment"] == "Good"
