"""Shared test fixtures for all test modules."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from platform_mcp_server.config import CLUSTER_MAP, ClusterConfig


@pytest.fixture
def test_cluster_config() -> dict[str, ClusterConfig]:
    """Return the full cluster config mapping for test use."""
    return CLUSTER_MAP


@pytest.fixture
def mock_k8s_core_client() -> MagicMock:
    """Factory for a mock Kubernetes CoreV1Api client."""
    return MagicMock()


@pytest.fixture
def mock_k8s_custom_objects_client() -> MagicMock:
    """Factory for a mock Kubernetes CustomObjectsApi client (metrics)."""
    return MagicMock()


@pytest.fixture
def mock_k8s_policy_client() -> MagicMock:
    """Factory for a mock Kubernetes PolicyV1Api client."""
    return MagicMock()


@pytest.fixture
def mock_azure_container_client() -> MagicMock:
    """Factory for a mock Azure ContainerServiceClient."""
    return MagicMock()


@pytest.fixture
def mock_azure_monitor_client() -> MagicMock:
    """Factory for a mock Azure MonitorManagementClient."""
    return MagicMock()


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
    node_labels = {"agentpool": pool}
    if labels:
        node_labels.update(labels)

    return {
        "metadata": {
            "name": name,
            "labels": node_labels,
        },
        "spec": {
            "unschedulable": unschedulable,
        },
        "status": {
            "node_info": {"kubelet_version": f"v{version}"},
            "allocatable": {
                "cpu": cpu_allocatable,
                "memory": memory_allocatable,
            },
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
            "container_statuses": container_statuses or [],
        },
    }
    if reason:
        pod["status"]["reason"] = reason
    return pod
