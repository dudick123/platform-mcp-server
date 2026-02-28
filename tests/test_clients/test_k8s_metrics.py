"""Tests for K8sMetricsClient: node metrics retrieval, graceful degradation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from platform_mcp_server.clients.k8s_metrics import K8sMetricsClient
from platform_mcp_server.config import CLUSTER_MAP


@pytest.fixture
def client() -> K8sMetricsClient:
    return K8sMetricsClient(CLUSTER_MAP["prod-eastus"])


class TestGetNodeMetrics:
    async def test_returns_metrics_for_all_nodes(self, client: K8sMetricsClient) -> None:
        mock_api = MagicMock()
        mock_api.list_cluster_custom_object.return_value = {
            "items": [
                {"metadata": {"name": "node-1"}, "usage": {"cpu": "2500m", "memory": "8Gi"}},
                {"metadata": {"name": "node-2"}, "usage": {"cpu": "1200m", "memory": "4Gi"}},
            ]
        }

        with patch.object(client, "_get_api", return_value=mock_api):
            metrics = await client.get_node_metrics()

        assert len(metrics) == 2
        assert metrics[0]["name"] == "node-1"
        assert metrics[0]["cpu_usage"] == "2500m"
        assert metrics[0]["memory_usage"] == "8Gi"

    async def test_metrics_server_unavailable_raises(self, client: K8sMetricsClient) -> None:
        mock_api = MagicMock()
        mock_api.list_cluster_custom_object.side_effect = Exception("metrics-server not available")

        with patch.object(client, "_get_api", return_value=mock_api), pytest.raises(Exception, match="metrics-server"):
            await client.get_node_metrics()

    async def test_empty_items_returns_empty_list(self, client: K8sMetricsClient) -> None:
        mock_api = MagicMock()
        mock_api.list_cluster_custom_object.return_value = {"items": []}

        with patch.object(client, "_get_api", return_value=mock_api):
            metrics = await client.get_node_metrics()

        assert metrics == []

    async def test_calls_correct_api_group(self, client: K8sMetricsClient) -> None:
        mock_api = MagicMock()
        mock_api.list_cluster_custom_object.return_value = {"items": []}

        with patch.object(client, "_get_api", return_value=mock_api):
            await client.get_node_metrics()

        mock_api.list_cluster_custom_object.assert_called_once_with(
            group="metrics.k8s.io",
            version="v1beta1",
            plural="nodes",
        )
