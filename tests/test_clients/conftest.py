"""Client-specific test fixtures — raw API response objects and error responses."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_k8s_api_response() -> MagicMock:
    """Factory for a mock Kubernetes API response object."""
    response = MagicMock()
    response.status_code = 200
    return response


@pytest.fixture
def mock_k8s_api_error() -> MagicMock:
    """Factory for a mock Kubernetes API error response."""
    response = MagicMock()
    response.status = 503
    response.reason = "Service Unavailable"
    return response
