"""Tests for check_node_pool_pressure tool handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from platform_mcp_server.tools.node_pools import check_node_pool_pressure_handler


def _make_node(
    name: str,
    pool: str,
    cpu_alloc: str = "4000m",
    mem_alloc: str = "16Gi",
) -> dict:
    return {
        "name": name,
        "pool": pool,
        "version": "v1.29.8",
        "unschedulable": False,
        "allocatable_cpu": cpu_alloc,
        "allocatable_memory": mem_alloc,
        "conditions": {"Ready": "True"},
        "labels": {"agentpool": pool},
    }


def _make_metric(name: str, cpu: str = "1000m", mem: str = "4Gi") -> dict:
    return {"name": name, "cpu_usage": cpu, "memory_usage": mem}


def _make_pod(name: str, namespace: str = "default", phase: str = "Pending", node: str | None = None) -> dict:
    return {
        "name": name,
        "namespace": namespace,
        "phase": phase,
        "node_name": node,
        "reason": None,
        "message": None,
        "container_statuses": [],
        "conditions": [{"type": "PodScheduled", "status": "False", "reason": "Unschedulable", "message": ""}],
    }


class TestCheckNodePoolPressure:
    async def test_happy_path_single_pool(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [
            _make_node("node-1", "userpool"),
            _make_node("node-2", "userpool"),
        ]
        mock_core.get_pods.return_value = []

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = [
            _make_metric("node-1", cpu="3000m", mem="12Gi"),
            _make_metric("node-2", cpu="2000m", mem="8Gi"),
        ]

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        assert result.cluster == "prod-eastus"
        assert len(result.pools) == 1
        assert result.pools[0].pool_name == "userpool"
        assert result.pools[0].ready_nodes == 2
        assert result.pools[0].pending_pods == 0
        assert result.pools[0].pressure_level == "ok"  # CPU ~62.5%, mem ~62.5% — both below 75%/80%

    async def test_critical_pressure_from_cpu(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", "userpool", cpu_alloc="4000m")]
        mock_core.get_pods.return_value = []

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = [_make_metric("node-1", cpu="3800m", mem="4Gi")]

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        assert result.pools[0].pressure_level == "critical"
        assert result.pools[0].cpu_requests_percent is not None
        assert result.pools[0].cpu_requests_percent >= 90.0

    async def test_warning_from_pending_pods(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", "userpool")]
        mock_core.get_pods.return_value = [
            _make_pod("pod-1", phase="Pending"),
            _make_pod("pod-2", phase="Pending"),
        ]

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = [_make_metric("node-1", cpu="1000m", mem="2Gi")]

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        assert result.pools[0].pressure_level == "warning"
        assert result.pools[0].pending_pods == 2

    async def test_ok_when_all_below_thresholds(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", "userpool")]
        mock_core.get_pods.return_value = []

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = [_make_metric("node-1", cpu="1000m", mem="2Gi")]

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        assert result.pools[0].pressure_level == "ok"

    async def test_multiple_pools_grouped(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [
            _make_node("node-1", "systempool"),
            _make_node("node-2", "userpool"),
            _make_node("node-3", "userpool"),
        ]
        mock_core.get_pods.return_value = []

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = [
            _make_metric("node-1"),
            _make_metric("node-2"),
            _make_metric("node-3"),
        ]

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        pool_names = {p.pool_name for p in result.pools}
        assert pool_names == {"systempool", "userpool"}

    async def test_graceful_degradation_without_metrics(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", "userpool")]
        mock_core.get_pods.return_value = []

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.side_effect = Exception("metrics-server unavailable")

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        assert len(result.pools) == 1
        assert result.pools[0].cpu_requests_percent is None
        assert result.pools[0].memory_requests_percent is None
        assert len(result.errors) == 1
        assert result.errors[0].source == "metrics-server"

    async def test_cluster_all_fan_out(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", "userpool")]
        mock_core.get_pods.return_value = []

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = [_make_metric("node-1")]

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            from platform_mcp_server.tools.node_pools import check_node_pool_pressure_all

            results = await check_node_pool_pressure_all()

        assert len(results) == 6

    async def test_summary_line_present(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", "userpool")]
        mock_core.get_pods.return_value = []

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = [_make_metric("node-1")]

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        assert result.summary
        assert "prod-eastus" in result.summary

    async def test_output_has_timestamp(self) -> None:
        mock_core = AsyncMock()
        mock_core.get_nodes.return_value = [_make_node("node-1", "userpool")]
        mock_core.get_pods.return_value = []

        mock_metrics = AsyncMock()
        mock_metrics.get_node_metrics.return_value = [_make_metric("node-1")]

        with (
            patch("platform_mcp_server.tools.node_pools.K8sCoreClient", return_value=mock_core),
            patch("platform_mcp_server.tools.node_pools.K8sMetricsClient", return_value=mock_metrics),
        ):
            result = await check_node_pool_pressure_handler("prod-eastus")

        assert result.timestamp
