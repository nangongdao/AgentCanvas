"""Pytest configuration."""

import pytest


@pytest.fixture
def mock_api_key():
    """Provide a mock API key for testing."""
    return "test-api-key-123"


@pytest.fixture
def mock_base_url():
    """Provide a mock base URL for testing."""
    return "http://localhost:8000"
