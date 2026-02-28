"""Tests for get_upgrade_progress tool handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from platform_mcp_server.tools.upgrade_progress import get_upgrade_progress_handler


def _make_pool_info(
    name: str = "userpool",
    current_version: str = "1.29.8",
    target_version: str = "1.30.0",
    provisioning_state: str = "Upgrading",
) -> dict:
    return {
        "name": name,
        "vm_size": "Standard_DS2_v2",
        "count": 5,
        "min_count": 3,
        "max_count": 10,
        "current_version": current_version,
        "target_version": target_version,
        "provisioning_state": provisioning_state,
        "power_state": "Running",
        "os_type": "Linux",
        "mode": "User",
    }


def _make_node(
    name: str,
    pool: str = "userpool",
    version: str = "v1.29.8",
    unschedulable: bool = False,
) -> dict:
    return {
        "name": name,
        "pool": pool,
        "version": version,
        "unschedulable": unschedulable,
        "allocatable_cpu": "4000m",
        "allocatable_memory": "16Gi",
        "conditions": {"Ready": "True"},
        "labels": {"agentpool": pool},
    }


def _make_event(node_name: str, reason: str, timestamp: str = "2026-02-28T12:00:00+00:00") -> dict:
    return {
        "reason": reason,
        "node_name": node_name,
        "message": f"{reason} event for {node_name}",
        "timestamp": timestamp,
        "count": 1,
    }


class TestGetUpgradeProgress:
    async def test_no_upgrade_in_progress(self) -> None:
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.29.8",
            "provisioning_state": "Succeeded",
            "node_pools": [
                _make_pool_info(provisioning_state="Succeeded", current_version="1.29.8", target_version="1.29.8")
            ],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        mock_events = AsyncMock()
        mock_policy = AsyncMock()

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        assert result.upgrade_in_progress is False

    async def test_node_classified_as_upgraded(self) -> None:
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_pool_info()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", version="v1.30.0")]
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = [
            _make_event("node-1", "NodeUpgrade", "2026-02-28T11:50:00+00:00"),
            _make_event("node-1", "NodeReady", "2026-02-28T11:55:00+00:00"),
        ]
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        assert result.upgrade_in_progress is True
        assert len(result.nodes) == 1
        assert result.nodes[0].state == "upgraded"

    async def test_node_classified_as_cordoned(self) -> None:
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_pool_info()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", version="v1.29.8", unschedulable=True)]
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []  # No NodeUpgrade event yet
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        assert result.nodes[0].state == "cordoned"

    async def test_node_classified_as_pending(self) -> None:
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_pool_info()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", version="v1.29.8", unschedulable=False)]
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = []
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = []
        mock_policy.evaluate_pdb_satisfiability.return_value = []

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        assert result.nodes[0].state == "pending"

    async def test_pdb_blocked_includes_reference(self) -> None:
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.30.0",
            "provisioning_state": "Succeeded",
            "node_pools": [_make_pool_info()],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", version="v1.29.8", unschedulable=True)]
        mock_events = AsyncMock()
        mock_events.get_node_events.return_value = [
            _make_event("node-1", "NodeUpgrade", "2026-02-28T11:50:00+00:00"),
        ]
        mock_policy = AsyncMock()
        mock_policy.get_pdbs.return_value = [
            {
                "name": "block-pdb",
                "namespace": "ns1",
                "max_unavailable": 0,
                "disruptions_allowed": 0,
                "selector": {},
                "current_healthy": 3,
                "desired_healthy": 3,
                "expected_pods": 3,
            }
        ]
        mock_policy.evaluate_pdb_satisfiability.return_value = [
            {"name": "block-pdb", "namespace": "ns1", "block_reason": "maxUnavailable=0"}
        ]

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            result = await get_upgrade_progress_handler("prod-eastus")

        assert result.nodes[0].state == "pdb_blocked"
        assert result.nodes[0].blocking_pdb == "block-pdb"

    async def test_cluster_all_fan_out(self) -> None:
        mock_aks = AsyncMock()
        mock_aks.get_cluster_info.return_value = {
            "control_plane_version": "1.29.8",
            "provisioning_state": "Succeeded",
            "node_pools": [
                _make_pool_info(provisioning_state="Succeeded", current_version="1.29.8", target_version="1.29.8")
            ],
            "fqdn": "test.eastus.azmk8s.io",
        }
        mock_core = AsyncMock()
        mock_events = AsyncMock()
        mock_policy = AsyncMock()

        with (
            patch("platform_mcp_server.tools.upgrade_progress.AzureAksClient", return_value=mock_aks),
            patch("platform_mcp_server.tools.upgrade_progress.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.upgrade_progress.K8sEventsClient", return_value=mock_events),
            patch("platform_mcp_server.tools.upgrade_progress.K8sPolicyClient", return_value=mock_policy),
        ):
            from platform_mcp_server.tools.upgrade_progress import get_upgrade_progress_all

            results = await get_upgrade_progress_all()

        assert len(results) == 6
