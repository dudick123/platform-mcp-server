"""Shared test fixtures for all test modules."""

from __future__ import annotations

import textwrap
from typing import Any
from unittest.mock import MagicMock

import pytest

from platform_mcp_server.config import CLUSTER_MAP, ClusterConfig, load_cluster_map

_TEST_CLUSTERS_YAML = textwrap.dedent("""\
    clusters:
      dev-eastus:
        environment: dev
        region: eastus
        subscription_id: "<dev-subscription-id>"
        resource_group: rg-dev-eastus
        aks_cluster_name: aks-dev-eastus
        kubeconfig_context: aks-dev-eastus
      dev-westus2:
        environment: dev
        region: westus2
        subscription_id: "<dev-subscription-id>"
        resource_group: rg-dev-westus2
        aks_cluster_name: aks-dev-westus2
        kubeconfig_context: aks-dev-westus2
      staging-eastus:
        environment: staging
        region: eastus
        subscription_id: "<staging-subscription-id>"
        resource_group: rg-staging-eastus
        aks_cluster_name: aks-staging-eastus
        kubeconfig_context: aks-staging-eastus
      staging-westus2:
        environment: staging
        region: westus2
        subscription_id: "<staging-subscription-id>"
        resource_group: rg-staging-westus2
        aks_cluster_name: aks-staging-westus2
        kubeconfig_context: aks-staging-westus2
      prod-eastus:
        environment: prod
        region: eastus
        subscription_id: "<prod-subscription-id>"
        resource_group: rg-prod-eastus
        aks_cluster_name: aks-prod-eastus
        kubeconfig_context: aks-prod-eastus
      prod-westus2:
        environment: prod
        region: westus2
        subscription_id: "<prod-subscription-id>"
        resource_group: rg-prod-westus2
        aks_cluster_name: aks-prod-westus2
        kubeconfig_context: aks-prod-westus2
""")


@pytest.fixture(autouse=True, scope="session")
def _load_test_clusters(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Write a test clusters.yaml and load it before any tests run."""
    config_path = tmp_path_factory.mktemp("config") / "clusters.yaml"
    config_path.write_text(_TEST_CLUSTERS_YAML)
    import os

    os.environ["PLATFORM_MCP_CLUSTERS"] = str(config_path)
    load_cluster_map()


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
