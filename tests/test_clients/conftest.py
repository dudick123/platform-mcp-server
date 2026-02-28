# Note 1: conftest.py is a special pytest file that is automatically discovered and loaded
# by pytest before any test in the same directory (or subdirectories) runs. You never
# import from conftest.py explicitly — pytest handles injection via fixture names.
"""Client-specific test fixtures — raw API response objects and error responses."""

# Note 2: The `from __future__ import annotations` import enables PEP 563 postponed
# evaluation of annotations. This lets you use type hints (like `MagicMock`) in function
# signatures without requiring the annotated types to be fully resolved at import time.
# It is particularly useful when type hints reference classes defined later in the file.
from __future__ import annotations

# Note 3: MagicMock is the go-to mock class in Python's standard library `unittest.mock`
# module. It auto-creates attributes and methods on demand, so you can do things like
# `mock.status_code = 200` or chain calls like `mock.foo.bar()` without pre-defining
# the full object shape. This makes it ideal for mocking complex SDK response objects
# whose exact structure you only care about partially.
from unittest.mock import MagicMock

# Note 4: pytest must be imported in conftest.py so that the @pytest.fixture decorator
# is available. Even though pytest discovers conftest.py automatically, fixtures still
# need the decorator to be recognized as injectable fixtures rather than plain functions.
import pytest


# Note 5: The @pytest.fixture decorator marks a function as a pytest fixture. Fixtures
# are pytest's dependency-injection mechanism: any test function that declares a parameter
# with the same name as a fixture will automatically receive the return value of that
# fixture when the test runs. No setup/teardown boilerplate required in the test itself.
@pytest.fixture
def mock_k8s_api_response() -> MagicMock:
    """Factory for a mock Kubernetes API response object."""
    # Note 6: MagicMock() with no arguments creates a blank mock object. Every attribute
    # access on a MagicMock returns another MagicMock, and every method call returns a
    # MagicMock too — unless you explicitly set a value. Here we set `status_code = 200`
    # to simulate a successful HTTP 200 response from the Kubernetes API server.
    response = MagicMock()
    response.status_code = 200
    return response


# Note 7: A second fixture is defined for the error case. Separating success and error
# fixtures follows the "one concern per fixture" principle: each fixture communicates a
# clear scenario at a glance without burying conditional logic inside a single fixture.
@pytest.fixture
def mock_k8s_api_error() -> MagicMock:
    """Factory for a mock Kubernetes API error response."""
    # Note 8: The Kubernetes Python client uses `.status` (an integer) and `.reason`
    # (a string) on its ApiException objects, which is why we set those fields rather
    # than `.status_code`. Using the correct field names is important so that production
    # code that reads `response.status` during error handling will work correctly when
    # given this mock. 503 "Service Unavailable" is chosen because it is the most common
    # transient error when a cluster control plane is temporarily unreachable.
    response = MagicMock()
    response.status = 503
    response.reason = "Service Unavailable"
    return response
