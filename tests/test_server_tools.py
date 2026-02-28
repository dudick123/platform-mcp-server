"""Integration tests for server.py tool wrappers — single cluster and fan-out."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from platform_mcp_server.models import (
    NodePoolPressureOutput,
    NodePoolResult,
    NodePoolVersionInfo,
    PdbCheckOutput,
    PodDetail,
    PodHealthOutput,
    UpgradeDurationOutput,
    UpgradeProgressOutput,
    UpgradeStatusOutput,
)
from platform_mcp_server.server import (
    check_node_pool_pressure,
    check_pdb_upgrade_risk,
    get_kubernetes_upgrade_status,
    get_pod_health,
    get_upgrade_duration_metrics,
    get_upgrade_progress,
)


def _pressure_output(cluster: str = "prod-eastus") -> NodePoolPressureOutput:
    return NodePoolPressureOutput(
        cluster=cluster,
        pools=[
            NodePoolResult(
                pool_name="userpool",
                cpu_requests_percent=50.0,
                memory_requests_percent=40.0,
                pending_pods=0,
                ready_nodes=3,
                max_nodes=10,
                pressure_level="ok",
            )
        ],
        summary="ok",
        timestamp="2026-02-28T12:00:00+00:00",
        errors=[],
    )


def _pod_health_output(cluster: str = "prod-eastus") -> PodHealthOutput:
    return PodHealthOutput(
        cluster=cluster,
        pods=[
            PodDetail(
                name="pod-1",
                namespace="default",
                phase="Pending",
                node_name="node-1",
                failure_category="scheduling",
            )
        ],
        groups={"scheduling": 1},
        total_matching=1,
        truncated=False,
        summary="1 unhealthy pod",
        timestamp="2026-02-28T12:00:00+00:00",
        errors=[],
    )


def _upgrade_status_output(cluster: str = "prod-eastus") -> UpgradeStatusOutput:
    return UpgradeStatusOutput(
        cluster=cluster,
        control_plane_version="1.29.8",
        node_pools=[
            NodePoolVersionInfo(
                pool_name="systempool",
                current_version="1.29.8",
                target_version="1.29.8",
                upgrading=False,
            )
        ],
        available_upgrades=["1.30.0"],
        upgrade_active=False,
        summary="1.29.8",
        timestamp="2026-02-28T12:00:00+00:00",
        errors=[],
    )


def _upgrade_progress_output(cluster: str = "prod-eastus") -> UpgradeProgressOutput:
    return UpgradeProgressOutput(
        cluster=cluster,
        upgrade_in_progress=False,
        nodes=[],
        summary="No upgrade",
        timestamp="2026-02-28T12:00:00+00:00",
        errors=[],
    )


def _upgrade_metrics_output(cluster: str = "prod-eastus") -> UpgradeDurationOutput:
    return UpgradeDurationOutput(
        cluster=cluster,
        node_pool="userpool",
        historical=[],
        summary="No active upgrade",
        timestamp="2026-02-28T12:00:00+00:00",
        errors=[],
    )


def _pdb_check_output(cluster: str = "prod-eastus") -> PdbCheckOutput:
    return PdbCheckOutput(
        cluster=cluster,
        mode="preflight",
        risks=[],
        summary="No PDB risks",
        timestamp="2026-02-28T12:00:00+00:00",
        errors=[],
    )


class TestCheckNodePoolPressure:
    async def test_single_cluster(self) -> None:
        with patch(
            "platform_mcp_server.server.check_node_pool_pressure_handler",
            new_callable=AsyncMock,
            return_value=_pressure_output(),
        ):
            result = await check_node_pool_pressure("prod-eastus")
        data = json.loads(result)
        assert data["cluster"] == "prod-eastus"

    async def test_all_clusters(self) -> None:
        outputs = [_pressure_output(f"cluster-{i}") for i in range(6)]
        with patch(
            "platform_mcp_server.server.check_node_pool_pressure_all",
            new_callable=AsyncMock,
            return_value=outputs,
        ):
            result = await check_node_pool_pressure("all")
        assert "cluster-0" in result
        assert "cluster-5" in result

    async def test_error_propagates(self) -> None:
        with (
            patch(
                "platform_mcp_server.server.check_node_pool_pressure_handler",
                new_callable=AsyncMock,
                side_effect=ValueError("test error"),
            ),
            pytest.raises(RuntimeError, match="test error"),
        ):
            await check_node_pool_pressure("prod-eastus")


class TestGetPodHealth:
    async def test_single_cluster(self) -> None:
        with patch(
            "platform_mcp_server.server.get_pod_health_handler",
            new_callable=AsyncMock,
            return_value=_pod_health_output(),
        ):
            result = await get_pod_health("prod-eastus")
        data = json.loads(result)
        assert data["cluster"] == "prod-eastus"

    async def test_all_clusters(self) -> None:
        outputs = [_pod_health_output(f"cluster-{i}") for i in range(6)]
        with patch(
            "platform_mcp_server.server.get_pod_health_all",
            new_callable=AsyncMock,
            return_value=outputs,
        ):
            result = await get_pod_health("all")
        assert "cluster-0" in result

    async def test_error_propagates(self) -> None:
        with (
            patch(
                "platform_mcp_server.server.get_pod_health_handler",
                new_callable=AsyncMock,
                side_effect=RuntimeError("api fail"),
            ),
            pytest.raises(RuntimeError, match="api fail"),
        ):
            await get_pod_health("prod-eastus")


class TestGetKubernetesUpgradeStatus:
    async def test_single_cluster(self) -> None:
        with patch(
            "platform_mcp_server.server.get_upgrade_status_handler",
            new_callable=AsyncMock,
            return_value=_upgrade_status_output(),
        ):
            result = await get_kubernetes_upgrade_status("prod-eastus")
        data = json.loads(result)
        assert data["control_plane_version"] == "1.29.8"

    async def test_all_clusters(self) -> None:
        outputs = [_upgrade_status_output(f"c-{i}") for i in range(6)]
        with patch(
            "platform_mcp_server.server.get_upgrade_status_all",
            new_callable=AsyncMock,
            return_value=outputs,
        ):
            result = await get_kubernetes_upgrade_status("all")
        assert "c-0" in result

    async def test_error_propagates(self) -> None:
        with (
            patch(
                "platform_mcp_server.server.get_upgrade_status_handler",
                new_callable=AsyncMock,
                side_effect=Exception("fail"),
            ),
            pytest.raises(Exception, match="fail"),
        ):
            await get_kubernetes_upgrade_status("prod-eastus")


class TestGetUpgradeProgress:
    async def test_single_cluster(self) -> None:
        with patch(
            "platform_mcp_server.server.get_upgrade_progress_handler",
            new_callable=AsyncMock,
            return_value=_upgrade_progress_output(),
        ):
            result = await get_upgrade_progress("prod-eastus")
        data = json.loads(result)
        assert data["upgrade_in_progress"] is False

    async def test_all_clusters(self) -> None:
        outputs = [_upgrade_progress_output(f"c-{i}") for i in range(6)]
        with patch(
            "platform_mcp_server.server.get_upgrade_progress_all",
            new_callable=AsyncMock,
            return_value=outputs,
        ):
            result = await get_upgrade_progress("all", node_pool="userpool")
        assert "c-0" in result

    async def test_error_propagates(self) -> None:
        with (
            patch(
                "platform_mcp_server.server.get_upgrade_progress_handler",
                new_callable=AsyncMock,
                side_effect=Exception("fail"),
            ),
            pytest.raises(Exception, match="fail"),
        ):
            await get_upgrade_progress("prod-eastus")


class TestGetUpgradeDurationMetrics:
    async def test_single_cluster(self) -> None:
        with patch(
            "platform_mcp_server.server.get_upgrade_metrics_handler",
            new_callable=AsyncMock,
            return_value=_upgrade_metrics_output(),
        ):
            result = await get_upgrade_duration_metrics("prod-eastus", "userpool")
        data = json.loads(result)
        assert data["node_pool"] == "userpool"

    async def test_all_clusters(self) -> None:
        outputs = [_upgrade_metrics_output(f"c-{i}") for i in range(6)]
        with patch(
            "platform_mcp_server.server.get_upgrade_metrics_all",
            new_callable=AsyncMock,
            return_value=outputs,
        ):
            result = await get_upgrade_duration_metrics("all", "userpool", 3)
        assert "c-0" in result

    async def test_error_propagates(self) -> None:
        with (
            patch(
                "platform_mcp_server.server.get_upgrade_metrics_handler",
                new_callable=AsyncMock,
                side_effect=Exception("fail"),
            ),
            pytest.raises(Exception, match="fail"),
        ):
            await get_upgrade_duration_metrics("prod-eastus", "userpool")


class TestCheckPdbUpgradeRisk:
    async def test_single_cluster(self) -> None:
        with patch(
            "platform_mcp_server.server.check_pdb_risk_handler",
            new_callable=AsyncMock,
            return_value=_pdb_check_output(),
        ):
            result = await check_pdb_upgrade_risk("prod-eastus")
        data = json.loads(result)
        assert data["mode"] == "preflight"

    async def test_all_clusters(self) -> None:
        outputs = [_pdb_check_output(f"c-{i}") for i in range(6)]
        with patch(
            "platform_mcp_server.server.check_pdb_risk_all",
            new_callable=AsyncMock,
            return_value=outputs,
        ):
            result = await check_pdb_upgrade_risk("all", node_pool="userpool", mode="live")
        assert "c-0" in result

    async def test_error_propagates(self) -> None:
        with (
            patch(
                "platform_mcp_server.server.check_pdb_risk_handler",
                new_callable=AsyncMock,
                side_effect=Exception("fail"),
            ),
            pytest.raises(Exception, match="fail"),
        ):
            await check_pdb_upgrade_risk("prod-eastus")
