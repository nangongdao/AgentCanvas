"""Main client class for AgentCanvas API."""

from __future__ import annotations

from typing import Any, Optional

import httpx

from agentcanvas.exceptions import (
    AgentCanvasError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
    RequestTimeoutError,
    ServerError,
    ValidationError,
)
from agentcanvas.types import (
    CreateProviderInput,
    CreateWorkflowInput,
    Execution,
    PaginationParams,
    Provider,
    StartExecutionInput,
    UpdateProviderInput,
    UpdateWorkflowInput,
    Workflow,
)


class AgentCanvasClient:
    """Main client for interacting with AgentCanvas API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "http://localhost:8000",
        timeout: float = 30.0,
    ) -> None:
        """Initialize the AgentCanvas client.

        Args:
            api_key: API authentication key.
            base_url: Base URL of the AgentCanvas API.
            timeout: Request timeout in seconds.
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    def __enter__(self) -> AgentCanvasClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def _handle_error(self, response: httpx.Response) -> None:
        """Handle HTTP error responses.

        Args:
            response: The HTTP response object.

        Raises:
            AgentCanvasError: Appropriate exception based on status code.
        """
        status_code = response.status_code
        try:
            error_data = response.json()
            message = error_data.get("detail", response.text)
        except (ValueError, KeyError):
            message = response.text or response.reason_phrase

        if status_code == 401:
            raise AuthenticationError(message, status_code)
        if status_code == 404:
            raise NotFoundError(message, status_code)
        if status_code == 422:
            raise ValidationError(message, status_code)
        if status_code == 429:
            raise RateLimitError(message, status_code)
        if status_code >= 500:
            raise ServerError(message, status_code)
        raise AgentCanvasError(message, status_code)

    def _request(
        self,
        method: str,
        endpoint: str,
        json: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> Any:
        """Make an HTTP request to the API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            endpoint: API endpoint path.
            json: JSON request body.
            params: Query parameters.

        Returns:
            Response data as JSON.

        Raises:
            TimeoutError: If the request times out.
            AgentCanvasError: For other errors.
        """
        try:
            response = self._client.request(
                method=method,
                url=endpoint,
                json=json,
                params=params,
            )
            response.raise_for_status()
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        except httpx.TimeoutException as e:
            raise RequestTimeoutError(f"Request timeout: {e}") from e
        except httpx.HTTPStatusError as e:
            self._handle_error(e.response)
        except httpx.RequestError as e:
            raise AgentCanvasError(f"Request failed: {e}") from e

    # ==================== Workflow Methods ====================

    def list_workflows(self, params: Optional[PaginationParams] = None) -> list[Workflow]:
        """List all workflows with optional pagination.

        Args:
            params: Pagination parameters (skip, limit).

        Returns:
            List of workflows.
        """
        query_params = {}
        if params:
            if params.skip is not None:
                query_params["skip"] = params.skip
            if params.limit is not None:
                query_params["limit"] = params.limit

        data = self._request("GET", "/api/workflows", params=query_params)
        return [Workflow(**w) for w in data]

    def get_workflow(self, workflow_id: str) -> Workflow:
        """Get a specific workflow by ID.

        Args:
            workflow_id: The workflow ID.

        Returns:
            The workflow object.
        """
        data = self._request("GET", f"/api/workflows/{workflow_id}")
        return Workflow(**data)

    def create_workflow(self, input_data: CreateWorkflowInput) -> Workflow:
        """Create a new workflow.

        Args:
            input_data: Workflow creation input.

        Returns:
            The created workflow.
        """
        data = self._request("POST", "/api/workflows", json=input_data.model_dump())
        return Workflow(**data)

    def update_workflow(
        self, workflow_id: str, input_data: UpdateWorkflowInput,
    ) -> Workflow:
        """Update an existing workflow.

        Args:
            workflow_id: The workflow ID.
            input_data: Workflow update input.

        Returns:
            The updated workflow.
        """
        data = self._request(
            "PUT",
            f"/api/workflows/{workflow_id}",
            json=input_data.model_dump(exclude_none=True),
        )
        return Workflow(**data)

    def delete_workflow(self, workflow_id: str) -> None:
        """Delete a workflow.

        Args:
            workflow_id: The workflow ID.
        """
        self._request("DELETE", f"/api/workflows/{workflow_id}")

    # ==================== Execution Methods ====================

    def list_executions(self, params: Optional[PaginationParams] = None) -> list[Execution]:
        """List all executions with optional pagination.

        Args:
            params: Pagination parameters (skip, limit).

        Returns:
            List of executions.
        """
        query_params = {}
        if params:
            if params.skip is not None:
                query_params["skip"] = params.skip
            if params.limit is not None:
                query_params["limit"] = params.limit

        data = self._request("GET", "/api/executions", params=query_params)
        return [Execution(**e) for e in data]

    def get_execution(self, execution_id: str) -> Execution:
        """Get a specific execution by ID.

        Args:
            execution_id: The execution ID.

        Returns:
            The execution object.
        """
        data = self._request("GET", f"/api/executions/{execution_id}")
        return Execution(**data)

    def start_execution(self, input_data: StartExecutionInput) -> Execution:
        """Start a new workflow execution.

        Args:
            input_data: Execution start input.

        Returns:
            The created execution.
        """
        data = self._request("POST", "/api/executions", json=input_data.model_dump())
        return Execution(**data)

    def cancel_execution(self, execution_id: str) -> Execution:
        """Cancel a running execution.

        Args:
            execution_id: The execution ID.

        Returns:
            The cancelled execution.
        """
        data = self._request("POST", f"/api/executions/{execution_id}/cancel")
        return Execution(**data)

    # ==================== Provider Methods ====================

    def list_providers(self) -> list[Provider]:
        """List all LLM providers.

        Returns:
            List of providers.
        """
        data = self._request("GET", "/api/providers")
        return [Provider(**p) for p in data]

    def get_provider(self, provider_id: str) -> Provider:
        """Get a specific provider by ID.

        Args:
            provider_id: The provider ID.

        Returns:
            The provider object.
        """
        data = self._request("GET", f"/api/providers/{provider_id}")
        return Provider(**data)

    def create_provider(self, input_data: CreateProviderInput) -> Provider:
        """Create a new LLM provider.

        Args:
            input_data: Provider creation input.

        Returns:
            The created provider.
        """
        data = self._request("POST", "/api/providers", json=input_data.model_dump())
        return Provider(**data)

    def update_provider(
        self, provider_id: str, input_data: UpdateProviderInput,
    ) -> Provider:
        """Update an existing provider.

        Args:
            provider_id: The provider ID.
            input_data: Provider update input.

        Returns:
            The updated provider.
        """
        data = self._request(
            "PUT",
            f"/api/providers/{provider_id}",
            json=input_data.model_dump(exclude_none=True),
        )
        return Provider(**data)

    def delete_provider(self, provider_id: str) -> None:
        """Delete a provider.

        Args:
            provider_id: The provider ID.
        """
        self._request("DELETE", f"/api/providers/{provider_id}")
