"""Marketplace API endpoint tests.

These cover the publish → list → detail → update → install → review flow.
Auth is disabled (single-user local mode) so requests run without tokens.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

FERNET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key=FERNET_KEY,
    )


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    with TestClient(create_app(_settings(tmp_path))) as c:
        yield c


def _dsl() -> dict:
    """A minimal valid workflow DSL (exactly one start and one end node)."""
    return {
        "nodes": [
            {"id": "n_start", "type": "start", "data": {}},
            {"id": "n_end", "type": "end", "data": {}},
        ],
        "edges": [{"id": "e1", "source": "n_start", "target": "n_end"}],
    }


def _create_workflow(client: TestClient, name: str) -> str:
    """Create a workflow through the API and return its id."""
    response = client.post(
        "/api/workflows",
        json={"name": name, "description": "test", "dsl": _dsl()},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _publish(client: TestClient, workflow_id: str, **overrides) -> dict:
    payload = {
        "display_name": "Test Workflow",
        "description": "Published from tests",
        "category": "automation",
        "tags": ["ai", "test"],
        "version": "1.0.0",
        "changelog": "Initial release",
        "dependencies": {},
    }
    payload.update(overrides)
    response = client.post(
        f"/api/marketplace/publish?workflow_id={workflow_id}",
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_publish_workflow(client: TestClient) -> None:
    """Publishing a workflow creates an approved marketplace entry."""
    workflow_id = _create_workflow(client, "Test Workflow")
    data = _publish(client, workflow_id, display_name="Amazing Workflow")

    assert data["display_name"] == "Amazing Workflow"
    assert data["category"] == "automation"
    assert data["tags"] == ["ai", "test"]
    assert data["version"] == "1.0.0"
    assert data["downloads"] == 0
    assert data["rating"] == 0.0
    assert data["rating_count"] == 0
    assert data["status"] == "approved"
    assert data["author_name"] == "本地用户"


def test_list_marketplace_workflows(client: TestClient) -> None:
    """Browsing marketplace workflows returns a JSON list."""
    response = client.get("/api/marketplace/workflows")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_list_marketplace_workflows_with_filters(client: TestClient) -> None:
    """Category filtering narrows the result set."""
    workflow_id = _create_workflow(client, "Analytics Workflow")
    _publish(
        client,
        workflow_id,
        display_name="Analytics Tool",
        description="Data analytics workflow",
        category="analytics",
        tags=["data"],
    )

    response = client.get("/api/marketplace/workflows?category=analytics")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["category"] == "analytics"


def test_search_marketplace_workflows(client: TestClient) -> None:
    """Server-side search matches display name/description."""
    workflow_id = _create_workflow(client, "Search Me")
    _publish(client, workflow_id, display_name="Searchable Tool")

    hit = client.get("/api/marketplace/workflows?search=searchable")
    assert hit.status_code == 200
    assert len(hit.json()) == 1

    miss = client.get("/api/marketplace/workflows?search=nomatchxyz")
    assert miss.status_code == 200
    assert len(miss.json()) == 0


def test_search_escapes_wildcards(client: TestClient) -> None:
    """A literal % in the query must not match every row."""
    workflow_id = _create_workflow(client, "Wildcard % WF")
    _publish(client, workflow_id, display_name="Has 100% in name")

    # Literal % should match the row containing 100%
    hit = client.get("/api/marketplace/workflows?search=100%25")
    assert hit.status_code == 200
    assert len(hit.json()) == 1

    # A bare % query matches a literal percent, not everything
    bare = client.get("/api/marketplace/workflows?search=%25")
    assert bare.status_code == 200
    assert len(bare.json()) == 1


def test_get_marketplace_entry_by_workflow(client: TestClient) -> None:
    """Entry lookup by originating workflow id distinguishes published state."""
    # Unpublished workflow -> null
    workflow_id = _create_workflow(client, "Not Published")
    response = client.get(f"/api/marketplace/entry/{workflow_id}")
    assert response.status_code == 200
    assert response.json() is None

    # Published workflow -> entry
    published_id = _create_workflow(client, "Published WF")
    _publish(client, published_id, display_name="Published Entry")
    response = client.get(f"/api/marketplace/entry/{published_id}")
    assert response.status_code == 200
    assert response.json()["display_name"] == "Published Entry"


def test_update_published_workflow(client: TestClient) -> None:
    """The PUT endpoint updates metadata of an existing entry."""
    workflow_id = _create_workflow(client, "Updatable WF")
    data = _publish(client, workflow_id, version="1.0.0")
    marketplace_id = data["id"]

    response = client.put(
        f"/api/marketplace/publish/{marketplace_id}",
        json={
            "display_name": "Updated Name",
            "description": "Updated description",
            "category": "automation",
            "tags": ["ai"],
            "version": "2.0.0",
        },
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["display_name"] == "Updated Name"
    assert updated["version"] == "2.0.0"


def test_install_workflow(client: TestClient) -> None:
    """Installing clones the workflow and increments the download count."""
    workflow_id = _create_workflow(client, "Source Workflow")
    data = _publish(client, workflow_id, display_name="Installable Workflow")
    marketplace_id = data["id"]

    install_response = client.post(f"/api/marketplace/install/{marketplace_id}")
    assert install_response.status_code == 200, install_response.text
    install_data = install_response.json()
    cloned_id = install_data["workflow_id"]
    assert "message" in install_data

    # Cloned workflow exists with marketplace suffix
    verify = client.get(f"/api/workflows/{cloned_id}")
    assert verify.status_code == 200
    assert "(from marketplace)" in verify.json()["name"]

    # Download count incremented
    detail = client.get(f"/api/marketplace/workflows/{marketplace_id}")
    assert detail.status_code == 200
    assert detail.json()["downloads"] == 1


def test_create_and_update_review(client: TestClient) -> None:
    """Creating and updating a review keeps aggregate rating correct."""
    workflow_id = _create_workflow(client, "Reviewable WF")
    data = _publish(client, workflow_id, display_name="Review Test")
    marketplace_id = data["id"]

    # Create review
    create_response = client.post(
        f"/api/marketplace/workflows/{marketplace_id}/reviews",
        json={"rating": 3, "comment": "Okay"},
    )
    assert create_response.status_code == 200, create_response.text
    assert create_response.json()["rating"] == 3

    detail = client.get(f"/api/marketplace/workflows/{marketplace_id}").json()
    assert detail["rating"] == 3.0
    assert detail["rating_count"] == 1

    # Update review (same user)
    update_response = client.post(
        f"/api/marketplace/workflows/{marketplace_id}/reviews",
        json={"rating": 5, "comment": "Actually great!"},
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["rating"] == 5

    detail = client.get(f"/api/marketplace/workflows/{marketplace_id}").json()
    assert detail["rating"] == 5.0
    assert detail["rating_count"] == 1


def test_list_reviews(client: TestClient) -> None:
    """Listing reviews returns the created review."""
    workflow_id = _create_workflow(client, "List Reviews WF")
    data = _publish(client, workflow_id, display_name="Review List Test")
    marketplace_id = data["id"]

    client.post(
        f"/api/marketplace/workflows/{marketplace_id}/reviews",
        json={"rating": 4, "comment": "Good"},
    )

    list_response = client.get(f"/api/marketplace/workflows/{marketplace_id}/reviews")
    assert list_response.status_code == 200
    reviews = list_response.json()
    assert len(reviews) == 1
    assert reviews[0]["rating"] == 4
    assert reviews[0]["comment"] == "Good"


def test_validation_rejects_bad_input(client: TestClient) -> None:
    """Server-side validators reject bad tags and non-http icon URLs."""
    workflow_id = _create_workflow(client, "Validation WF")

    # Too many tags
    response = client.post(
        f"/api/marketplace/publish?workflow_id={workflow_id}",
        json={
            "display_name": "Bad Tags",
            "description": "test",
            "category": "automation",
            "tags": [f"t{i}" for i in range(11)],
            "version": "1.0.0",
        },
    )
    assert response.status_code == 422

    # javascript: icon URL
    response = client.post(
        f"/api/marketplace/publish?workflow_id={workflow_id}",
        json={
            "display_name": "Bad Icon",
            "description": "test",
            "category": "automation",
            "tags": [],
            "version": "1.0.0",
            "icon_url": "javascript:alert(1)",
        },
    )
    assert response.status_code == 422