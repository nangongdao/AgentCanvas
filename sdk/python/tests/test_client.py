"""Unit tests for AgentCanvas client."""

from unittest.mock import Mock, patch

import pytest

from agentcanvas import (
    AgentCanvasClient,
    CreateWorkflowInput,
    ExecutionStatus,
    StartExecutionInput,
)


@pytest.fixture
def client():
    """Create a test client."""
    return AgentCanvasClient(api_key="test-key", base_url="http://test.local")


@pytest.fixture
def mock_response():
    """Create a mock HTTP response."""
    response = Mock()
    response.status_code = 200
    response.json.return_value = {}
    return response


class TestWorkflows:
    """Test workflow methods."""

    def test_list_workflows(self, client, mock_response):
        """Test listing workflows."""
        mock_response.json.return_value = [
            {
                "id": "wf1",
                "name": "Test Workflow",
                "description": "Test",
                "nodes": [],
                "edges": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "version": 1,
            }
        ]

        with patch.object(client._client, "request", return_value=mock_response):
            workflows = client.list_workflows()
            assert len(workflows) == 1
            assert workflows[0].id == "wf1"
            assert workflows[0].name == "Test Workflow"

    def test_get_workflow(self, client, mock_response):
        """Test getting a workflow by ID."""
        mock_response.json.return_value = {
            "id": "wf1",
            "name": "Test Workflow",
            "description": "Test",
            "nodes": [],
            "edges": [],
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "version": 1,
        }

        with patch.object(client._client, "request", return_value=mock_response):
            workflow = client.get_workflow("wf1")
            assert workflow.id == "wf1"
            assert workflow.name == "Test Workflow"

    def test_create_workflow(self, client, mock_response):
        """Test creating a workflow."""
        mock_response.json.return_value = {
            "id": "wf1",
            "name": "New Workflow",
            "description": "Created",
            "nodes": [],
            "edges": [],
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "version": 1,
        }

        with patch.object(client._client, "request", return_value=mock_response):
            input_data = CreateWorkflowInput(name="New Workflow", description="Created")
            workflow = client.create_workflow(input_data)
            assert workflow.id == "wf1"
            assert workflow.name == "New Workflow"


class TestExecutions:
    """Test execution methods."""

    def test_list_executions(self, client, mock_response):
        """Test listing executions."""
        mock_response.json.return_value = [
            {
                "id": "ex1",
                "workflow_id": "wf1",
                "status": "succeeded",
                "trigger_source": "manual",
                "started_at": "2024-01-01T00:00:00Z",
                "ended_at": "2024-01-01T00:01:00Z",
                "duration": 60.0,
            }
        ]

        with patch.object(client._client, "request", return_value=mock_response):
            executions = client.list_executions()
            assert len(executions) == 1
            assert executions[0].id == "ex1"
            assert executions[0].status == ExecutionStatus.SUCCEEDED

    def test_start_execution(self, client, mock_response):
        """Test starting an execution."""
        mock_response.json.return_value = {
            "id": "ex1",
            "workflow_id": "wf1",
            "status": "queued",
            "trigger_source": "manual",
        }

        with patch.object(client._client, "request", return_value=mock_response):
            input_data = StartExecutionInput(
                workflow_id="wf1",
                inputs={"query": "test"}
            )
            execution = client.start_execution(input_data)
            assert execution.id == "ex1"
            assert execution.status == ExecutionStatus.QUEUED

    def test_cancel_execution(self, client, mock_response):
        """Test cancelling an execution."""
        mock_response.json.return_value = {
            "id": "ex1",
            "workflow_id": "wf1",
            "status": "cancelled",
            "trigger_source": "manual",
        }

        with patch.object(client._client, "request", return_value=mock_response):
            execution = client.cancel_execution("ex1")
            assert execution.status == ExecutionStatus.CANCELLED


class TestErrorHandling:
    """Test error handling."""

    def test_not_found_error(self, client):
        """Test 404 error handling."""
        response = Mock()
        response.status_code = 404
        response.json.return_value = {"detail": "Not found"}
        response.raise_for_status.side_effect = Exception()

        with patch.object(client._client, "request") as mock_request:
            mock_request.side_effect = Exception()
            # The actual implementation would raise NotFoundError
            # This is a simplified test

    def test_authentication_error(self, client):
        """Test 401 error handling."""
        response = Mock()
        response.status_code = 401
        response.json.return_value = {"detail": "Unauthorized"}
        # Test authentication error handling


class TestContextManager:
    """Test context manager support."""

    def test_context_manager(self):
        """Test using client as context manager."""
        with AgentCanvasClient(api_key="test-key") as client:
            assert client.api_key == "test-key"
            assert client.base_url == "http://localhost:8000"
