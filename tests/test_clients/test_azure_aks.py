"""Tests for AzureAksClient: cluster info, node pool state, upgrade profile, activity log."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from platform_mcp_server.clients.azure_aks import AzureAksClient
from platform_mcp_server.config import CLUSTER_MAP


def _make_mock_pool(
    name: str = "userpool",
    count: int = 3,
    current_version: str = "1.29.8",
    target_version: str = "1.29.8",
    provisioning_state: str = "Succeeded",
) -> MagicMock:
    pool = MagicMock()
    pool.name = name
    pool.vm_size = "Standard_DS2_v2"
    pool.count = count
    pool.min_count = 1
    pool.max_count = 10
    pool.current_orchestrator_version = current_version
    pool.orchestrator_version = target_version
    pool.provisioning_state = provisioning_state
    pool.power_state.code = "Running"
    pool.os_type = "Linux"
    pool.mode = "User"
    return pool


@pytest.fixture
def client() -> AzureAksClient:
    return AzureAksClient(CLUSTER_MAP["prod-eastus"])


class TestGetClusterInfo:
    async def test_returns_cluster_version_and_pools(self, client: AzureAksClient) -> None:
        mock_container = MagicMock()
        cluster_mock = MagicMock()
        cluster_mock.kubernetes_version = "1.29.8"
        cluster_mock.provisioning_state = "Succeeded"
        cluster_mock.fqdn = "aks-prod.eastus.azmk8s.io"
        cluster_mock.agent_pool_profiles = [
            _make_mock_pool(name="systempool"),
            _make_mock_pool(name="userpool"),
        ]
        mock_container.managed_clusters.get.return_value = cluster_mock

        with patch.object(client, "_get_container_client", return_value=mock_container):
            info = await client.get_cluster_info()

        assert info["control_plane_version"] == "1.29.8"
        assert len(info["node_pools"]) == 2
        assert info["node_pools"][0]["name"] == "systempool"

    async def test_error_handling(self, client: AzureAksClient) -> None:
        mock_container = MagicMock()
        mock_container.managed_clusters.get.side_effect = Exception("Unauthorized")

        with (
            patch.object(client, "_get_container_client", return_value=mock_container),
            pytest.raises(Exception, match="Unauthorized"),
        ):
            await client.get_cluster_info()


class TestGetNodePoolState:
    async def test_returns_pool_details(self, client: AzureAksClient) -> None:
        mock_container = MagicMock()
        mock_container.agent_pools.get.return_value = _make_mock_pool(name="userpool", count=5)

        with patch.object(client, "_get_container_client", return_value=mock_container):
            state = await client.get_node_pool_state("userpool")

        assert state["name"] == "userpool"
        assert state["count"] == 5


class TestGetUpgradeProfile:
    async def test_returns_available_upgrades(self, client: AzureAksClient) -> None:
        mock_container = MagicMock()
        profile = MagicMock()

        # Control plane upgrades
        upgrade_1 = MagicMock()
        upgrade_1.kubernetes_version = "1.30.0"
        profile.control_plane_profile.kubernetes_version = "1.29.8"
        profile.control_plane_profile.upgrades = [upgrade_1]

        # Pool upgrades
        pool_profile = MagicMock()
        pool_profile.name = "userpool"
        pool_upgrade = MagicMock()
        pool_upgrade.kubernetes_version = "1.30.0"
        pool_profile.upgrades = [pool_upgrade]
        profile.agent_pool_profiles = [pool_profile]

        mock_container.managed_clusters.get_upgrade_profile.return_value = profile

        with patch.object(client, "_get_container_client", return_value=mock_container):
            result = await client.get_upgrade_profile()

        assert "1.30.0" in result["control_plane_upgrades"]
        assert "1.30.0" in result["pool_upgrades"]["userpool"]


class TestGetActivityLogUpgrades:
    async def test_returns_historical_records(self, client: AzureAksClient) -> None:
        mock_monitor = MagicMock()
        entry = MagicMock()
        entry.status.value = "Succeeded"
        entry.event_timestamp = datetime(2026, 2, 20, 12, 0, 0, tzinfo=UTC)
        entry.submission_timestamp = datetime(2026, 2, 20, 11, 0, 0, tzinfo=UTC)
        entry.operation_name.value = "Microsoft.ContainerService/managedClusters/write"
        entry.description = "Upgrade to 1.29.8"
        mock_monitor.activity_logs.list.return_value = [entry]

        with patch.object(client, "_get_monitor_client", return_value=mock_monitor):
            records = await client.get_activity_log_upgrades(count=5)

        assert len(records) == 1
        assert records[0]["duration_seconds"] == 3600.0  # 1 hour

    async def test_fewer_records_than_requested(self, client: AzureAksClient) -> None:
        mock_monitor = MagicMock()
        mock_monitor.activity_logs.list.return_value = []

        with patch.object(client, "_get_monitor_client", return_value=mock_monitor):
            records = await client.get_activity_log_upgrades(count=5)

        assert len(records) == 0

    async def test_partial_failure_handling(self, client: AzureAksClient) -> None:
        mock_monitor = MagicMock()
        mock_monitor.activity_logs.list.side_effect = Exception("Timeout")

        with (
            patch.object(client, "_get_monitor_client", return_value=mock_monitor),
            pytest.raises(Exception, match="Timeout"),
        ):
            await client.get_activity_log_upgrades()
