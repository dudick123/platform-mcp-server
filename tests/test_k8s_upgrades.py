"""Tests for get_kubernetes_upgrade_status tool handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from platform_mcp_server.tools.k8s_upgrades import get_upgrade_status_handler


def _make_cluster_info(
    cp_version: str = "1.29.8",
    pools: list | None = None,
) -> dict:
    default_pools = [
        {
            "name": "systempool",
            "vm_size": "Standard_DS2_v2",
            "count": 3,
            "min_count": 3,
            "max_count": 5,
            "current_version": cp_version,
            "target_version": cp_version,
            "provisioning_state": "Succeeded",
            "power_state": "Running",
            "os_type": "Linux",
            "mode": "System",
        },
    ]
    return {
        "control_plane_version": cp_version,
        "provisioning_state": "Succeeded",
        "node_pools": pools if pools is not None else default_pools,
        "fqdn": "aks-test.eastus.azmk8s.io",
    }


def _make_upgrade_profile(
    cp_version: str = "1.29.8",
    cp_upgrades: list[str] | None = None,
    pool_upgrades: dict | None = None,
) -> dict:
    return {
        "control_plane_version": cp_version,
        "control_plane_upgrades": cp_upgrades or ["1.30.0"],
        "pool_upgrades": pool_upgrades or {"systempool": ["1.30.0"]},
    }


class TestGetUpgradeStatus:
    async def test_happy_path_version_data(self) -> None:
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = _make_cluster_info()
        mock_aks.get_upgrade_profile.return_value = _make_upgrade_profile()

        with patch("platform_mcp_server.tools.k8s_upgrades.AzureAksClient", return_value=mock_aks):
            result = await get_upgrade_status_handler("prod-eastus")

        assert result.control_plane_version == "1.29.8"
        assert "1.30.0" in result.available_upgrades
        assert result.upgrade_active is False

    async def test_active_upgrade_detected(self) -> None:
        pool = {
            "name": "userpool",
            "vm_size": "Standard_DS2_v2",
            "count": 5,
            "min_count": 3,
            "max_count": 10,
            "current_version": "1.29.8",
            "target_version": "1.30.0",
            "provisioning_state": "Upgrading",
            "power_state": "Running",
            "os_type": "Linux",
            "mode": "User",
        }
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = _make_cluster_info(pools=[pool])
        mock_aks.get_upgrade_profile.return_value = _make_upgrade_profile()

        with patch("platform_mcp_server.tools.k8s_upgrades.AzureAksClient", return_value=mock_aks):
            result = await get_upgrade_status_handler("prod-eastus")

        assert result.upgrade_active is True
        assert any(np.upgrading for np in result.node_pools)

    async def test_cluster_all_fan_out(self) -> None:
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = _make_cluster_info()
        mock_aks.get_upgrade_profile.return_value = _make_upgrade_profile()

        with patch("platform_mcp_server.tools.k8s_upgrades.AzureAksClient", return_value=mock_aks):
            from platform_mcp_server.tools.k8s_upgrades import get_upgrade_status_all

            results = await get_upgrade_status_all()

        assert len(results) == 6

    async def test_partial_failure_returns_error(self) -> None:
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.side_effect = Exception("AKS API unreachable")
        mock_aks.get_upgrade_profile.return_value = _make_upgrade_profile()

        with patch("platform_mcp_server.tools.k8s_upgrades.AzureAksClient", return_value=mock_aks):
            result = await get_upgrade_status_handler("prod-eastus")

        assert len(result.errors) > 0
        assert result.errors[0].source == "aks-api"
