"""Shared test fixtures for all test modules."""

# Note 1: conftest.py is a special pytest file — pytest automatically discovers and
# loads it before any tests run in the same directory or subdirectories. You never
# import from conftest.py directly; pytest injects its fixtures by name matching.

# Note 2: `from __future__ import annotations` enables PEP 563 postponed evaluation
# of type annotations. This lets you use newer type-hint syntax (e.g., `X | None`)
# even on Python 3.9, since annotations are stored as strings rather than evaluated
# eagerly at class-definition time.
from __future__ import annotations

# Note 3: `Any` is imported for use in the helper factory functions below. Using
# `dict[str, Any]` as the return type is intentional: the mock node/pod dicts mimic
# the structure of kubernetes-client Python objects, which return heterogeneous nested
# dicts whose leaf types are mixed (str, bool, int, list, etc.).
from typing import Any

# Note 4: MagicMock is the most versatile mock class in Python's standard library.
# Unlike a plain Mock, MagicMock pre-configures magic/dunder methods (__len__,
# __iter__, __enter__, __exit__, etc.) so it can stand in for objects that use
# Python protocols (context managers, iterables, etc.) without extra setup.
from unittest.mock import MagicMock

import pytest

from platform_mcp_server.config import CLUSTER_MAP, ClusterConfig


# Note 5: The `@pytest.fixture` decorator registers a function as a pytest fixture.
# When a test function declares a parameter whose name matches a fixture name, pytest
# automatically calls the fixture and passes its return value. This is called
# dependency injection — tests never construct their own dependencies.
@pytest.fixture
def test_cluster_config() -> dict[str, ClusterConfig]:
    """Return the full cluster config mapping for test use."""
    # Note 6: Returning the real CLUSTER_MAP (not a copy) is intentional here.
    # Because ClusterConfig objects are frozen dataclasses (immutable), tests cannot
    # accidentally mutate shared state. If they were mutable, returning a deep copy
    # would be safer to preserve test isolation.
    return CLUSTER_MAP


@pytest.fixture
def mock_k8s_core_client() -> MagicMock:
    """Factory for a mock Kubernetes CoreV1Api client."""
    # Note 7: Returning a bare MagicMock() means every attribute access and method
    # call on the returned object automatically creates a new child MagicMock. This
    # "autospec-free" style is fast to set up but does not validate that methods you
    # call actually exist on the real CoreV1Api. For stricter tests, use
    # `unittest.mock.create_autospec(CoreV1Api)` instead.
    return MagicMock()


@pytest.fixture
def mock_k8s_custom_objects_client() -> MagicMock:
    """Factory for a mock Kubernetes CustomObjectsApi client (metrics)."""
    # Note 8: A separate fixture is created for each Kubernetes API client type
    # (Core, CustomObjects, Policy). This mirrors the real application code, which
    # creates distinct client instances per API group. Having separate fixtures lets
    # individual tests configure only the mock they need (e.g., make only the metrics
    # client raise an exception) without affecting the other clients.
    return MagicMock()


@pytest.fixture
def mock_k8s_policy_client() -> MagicMock:
    """Factory for a mock Kubernetes PolicyV1Api client."""
    return MagicMock()


@pytest.fixture
def mock_azure_container_client() -> MagicMock:
    """Factory for a mock Azure ContainerServiceClient."""
    # Note 9: Azure SDK clients are heavy objects that require live credentials and
    # network access. By mocking them at the fixture level, tests remain fast,
    # deterministic, and runnable in CI environments that have no Azure access.
    return MagicMock()


@pytest.fixture
def mock_azure_monitor_client() -> MagicMock:
    """Factory for a mock Azure MonitorManagementClient."""
    return MagicMock()


# Note 10: `make_node` and `make_pod` are plain helper functions, NOT fixtures.
# They are defined as module-level functions so tests can call them directly with
# custom arguments to build specific scenarios. If they were fixtures, pytest would
# only let you pass them as test parameters, not call them mid-test with varying
# arguments to build lists of test data.
def make_node(
    name: str = "aks-userpool-00000001",
    pool: str = "userpool",
    version: str = "1.29.8",
    unschedulable: bool = False,
    cpu_allocatable: str = "4",
    memory_allocatable: str = "16Gi",
    cpu_requests: str = "2",
    memory_requests: str = "8Gi",
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a mock node dict for test fixtures."""
    # Note 11: All parameters have sensible defaults so callers only need to supply
    # the fields relevant to the behavior under test. This "keyword-argument with
    # defaults" pattern is sometimes called a "test data builder" — it minimises noise
    # in individual tests while still allowing full customisation.
    node_labels = {"agentpool": pool}
    if labels:
        # Note 12: Extra labels are merged on top of the mandatory `agentpool` label.
        # Real AKS nodes carry many labels (topology, OS, SKU, etc.). Tests that only
        # care about pool membership can ignore the `labels` parameter entirely.
        node_labels.update(labels)

    return {
        "metadata": {
            "name": name,
            "labels": node_labels,
        },
        "spec": {
            # Note 13: `unschedulable: False` is the normal state. Setting it to True
            # simulates a node that has been cordoned (kubectl cordon), which is a key
            # intermediate state during Kubernetes node upgrades and drain sequences.
            "unschedulable": unschedulable,
        },
        "status": {
            "node_info": {"kubelet_version": f"v{version}"},
            # Note 14: `allocatable` reflects capacity after subtracting OS/daemon
            # overhead from total capacity. Pressure calculations compare pod request
            # sums against allocatable, not raw capacity, matching how the Kubernetes
            # scheduler itself makes scheduling decisions.
            "allocatable": {
                "cpu": cpu_allocatable,
                "memory": memory_allocatable,
            },
            # Note 15: The conditions list uses a single "Ready: True" entry. Real
            # nodes have many conditions (MemoryPressure, DiskPressure, PIDPressure,
            # etc.), but tests only need the Ready condition to exercise the health
            # check logic. Keeping the fixture minimal reduces coupling between tests
            # and the exact condition schema.
            "conditions": [
                {"type": "Ready", "status": "True"},
            ],
        },
    }


def make_pod(
    name: str = "test-pod-abc123",
    namespace: str = "default",
    phase: str = "Running",
    node_name: str = "aks-userpool-00000001",
    restart_count: int = 0,
    reason: str | None = None,
    container_statuses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a mock pod dict for test fixtures."""
    # Note 16: `pod` is typed as `dict[str, Any]` rather than a more specific type
    # because we conditionally add the `reason` key below. Using `Any` as the value
    # type avoids mypy complaints about assigning to a non-existent key in a
    # TypedDict while still providing enough type safety for test code.
    pod: dict[str, Any] = {
        "metadata": {
            "name": name,
            "namespace": namespace,
        },
        "spec": {
            "node_name": node_name,
        },
        "status": {
            "phase": phase,
            # Note 17: `container_statuses or []` is the idiomatic Python pattern for
            # providing a mutable default. Using a bare `[]` as a default argument is
            # a classic Python gotcha — all callers would share the same list object.
            # Using `None` as the default and substituting `[]` inside the function
            # is the correct fix.
            "container_statuses": container_statuses or [],
        },
    }
    if reason:
        # Note 18: `reason` is only added to the dict when it has a value. This
        # mirrors the Kubernetes API, which omits optional fields entirely from
        # responses rather than including them as null. Tests that assert on the
        # absence of a key benefit from this conditional insertion pattern.
        pod["status"]["reason"] = reason
    return pod
